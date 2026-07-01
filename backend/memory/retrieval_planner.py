"""Pre-LLM retrieval plan (design §39): the Phase 4/5 -> Phase 3 seam.

Turns the heuristic intent (``intent_classifier.classify_intent``, design §17-18)
into a concrete retrieval decision (§19-20): whether the current message needs
schema retrieval, what retrieval query to use, and which tables to pin. The plan's
(retrieval_query, pinned_tables) feed straight into ``RetrievalService.retrieve``.
The intent + reason are surfaced for inspection; the authoritative intent is still
the single LLM call's (Phase 7).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from backend.memory.intent_classifier import (
    ASK_ABOUT_PREVIOUS_RESULT,
    ASK_ABOUT_PREVIOUS_SQL,
    DRILL_DOWN_PREVIOUS_RESULT,
    EXPLAIN_PREVIOUS_RESULT,
    INSUFFICIENT_CONTEXT,
    NEW_QUERY,
    REFINE_PREVIOUS_QUERY,
    classify_intent,
)
from backend.memory.memory_builder import last_sql_turn
from backend.memory.models import Turn

# Intents that answer from memory alone -- no schema retrieval this turn.
_NO_RETRIEVAL_INTENTS = {
    ASK_ABOUT_PREVIOUS_SQL, ASK_ABOUT_PREVIOUS_RESULT, EXPLAIN_PREVIOUS_RESULT,
}
_MAX_DRILL_ENTITIES = 3  # top result entities to fold into a drill-down query


class RetrievalPlan(BaseModel):
    needs_retrieval: bool
    retrieval_query: str | None = None
    pinned_tables: list[str] = Field(default_factory=list)
    intent_hint: str = ""
    intent_reason: str = ""


def _drill_down_query(last: Turn, user_message: str) -> str:
    """§20 DRILL_DOWN retrieval query = previous entities + filters + current message."""
    parts: list[str] = []
    for e in (last.result_entities or [])[:_MAX_DRILL_ENTITIES]:
        parts.extend(v for v in (e.id_value, e.name_value) if v)
    parts.extend(last.selected_filters or [])
    parts.append(str(user_message))
    return " ".join(p for p in parts if p).strip()


def _refine_query(last: Turn, last_tables: list[str], user_message: str) -> str:
    """§20 REFINE retrieval query = previous standalone + previous tables + message."""
    anchor = last.standalone_question or last.user_question
    return " ".join(p for p in [anchor, " ".join(last_tables), str(user_message)] if p).strip()


def build_retrieval_plan(user_message: str, turns: list[Turn]) -> RetrievalPlan:
    last = last_sql_turn(turns or [])
    last_tables = list(last.selected_tables) if last else []
    cls = classify_intent(user_message, turns)
    intent, reason = cls.intent, cls.reason

    if intent in _NO_RETRIEVAL_INTENTS:
        return RetrievalPlan(needs_retrieval=False, retrieval_query=None,
                             pinned_tables=last_tables, intent_hint=intent, intent_reason=reason)

    if intent == INSUFFICIENT_CONTEXT:
        return RetrievalPlan(needs_retrieval=False, retrieval_query=None,
                             pinned_tables=[], intent_hint=intent, intent_reason=reason)

    if intent == DRILL_DOWN_PREVIOUS_RESULT and last:
        return RetrievalPlan(needs_retrieval=True,
                             retrieval_query=_drill_down_query(last, user_message),
                             pinned_tables=last_tables, intent_hint=intent, intent_reason=reason)

    if intent == REFINE_PREVIOUS_QUERY and last:
        return RetrievalPlan(needs_retrieval=True,
                             retrieval_query=_refine_query(last, last_tables, user_message),
                             pinned_tables=last_tables, intent_hint=intent, intent_reason=reason)

    # NEW_QUERY (or a follow-up intent with no usable prior turn): retrieve fresh.
    return RetrievalPlan(needs_retrieval=True, retrieval_query=str(user_message),
                         pinned_tables=[], intent_hint=NEW_QUERY, intent_reason=reason)
