"""Phase 7 response parsing: raw model text -> ``LlmDecision`` (never raises).

Local models are not always well-behaved: they wrap JSON in ```` ```json ```` fences,
add trailing prose, or omit fields. ``parse_decision`` is defensive - it extracts the
first balanced JSON object, coerces the intent to one of the 7 canonical constants,
reconciles ``needs_sql`` with ``sql``, cleans the SQL, and always yields a usable
``answer`` so the chat handler can respond even on garbage input.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from backend.memory.intent_classifier import (
    ASK_ABOUT_PREVIOUS_RESULT,
    ASK_ABOUT_PREVIOUS_SQL,
    DRILL_DOWN_PREVIOUS_RESULT,
    EXPLAIN_PREVIOUS_RESULT,
    INSUFFICIENT_CONTEXT,
    NEW_QUERY,
    REFINE_PREVIOUS_QUERY,
)

_INTENTS = {
    NEW_QUERY, REFINE_PREVIOUS_QUERY, ASK_ABOUT_PREVIOUS_SQL, ASK_ABOUT_PREVIOUS_RESULT,
    DRILL_DOWN_PREVIOUS_RESULT, EXPLAIN_PREVIOUS_RESULT, INSUFFICIENT_CONTEXT,
}
_FENCE_RE = re.compile(r"```(?:json|sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_DEFAULT_ANSWER_VN = "Xin lỗi, tôi chưa hiểu được yêu cầu. Bạn thử diễn đạt lại giúp nhé."


class MemoryUpdate(BaseModel):
    selected_tables: list[str] = Field(default_factory=list)
    selected_columns: list[str] = Field(default_factory=list)
    selected_metrics: list[str] = Field(default_factory=list)
    selected_filters: list[str] = Field(default_factory=list)
    referenced_previous_entities: list[str] = Field(default_factory=list)


class LlmDecision(BaseModel):
    intent: str = NEW_QUERY
    needs_sql: bool = False
    standalone_question: Optional[str] = None
    answer: str = ""
    answer_from_memory: Optional[str] = None
    sql: Optional[str] = None
    used_previous_context: bool = False
    memory_update: MemoryUpdate = Field(default_factory=MemoryUpdate)
    parse_ok: bool = True
    parse_error: str = ""


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.search(text or "")
    return m.group(1).strip() if m else (text or "").strip()


def _balanced_object(text: str) -> Optional[str]:
    """Return the first balanced ``{...}`` object, respecting string literals."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    candidate = _strip_fences(text)
    for attempt in (candidate, _balanced_object(candidate) or ""):
        if not attempt:
            continue
        try:
            obj = json.loads(attempt)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def clean_sql(sql: object) -> Optional[str]:
    if not sql or not isinstance(sql, str):
        return None
    s = _strip_fences(sql).strip()
    if not s or s.lower() in {"null", "none"}:
        return None
    if ";" in s:  # keep only the first statement
        s = s[: s.index(";")]
    return s.strip() or None


def _coerce_intent(value: object, needs_sql: bool) -> str:
    v = str(value or "").strip().upper()
    if v in _INTENTS:
        return v
    return NEW_QUERY if needs_sql else INSUFFICIENT_CONTEXT


def parse_decision(text: str) -> LlmDecision:
    data = extract_json_object(text)
    if data is None:
        return LlmDecision(
            intent=INSUFFICIENT_CONTEXT, needs_sql=False, answer=_DEFAULT_ANSWER_VN,
            parse_ok=False, parse_error="no JSON object found in model output",
        )
    # Build defensively field-by-field so one bad field never sinks the whole parse.
    try:
        mu_raw = data.get("memory_update") or {}
        memory_update = MemoryUpdate(**mu_raw) if isinstance(mu_raw, dict) else MemoryUpdate()
    except ValidationError:
        memory_update = MemoryUpdate()

    needs_sql = bool(data.get("needs_sql", False))
    sql = clean_sql(data.get("sql"))
    # Reconcile: SQL present => it's a SQL turn; needs_sql claimed but no SQL => not one.
    if sql:
        needs_sql = True
    elif needs_sql and not sql:
        needs_sql = False

    answer = str(data.get("answer") or "").strip()
    answer_from_memory = data.get("answer_from_memory")
    if isinstance(answer_from_memory, str):
        answer_from_memory = answer_from_memory.strip() or None
    else:
        answer_from_memory = None
    if not answer:
        answer = answer_from_memory or _DEFAULT_ANSWER_VN

    standalone = data.get("standalone_question")
    if isinstance(standalone, str):
        standalone = standalone.strip() or None
    else:
        standalone = None

    return LlmDecision(
        intent=_coerce_intent(data.get("intent"), needs_sql),
        needs_sql=needs_sql,
        standalone_question=standalone,
        answer=answer,
        answer_from_memory=answer_from_memory,
        sql=sql,
        used_previous_context=bool(data.get("used_previous_context", False)),
        memory_update=memory_update,
        parse_ok=True,
    )
