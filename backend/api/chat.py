"""Phase 7 + 8: the real conversational turn -- ``POST /api/chat`` (+ streaming).

Reuses the same pre-LLM pipeline as ``/api/chat/plan`` (plan -> retrieve -> memory
window -> compact skill context), then adds the single LLM call, SELECT-only
validation (with one optional self-repair round-trip), read-only execution,
deterministic result summarization, and memory write-back.

The whole turn is expressed once as a generator (``_run_turn``) that yields progress
events -- one per pipeline phase plus live model-token deltas. Two endpoints consume it:

- ``POST /api/chat``         -> drains the generator, returns only the final ChatResponse.
- ``POST /api/chat/stream``  -> forwards every event as Server-Sent Events so the UI can
                                show which step the answer is at and stream the model.

Every failure mode (LLM offline, invalid SQL, execution error) degrades to a friendly
Vietnamese ``answer`` plus a machine-readable ``error`` field, and the exact model input
(skill context + system/user prompt) and raw output are persisted on the turn so any past
session's model input can be inspected later.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Iterator, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend import config
from backend.analysis import (
    controller,
    followup,
    geo_controller,
    mode_detector,
    review_target_resolver,
)
from backend.analysis.review_store import get_review_store
from backend.api.state import get_retrieval_service
from backend.common.logging import get_logger
from backend.execution.query_runner import run_query
from backend.llm.client import LlmResult, get_client
from backend.llm.prompt_builder import (
    build_repair_user_prompt,
    build_system_prompt,
    build_user_prompt,
)
from backend.llm.response_parser import parse_decision
from backend.memory.intent_classifier import INSUFFICIENT_CONTEXT, REFINE_PREVIOUS_QUERY
from backend.memory.memory_builder import build_compact_memory
from backend.memory.models import ResultEntity
from backend.memory.result_summarizer import extract_entities, summarize
from backend.memory.retrieval_planner import build_retrieval_plan
from backend.memory.store import get_conversation_store
from backend.retrieval.context_builder import RetrievalService
from backend.retrieval.skill_context import build_llm_skill_context
from backend.validation.sql_validator import ValidationResult, validate

router = APIRouter(tags=["chat"])
log = get_logger(__name__)

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
    # The exact model input (the same compact context the Chat Plan tab shows) + raw output,
    # logged per turn so every chat session's model input can be inspected.
    llm_skill_context: str = ""
    llm_system_prompt: str = ""
    llm_user_prompt: str = ""
    llm_raw_response: str = ""
    timings_ms: dict = Field(default_factory=dict)
    error: Optional[str] = None
    # ---- Phase 13/14 analytic turn (empty on a normal turn) ----
    mode: str = ""
    review_id: str = ""
    report_markdown: str = ""
    evidence: list[dict] = Field(default_factory=list)
    charts: list[dict] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    follow_up_suggestions: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    analytic_status: str = ""


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


# ---- event helpers ----------------------------------------------------------
def _step(step: str, status: str, **extra) -> dict:
    return {"type": "step", "step": step, "status": status, **extra}


def _final(resp: ChatResponse) -> dict:
    return {"type": "final", "response": resp.model_dump()}


def _run_turn(req: ChatRequest, rsvc: RetrievalService) -> Iterator[dict]:
    """Drive one conversational turn, yielding progress events then a final response."""
    t0 = time.time()
    timings: dict = {}
    rsvc.ensure_fresh()  # Phase 10: apply any KB edits before this turn (rules + retrieval)
    store = get_conversation_store()
    review_store = get_review_store()
    conversation_id = req.conversation_id or store.create()
    turns = store.load_recent(conversation_id)
    last_review = review_store.last_review(conversation_id) if config.ANALYTIC_ENABLED else None

    # ---- step 0: mode detection (Phase 12) --------------------------------
    # The 4-mode router runs every turn. The analytic controller ships in Phase 13; until
    # then (and whenever ANALYTIC_ENABLED=0) every mode falls through to the normal SQL
    # pipeline below, so normal chat behavior is unchanged. The "mode" SSE step is emitted
    # only when the flag is on, keeping the disabled-mode stream byte-identical.
    mode = mode_detector.detect_mode(req.message, turns, last_review=last_review)
    log.info("mode=%s analytic_enabled=%s", mode, config.ANALYTIC_ENABLED)
    if config.ANALYTIC_ENABLED:
        yield _step("mode", "done", mode=mode)

    # ---- geo prospecting routing (Phase 19) -------------------------------
    # A GEO_PROSPECT turn runs its own deterministic-core controller (resolve location →
    # Google Places → dedupe vs customers → LLM-narrated prospect report). It owns the whole
    # turn (no downgrade); an unresolved location yields a friendly guidance answer.
    if config.GEO_ENABLED and mode == mode_detector.GEO_PROSPECT:
        for ev in geo_controller.run_geo_prospect(
                message=req.message, conversation_id=conversation_id, turns=turns,
                store=store, review_store=review_store, client=get_client(), t0=None):
            yield ev
        return

    # ---- analytic routing (Phase 13/14) -----------------------------------
    # An analytic turn runs the review controller (plan §5). The planner may signal a
    # mode_downgrade, in which case we fall through to the normal SQL pipeline below.
    if config.ANALYTIC_ENABLED and mode == mode_detector.ANALYTIC_FOLLOWUP and last_review is not None:
        for ev in followup.handle_followup(
                message=req.message, conversation_id=conversation_id,
                review=last_review, store=store, client=get_client()):
            yield ev
        return

    if config.ANALYTIC_ENABLED and mode in controller.ANALYTIC_MODES:
        seed = None
        if mode == mode_detector.ANALYTIC_FROM_PREVIOUS_RESULT:
            seed = review_target_resolver.resolve(req.message, turns)
            if not seed.ok:
                resp = ChatResponse(
                    conversation_id=conversation_id, intent=mode, mode=mode,
                    answer=seed.reason, analytic_status="failed", error="unresolved_reference")
                saved = store.save_non_sql_turn(
                    conversation_id, req.message, intent=mode, answer=seed.reason,
                    error="unresolved_reference")
                resp.turn_id = saved.turn_id
                timings["total"] = int((time.time() - t0) * 1000)
                resp.timings_ms = timings
                yield _final(resp)
                return
        downgraded = False
        for ev in controller.run_review(
                message=req.message, conversation_id=conversation_id, turns=turns,
                rsvc=rsvc, mode=mode, seed=seed, store=store,
                review_store=review_store, client=get_client(), t0=None):
            if ev.get("type") == "downgrade":
                downgraded = True
                break
            yield ev
        if not downgraded:
            return
        # else: mode_downgrade -> continue into the normal SQL pipeline below.

    # ---- step 1: plan (intent + retrieval decision) -----------------------
    yield _step("plan", "start")
    plan = build_retrieval_plan(req.message, turns)
    yield _step("plan", "done", intent=plan.intent_hint,
                needs_retrieval=plan.needs_retrieval, reason=plan.intent_reason or "")

    # ---- step 2: retrieval ------------------------------------------------
    t_r = time.time()
    resolved = None
    if plan.needs_retrieval and plan.retrieval_query:
        yield _step("retrieve", "start", query=plan.retrieval_query)
        resolved = rsvc.retrieve(plan.retrieval_query, plan.pinned_tables)
        yield _step("retrieve", "done", tables=list(resolved.final_tables))
    else:
        yield _step("retrieve", "done", skipped=True)
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
    system = build_system_prompt()
    user = build_user_prompt(
        skill_ctx, today=date.today().isoformat(),
        data_min=config.DATA_MIN_DATE, data_max=config.DATA_MAX_DATE,
    )
    # These are what get logged/persisted; the repair branch overrides them below.
    eff_user, eff_raw = user, ""

    # ---- step 3: the single (streamed) LLM call ---------------------------
    yield _step("llm", "start")
    t_l = time.time()
    res: Optional[LlmResult] = None
    parts: list[str] = []
    for kind, payload in get_client().stream_chat(system, user):
        if kind == "delta":
            parts.append(str(payload))
            yield {"type": "token", "delta": str(payload)}
        else:
            res = payload if isinstance(payload, LlmResult) else res
    if res is None:
        res = LlmResult(content="".join(parts))
    if not res.content and parts:
        res.content = "".join(parts)
    eff_raw = res.content
    timings["llm"] = int((time.time() - t_l) * 1000)
    yield _step("llm", "done", ms=timings["llm"], model=res.model, error=res.error or "")

    def _persist_and_finish(resp: ChatResponse, *, sql_turn: bool,
                            decision=None, vr: Optional[ValidationResult] = None,
                            qr=None, entities=None, summary: str = "") -> Iterator[dict]:
        """Save the turn (with the model-input log) and yield the final event."""
        common = dict(
            llm_model=res.model, llm_skill_context=skill_ctx,
            llm_system_prompt=system, llm_user_prompt=eff_user, llm_raw_response=eff_raw,
        )
        if sql_turn and decision is not None and vr is not None and qr is not None:
            saved = store.save_sql_turn(
                conversation_id, req.message,
                standalone_question=decision.standalone_question or "",
                intent=decision.intent,
                selected_tables=resp.tables_used,
                selected_columns=(decision.memory_update.selected_columns
                                  if decision.memory_update else []),
                selected_metrics=resp.metrics_used, selected_filters=resp.filters_used,
                generated_sql=vr.normalized_sql,
                result_columns=qr.columns,
                result_preview=qr.rows[: config.RESULT_PREVIEW_ROWS],
                result_entities=entities or [], result_summary=summary,
                answer=resp.answer, display_rows=qr.rows[: config.HISTORY_DISPLAY_ROWS],
                row_count=qr.row_count, truncated=qr.truncated, error=resp.error or "",
                **common)
        else:
            saved = store.save_non_sql_turn(
                conversation_id, req.message,
                standalone_question=(decision.standalone_question if decision else "") or "",
                intent=resp.intent, answer_from_memory=resp.answer, answer=resp.answer,
                error=resp.error or "", **common)
        resp.turn_id = saved.turn_id
        resp.timings_ms = timings
        resp.llm_skill_context = skill_ctx
        resp.llm_system_prompt = system
        resp.llm_user_prompt = eff_user
        resp.llm_raw_response = eff_raw
        timings["total"] = int((time.time() - t0) * 1000)
        yield _final(resp)

    # ---- LLM offline / errored -------------------------------------------
    if res.error:
        resp = ChatResponse(
            conversation_id=conversation_id, intent=INSUFFICIENT_CONTEXT,
            answer=_LLM_UNAVAILABLE_VN, llm_model=res.model, error=res.error)
        yield from _persist_and_finish(resp, sql_turn=False)
        return

    decision = parse_decision(res.content)
    resolved_table_set = set(resolved.final_tables) if resolved else None
    mu = decision.memory_update

    # ---- branch A: answer from memory (no SQL) ----------------------------
    if not decision.needs_sql:
        answer = decision.answer or decision.answer_from_memory or _INVALID_SQL_VN
        resp = ChatResponse(
            conversation_id=conversation_id, intent=decision.intent, needs_sql=False,
            answer=answer, standalone_question=decision.standalone_question,
            used_previous_context=decision.used_previous_context, llm_model=res.model)
        yield from _persist_and_finish(resp, sql_turn=False, decision=decision)
        return

    # ---- branch B: SQL turn -----------------------------------------------
    yield _step("validate", "start")
    t_v = time.time()
    vr: ValidationResult = validate(decision.sql, resolved_tables=resolved_table_set)
    repaired = False
    if not vr.ok and config.LLM_SELF_REPAIR and decision.sql:
        yield _step("repair", "start", errors=list(vr.errors))
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
                    eff_user, eff_raw = repair_user, res2.content
        yield _step("repair", "done", ok=vr.ok)
    timings["validate"] = int((time.time() - t_v) * 1000)
    yield _step("validate", "done", ok=vr.ok, repaired=repaired,
                errors=list(vr.errors), warnings=list(vr.warnings))

    if not vr.ok:
        resp = ChatResponse(
            conversation_id=conversation_id, intent=decision.intent, needs_sql=True,
            answer=_INVALID_SQL_VN, standalone_question=decision.standalone_question,
            sql=decision.sql, validation_errors=vr.errors, validation_warnings=vr.warnings,
            repaired=repaired, llm_model=res.model, error="validation failed")
        yield from _persist_and_finish(resp, sql_turn=False, decision=decision)
        return

    # ---- execute ----------------------------------------------------------
    yield _step("execute", "start")
    t_e = time.time()
    qr = run_query(vr.normalized_sql)
    timings["execute"] = int((time.time() - t_e) * 1000)

    if qr.error:
        yield _step("execute", "done", ok=False, error=qr.error)
        resp = ChatResponse(
            conversation_id=conversation_id, intent=decision.intent, needs_sql=True,
            answer=_EXEC_ERROR_VN, standalone_question=decision.standalone_question,
            sql=vr.normalized_sql, validation_warnings=vr.warnings, repaired=repaired,
            llm_model=res.model, error=qr.error)
        yield from _persist_and_finish(resp, sql_turn=False, decision=decision)
        return
    yield _step("execute", "done", ok=True, row_count=qr.row_count, truncated=qr.truncated)

    # ---- summarize + persist ----------------------------------------------
    yield _step("summarize", "start")
    summary = summarize(qr.columns, qr.rows)
    entities = extract_entities(qr.columns, qr.rows)
    answer = _compose_answer(decision.answer, qr.row_count, qr.truncated, qr.columns, qr.rows)
    tables_used = mu.selected_tables or vr.referenced_tables
    yield _step("summarize", "done")

    resp = ChatResponse(
        conversation_id=conversation_id, intent=decision.intent, needs_sql=True, answer=answer,
        standalone_question=decision.standalone_question, sql=vr.normalized_sql,
        columns=qr.columns, rows=qr.rows, row_count=qr.row_count, truncated=qr.truncated,
        result_summary=summary, result_entities=entities, tables_used=tables_used,
        metrics_used=mu.selected_metrics, filters_used=mu.selected_filters,
        validation_warnings=vr.warnings, used_previous_context=decision.used_previous_context,
        repaired=repaired, llm_model=res.model)
    yield from _persist_and_finish(resp, sql_turn=True, decision=decision, vr=vr, qr=qr,
                                   entities=entities, summary=summary)


# ---- endpoints --------------------------------------------------------------
@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, rsvc: RetrievalService = Depends(get_retrieval_service)):
    final: Optional[dict] = None
    for ev in _run_turn(req, rsvc):
        if ev.get("type") == "final":
            final = ev["response"]
    return ChatResponse(**final) if final else ChatResponse(conversation_id="")


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for ev in events:
        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
def chat_stream(req: ChatRequest, rsvc: RetrievalService = Depends(get_retrieval_service)):
    """Same turn as ``/chat`` but streamed as Server-Sent Events (step + token + final)."""
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # disable proxy buffering so events arrive live
    }
    return StreamingResponse(
        _sse(_run_turn(req, rsvc)), media_type="text/event-stream", headers=headers)
