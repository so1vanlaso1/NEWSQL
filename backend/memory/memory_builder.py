"""Build the compact memory window sent to the model + follow-up heuristics.

``build_compact_memory`` renders the design §16 block from the last SQL turn only
(not the whole history). ``looks_like_follow_up`` is a cheap Vietnamese/English cue
heuristic used to decide table pinning; the authoritative intent is the LLM's job.
"""
from __future__ import annotations

from backend.common.vn_text import normalize_vietnamese_text
from backend.memory.models import Turn

# không-dấu cues (matched against the normalized message on token boundaries)
FOLLOW_UP_CUES_VN = [
    "chi", "con", "the con", "trong do", "sap xep", "loc", "rieng", "cua ho", "cua no",
    "cai do", "cai nay", "con lai", "thay vao do", "doi lai", "thang truoc", "thang nay",
    "nam ngoai", "gio", "bay gio", "vay con",
]
FOLLOW_UP_CUES_EN = [
    "only", "now", "what about", "sort by", "filter", "that one", "which one", "their",
    "them", "highest", "lowest", "last month", "instead", "also", "and for",
]
# Third-person back-references. "họ"/"nó" clearly point at a prior referent, so they
# signal a follow-up at any length; "đó/này/ấy/kia" are weaker and only count in a
# short message (they can appear mid-sentence in a fresh question).
_STRONG_REF = {"ho", "no"}
_WEAK_REF = {"do", "nay", "ay", "kia"}


def last_sql_turn(turns: list[Turn]) -> Turn | None:
    for t in reversed(turns or []):
        if t.is_sql_turn():
            return t
    return None


def build_compact_memory(turns: list[Turn]) -> str:
    t = last_sql_turn(turns)
    if t is None:
        return "No previous SQL query."
    lines = ["PREVIOUS QUERY MEMORY:", f"Last user question: {t.user_question}"]
    if t.standalone_question:
        lines.append(f"Standalone question: {t.standalone_question}")
    if t.generated_sql:
        lines.append(f"Previous SQL: {t.generated_sql}")
    if t.selected_tables:
        lines.append(f"Previous tables: {', '.join(t.selected_tables)}")
    if t.selected_metrics:
        lines.append(f"Previous metrics: {', '.join(t.selected_metrics)}")
    if t.selected_filters:
        lines.append(f"Previous filters: {', '.join(t.selected_filters)}")
    if t.result_columns:
        lines.append(f"Previous result columns: {', '.join(t.result_columns)}")
    if t.result_preview:
        lines.append("Previous result preview:")
        for i, row in enumerate(t.result_preview, 1):
            vals = " | ".join(str(v) for v in row.values())
            lines.append(f"{i}. {vals}")
    if t.result_summary:
        lines.append(f"Previous result summary: {t.result_summary}")
    return "\n".join(lines)


def looks_like_follow_up(user_message: object) -> bool:
    norm = normalize_vietnamese_text(user_message)
    if not norm:
        return False
    toks = norm.split()
    tokset = set(toks)
    if tokset & _STRONG_REF:
        return True
    padded = f" {norm} "
    for cue in FOLLOW_UP_CUES_VN + FOLLOW_UP_CUES_EN:
        c = normalize_vietnamese_text(cue)
        if c and f" {c} " in padded:
            return True
    if len(toks) <= 4 and (tokset & _WEAK_REF):
        return True
    return False
