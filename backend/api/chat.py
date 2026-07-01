"""Phase 7 + 8: the real conversational turn -- ``POST /api/chat``.

Reuses the same pre-LLM pipeline as ``/api/chat/plan`` (plan -> retrieve -> memory
window -> compact skill context), then adds the single LLM call, SELECT-only
validation (with one optional self-repair round-trip), read-only execution,
deterministic result summarization, and memory write-back.

The endpoint always returns HTTP 200 with a populated Vietnamese ``answer`` -- every
failure mode (LLM offline, invalid SQL, execution error) degrades to a friendly message
plus a machine-readable ``error`` field, so the chat UI never has to render a raw 500.
"""
from __future__ import annotations

import re
import time
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend import config
from backend.api.state import get_retrieval_service
from backend.execution.query_runner import run_query
from backend.llm.client import get_client
from backend.llm.prompt_builder import (
    build_repair_user_prompt,
    build_system_prompt,
    build_user_prompt,
)
from backend.llm.response_parser import LlmDecision, parse_decision
from backend.memory.intent_classifier import INSUFFICIENT_CONTEXT, REFINE_PREVIOUS_QUERY
from backend.memory.memory_builder import build_compact_memory
from backend.memory.models import ResultEntity
from backend.memory.result_summarizer import extract_entities, summarize
from backend.memory.retrieval_planner import build_retrieval_plan
from backend.memory.store import ConversationStore
from backend.retrieval.context_builder import RetrievalService
from backend.retrieval.skill_context import build_llm_skill_context
from backend.validation.sql_validator import ValidationResult, validate

router = APIRouter(tags=["chat"])

_conv_store: Optional[ConversationStore] = None

_LLM_UNAVAILABLE_VN = (
    "Xin lỗi, hiện tại tôi không kết nối được tới mô hình ngôn ngữ. "
    "Bạn vui lòng thử lại sau ít phút nhé."
)
_INVALID_SQL_VN = (
    "Xin lỗi, tôi chưa tạo được câu truy vấn hợp lệ cho yêu cầu này. "
    "Bạn thử diễn đạt lại cụ thể hơn giúp nhé."
)
_EXEC_ERROR_VN = "Xin lỗi, câu truy vấn gặp lỗi khi chạy trên dữ liệu."
_MONEY_COL_RE = re.compile(r"(doanh_thu|thanh_tien|tong_tien|gia_ban|don_gia|revenue|amount|_tien|_gia)$", re.I)


def _store() -> ConversationStore:
    global _conv_store
    if _conv_store is None:
        _conv_store = ConversationStore()
    return _conv_store


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str


class ChatResponse(BaseModel):
    conversation_id: str
    turn_id: str = ""
    intent: str = ""
    needs_sql: bool = False
    answer: str = ""
    standalone_question: Optional[str] = None
    sql: Optional[str] = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    result_summary: str = ""
    result_entities: list[ResultEntity] = Field(default_factory=list)
    tables_used: list[str] = Field(default_factory=list)
    metrics_used: list[str] = Field(default_factory=list)
    filters_used: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    used_previous_context: bool = False
    repaired: bool = False
    llm_model: str = ""
    timings_ms: dict = Field(default_factory=dict)
    error: Optional[str] = None


def _vn_highlight(columns: list[str], rows: list[dict]) -> str:
    """A short Vietnamese lead-line for the top row (no LLM)."""
    if not rows:
        return ""
    first = rows[0]
    name_col = next((c for c in columns if c.startswith("ten_")), None)
    metric_col = None
    for c in reversed(columns):
        if c.endswith("_id"):
            continue
        v = first.get(c)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            metric_col = c
            break
    if name_col and metric_col:
        val = first.get(metric_col)
        unit = " ₫" if _MONEY_COL_RE.search(metric_col) else ""
        try:
            val = f"{float(val):,.0f}".replace(",", ".")
        except (TypeError, ValueError):
            unit = ""
        return f"Dẫn đầu: {first.get(name_col)} ({metric_col} = {val}{unit})."
    if name_col:
        return f"Dẫn đầu: {first.get(name_col)}."
    return ""


