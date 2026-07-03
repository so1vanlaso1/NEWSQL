"""Heuristic 4-mode router (plan §3). No LLM call.

Runs on the normalized (không-dấu) message, same normalization as the intent classifier.
Emits one of NORMAL_SQL / ANALYTIC_MODE / ANALYTIC_FROM_PREVIOUS_RESULT / ANALYTIC_FOLLOWUP.

The heuristics deliberately over-trigger ("phân tích" can prefix a plain lookup); the review
planner's ``mode_downgrade`` (plan §3.4, Phase 13) corrects the rare false positive without an
extra LLM call. A recent review owns referential/follow-up questions; otherwise an analytic
trigger + a previous-result reference routes to the seeded flow, an analytic trigger alone to a
fresh analysis, and everything else stays on the normal SQL pipeline.
"""
from __future__ import annotations

from typing import Optional

from backend.common.vn_text import normalize_vietnamese_text
from backend.memory.memory_builder import last_sql_turn
from backend.memory.models import Turn

# ---- mode constants (plan §3.1) ---------------------------------------------
NORMAL_SQL = "NORMAL_SQL"
ANALYTIC_MODE = "ANALYTIC_MODE"
ANALYTIC_FROM_PREVIOUS_RESULT = "ANALYTIC_FROM_PREVIOUS_RESULT"
ANALYTIC_FOLLOWUP = "ANALYTIC_FOLLOWUP"

# ---- trigger lexicons (không-dấu; matched on token boundaries, plan §3.2) ----
# "This message wants an investigation, not a single lookup."
ANALYTIC_TRIGGERS = [
    "phan tich", "phan tich sau", "danh gia", "nguyen nhan", "vi sao", "tai sao", "ly do",
    "tim nguyen nhan", "hieu suat", "cai thien", "de xuat", "khuyen nghi", "chuyen sau",
    "di sau", "soi", "mo xe", "chan doan", "bao cao phan tich",
    "analyze", "analysis", "analyse", "review", "in depth", "in-depth", "insight", "why",
    "reason", "root cause", "diagnose", "investigate", "what caused", "how to improve",
    "recommendation", "deep dive", "deep-dive", "breakdown", "drivers",
]

# "This message points at something in the previous result."
# NOTE: bare month phrases ("tháng này", "tháng đầu") are deliberately EXCLUDED — they
# normalize to "thang nay"/"thang dau" and collide with the informal "thằng này"/"thằng đầu",
# but the temporal meaning is far more common, so "phân tích doanh thu tháng này" must stay a
# fresh ANALYTIC_MODE analysis, not a previous-result reference. Row references are covered by
# "cai nay", "dong dau", "dong 1", "top 1", "cai top", etc.
PREVIOUS_RESULT_REFERENCES = [
    "cai nay", "cai do", "dong nay", "dong dau", "dong 1", "top 1", "khach hang nay",
    "cong ty nay", "san pham nay", "ket qua tren", "bang nay", "trong do",
    "cai top", "o tren", "ben tren", "vua roi", "vua xong",
    "this", "that one", "first one", "row 1", "top one", "top customer", "highest",
    "lowest", "the top", "above", "previous result", "that customer",
]

# "This message is a follow-up about the last analytic review."
REVIEW_FOLLOWUP_MARKERS = [
    "bang chung", "kiem tra gi", "cau sql nao", "hien sql", "hien cau sql", "cho xem sql",
    "phan tich tiep", "di sau hon", "ve bieu do", "ve lai bieu do", "hien bang",
    "vi sao ban noi", "tai sao ban noi", "bao cao vua roi", "phan tich vua roi",
    "show evidence", "what did you check", "which sql", "show sql", "continue",
    "drill down", "drill-down", "show chart", "show the chart", "show table",
]


def _contains_any(normalized: str, phrases: list[str]) -> Optional[str]:
    """First phrase found on a token boundary in ``normalized`` (already không-dấu), else None."""
    padded = f" {normalized} "
    for p in phrases:
        np = normalize_vietnamese_text(p)
        if np and f" {np} " in padded:
            return p
    return None


def contains_any(normalized: str, phrases: list[str]) -> bool:
    return _contains_any(normalized, phrases) is not None


def detect_mode(user_message: str, turns: list[Turn] | None = None,
                last_review: object | None = None) -> str:
    """Route a turn across the 4 modes (plan §3.3).

    ``turns`` is the recent conversation window; ``last_review`` is the most recent review in
    the conversation (None until review storage ships in Phase 14 — the FOLLOWUP branch stays
    dormant until then, exactly as designed).
    """
    text = normalize_vietnamese_text(user_message)
    analytic = contains_any(text, ANALYTIC_TRIGGERS)
    refs_prev = contains_any(text, PREVIOUS_RESULT_REFERENCES)
    followup = contains_any(text, REVIEW_FOLLOWUP_MARKERS)

    last_sql = last_sql_turn(turns or [])

    # A recent review owns referential/follow-up questions (plan §3.3).
    if last_review is not None and (
        followup or (refs_prev and getattr(last_review, "is_latest_artifact", True))
    ):
        return ANALYTIC_FOLLOWUP

    if analytic and refs_prev and last_sql is not None:
        return ANALYTIC_FROM_PREVIOUS_RESULT

    if analytic:
        return ANALYTIC_MODE

    return NORMAL_SQL