def _compose_answer(preamble: str, row_count: int, truncated: bool,
                    columns: list[str], rows: list[dict]) -> str:
    base = (preamble or "").strip()
    if row_count == 0:
        note = (
            "Hiện chưa có dữ liệu phù hợp cho yêu cầu này. "
            f"Dữ liệu hiện có từ {config.DATA_MIN_DATE} đến {config.DATA_MAX_DATE}."
        )
        return (base + " " + note).strip() if base else note
    tail = f"Có {row_count} kết quả."
    hi = _vn_highlight(columns, rows)
    if hi:
        tail += " " + hi
    if truncated:
        tail += f" (hiển thị {config.MAX_RESULT_ROWS} dòng đầu)"
    return (base + " " + tail).strip() if base else tail


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, rsvc: RetrievalService = Depends(get_retrieval_service)):
    t0 = time.time()
    timings: dict = {}
    store = _store()
    conversation_id = req.conversation_id or store.create()
    turns = store.load_recent(conversation_id)

    # ---- steps 1-6: same pre-LLM pipeline as /chat/plan --------------------
    t_r = time.time()
    plan = build_retrieval_plan(req.message, turns)
    resolved = None
    if plan.needs_retrieval and plan.retrieval_query:
        resolved = rsvc.retrieve(plan.retrieval_query, plan.pinned_tables)
    timings["retrieval"] = int((time.time() - t_r) * 1000)

    memory_window = build_compact_memory(turns)
    standalone = (
        plan.retrieval_query
        if plan.intent_hint == REFINE_PREVIOUS_QUERY
        and plan.retrieval_query and plan.retrieval_query != req.message
        else None
    )
    skill_ctx = build_llm_skill_context(
        user_message=req.message,
        memory_window=memory_window,
        resolved=resolved,
        standalone_question=standalone,
        rules=rsvc.global_rules,
    )

    # ---- step 7: the single LLM call --------------------------------------
    system = build_system_prompt()
    user = build_user_prompt(
        skill_ctx, today=date.today().isoformat(),
        data_min=config.DATA_MIN_DATE, data_max=config.DATA_MAX_DATE,
    )
    t_l = time.time()
    res = get_client().chat(system, user)
    timings["llm"] = int((time.time() - t_l) * 1000)

    if res.error:
        saved = store.save_non_sql_turn(
            conversation_id, req.message, intent=INSUFFICIENT_CONTEXT,
            answer_from_memory=_LLM_UNAVAILABLE_VN,
        )
        timings["total"] = int((time.time() - t0) * 1000)
        return ChatResponse(
            conversation_id=conversation_id, turn_id=saved.turn_id,
            intent=INSUFFICIENT_CONTEXT, answer=_LLM_UNAVAILABLE_VN,
            llm_model=res.model, timings_ms=timings, error=res.error,
        )

    decision = parse_decision(res.content)
    resolved_table_set = set(resolved.final_tables) if resolved else None
    mu = decision.memory_update

    # ---- branch A: answer from memory (no SQL) ----------------------------
    if not decision.needs_sql:
        answer = decision.answer or decision.answer_from_memory or _INVALID_SQL_VN
        saved = store.save_non_sql_turn(
            conversation_id, req.message, standalone_question=decision.standalone_question or "",
            intent=decision.intent, answer_from_memory=answer,
        )
        timings["total"] = int((time.time() - t0) * 1000)
        return ChatResponse(
            conversation_id=conversation_id, turn_id=saved.turn_id, intent=decision.intent,
            needs_sql=False, answer=answer, standalone_question=decision.standalone_question,
            used_previous_context=decision.used_previous_context, llm_model=res.model,
            timings_ms=timings,
        )

    # ---- branch B: SQL turn -----------------------------------------------
    t_v = time.time()
    vr: ValidationResult = validate(decision.sql, resolved_tables=resolved_table_set)
    repaired = False
    if not vr.ok and config.LLM_SELF_REPAIR and decision.sql:
        repair_user = build_repair_user_prompt(user, decision.sql, "; ".join(vr.errors))
        t_l2 = time.time()
        res2 = get_client().chat(system, repair_user)
        timings["llm"] += int((time.time() - t_l2) * 1000)
        if not res2.error:
            decision2 = parse_decision(res2.content)
            if decision2.sql:
                vr2 = validate(decision2.sql, resolved_tables=resolved_table_set)
                repaired = True
                if vr2.ok:
                    decision, vr = decision2, vr2
                    mu = decision.memory_update
    timings["validate"] = int((time.time() - t_v) * 1000)

    if not vr.ok:
        saved = store.save_non_sql_turn(
            conversation_id, req.message, standalone_question=decision.standalone_question or "",
            intent=decision.intent, answer_from_memory=_INVALID_SQL_VN,
        )
        timings["total"] = int((time.time() - t0) * 1000)
        return ChatResponse(
            conversation_id=conversation_id, turn_id=saved.turn_id, intent=decision.intent,
            needs_sql=True, answer=_INVALID_SQL_VN, standalone_question=decision.standalone_question,
            sql=decision.sql, validation_errors=vr.errors, validation_warnings=vr.warnings,
            repaired=repaired, llm_model=res.model, timings_ms=timings, error="validation failed",
        )

    # ---- execute ----------------------------------------------------------
    t_e = time.time()
    qr = run_query(vr.normalized_sql)
    timings["execute"] = int((time.time() - t_e) * 1000)

    if qr.error:
        saved = store.save_non_sql_turn(
            conversation_id, req.message, standalone_question=decision.standalone_question or "",
            intent=decision.intent, answer_from_memory=_EXEC_ERROR_VN,
        )
        timings["total"] = int((time.time() - t0) * 1000)
        return ChatResponse(
            conversation_id=conversation_id, turn_id=saved.turn_id, intent=decision.intent,
            needs_sql=True, answer=_EXEC_ERROR_VN,
            standalone_question=decision.standalone_question, sql=vr.normalized_sql,
            validation_warnings=vr.warnings, repaired=repaired, llm_model=res.model,
            timings_ms=timings, error=qr.error,
        )

    # ---- summarize + persist ----------------------------------------------
    summary = summarize(qr.columns, qr.rows)
    entities = extract_entities(qr.columns, qr.rows)
    answer = _compose_answer(decision.answer, qr.row_count, qr.truncated, qr.columns, qr.rows)
    tables_used = mu.selected_tables or vr.referenced_tables

    saved = store.save_sql_turn(
        conversation_id, req.message,
        standalone_question=decision.standalone_question or "",
        intent=decision.intent,
        selected_tables=tables_used,
        selected_columns=mu.selected_columns,
        selected_metrics=mu.selected_metrics,
        selected_filters=mu.selected_filters,
        generated_sql=vr.normalized_sql,
        result_columns=qr.columns,
        result_preview=qr.rows[: config.RESULT_PREVIEW_ROWS],
        result_entities=entities,
        result_summary=summary,
    )
    timings["total"] = int((time.time() - t0) * 1000)
    return ChatResponse(
        conversation_id=conversation_id, turn_id=saved.turn_id, intent=decision.intent,
        needs_sql=True, answer=answer, standalone_question=decision.standalone_question,
        sql=vr.normalized_sql, columns=qr.columns, rows=qr.rows, row_count=qr.row_count,
        truncated=qr.truncated, result_summary=summary, result_entities=entities,
        tables_used=tables_used, metrics_used=mu.selected_metrics, filters_used=mu.selected_filters,
        validation_warnings=vr.warnings, used_previous_context=decision.used_previous_context,
        repaired=repaired, llm_model=res.model, timings_ms=timings,
    )
