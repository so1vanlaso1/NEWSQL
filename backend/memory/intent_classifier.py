"""Heuristic (non-LLM) conversational intent classifier (design §17-18).

Classifies each user turn into one of the seven plan intents from conversation
memory + cheap Vietnamese/English cues. This is *pre-LLM*: the single LLM call
(Phase 7) remains the authoritative classifier, but a full heuristic here lets the
retrieval planner (Phase 5) pick a retrieval mode for every intent -- including the
drill-down / explain / insufficient-context cases the earlier 4-way hint ignored.

The classifier is intentionally imperfect and conservative; ``reason`` records the
cue that fired so the decision is inspectable in the Chat Plan tester + smoke tests.
"""
from __future__ import annotations

from pydantic import BaseModel

from backend.common.vn_text import normalize_vietnamese_text
from backend.memory.memory_builder import last_sql_turn, looks_like_follow_up
from backend.memory.models import Turn

# ---- Intent constants (design §17) ------------------------------------------
NEW_QUERY = "NEW_QUERY"
REFINE_PREVIOUS_QUERY = "REFINE_PREVIOUS_QUERY"
ASK_ABOUT_PREVIOUS_SQL = "ASK_ABOUT_PREVIOUS_SQL"
ASK_ABOUT_PREVIOUS_RESULT = "ASK_ABOUT_PREVIOUS_RESULT"
DRILL_DOWN_PREVIOUS_RESULT = "DRILL_DOWN_PREVIOUS_RESULT"
EXPLAIN_PREVIOUS_RESULT = "EXPLAIN_PREVIOUS_RESULT"
INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"

# ---- Cue lists (matched không-dấu, on token boundaries) ---------------------
# Questions ABOUT the previous SQL (no new retrieval, answer from memory).
ASK_SQL_CUES = [
    "cau sql", "cau lenh", "truy van gi", "dung bang nao", "bang nao", "query gi",
    "cau query", "show me the sql", "what did you query", "which table", "which tables",
    "the sql", "cau truy van",
]
# Questions ABOUT the previous result (no new retrieval, answer from preview).
ASK_RESULT_CUES = [
    "cai nao cao nhat", "cai nao thap nhat", "cai nao", "dong nao", "bao nhieu dong",
    "bao nhieu ket qua", "bao nhieu ban ghi", "dong dau tien", "which one", "how many rows",
    "the first row", "top row", "ket qua tren", "cao nhat", "thap nhat", "top 1",
]
# Interpretation / "why" questions -- answer from result memory (§18.6).
EXPLAIN_CUES = [
    "tai sao", "vi sao", "ly do", "giai thich", "nghia la gi", "y nghia", "co nghia gi",
    "why", "explain", "what does this mean", "reason", "giai thich ket qua",
]
# New-detail nouns that, tied to a prior entity, signal a drill-down (§18.5).
DRILL_DOWN_CUES = [
    "san pham", "products", "product", "don hang", "orders", "order", "chi tiet",
    "details", "mua gi", "da mua", "bought", "buy", "tuyen", "routes", "route",
    "mat hang", "hoa don",
]

# Back-references pointing at a prior turn/entity.
_STRONG_REF = {"ho", "no", "they", "their", "them"}
_WEAK_REF = {"do", "nay", "ay", "kia", "that", "this"}
_REF_PHRASES = ("cai do", "cai nay", "that one", "con lai", "thay vao do", "the top")


class IntentClassification(BaseModel):
    intent: str
    reason: str = ""  # which cue/branch fired (debug + UI)


def _contains_any(normalized: str, cues: list[str]) -> str | None:
    """Return the first cue found on a token boundary, else None."""
    padded = f" {normalized} "
    for c in cues:
        if f" {normalize_vietnamese_text(c)} " in padded:
            return c
    return None


def _has_strong_ref(tokset: set[str], padded: str) -> bool:
    if tokset & _STRONG_REF:
        return True
    return any(f" {p} " in padded for p in _REF_PHRASES)


def _looks_like_bare_reference(tokset: set[str], padded: str, n_tokens: int) -> bool:
    """A short message that references a prior thing without naming its own subject
    (e.g. "cái đó thì sao?", "what about that one?"). Only meaningful when there is
    no prior turn to resolve the reference against."""
    if n_tokens > 6:
        return False
    if tokset & (_STRONG_REF | _WEAK_REF):
        return True
    return any(f" {p} " in padded for p in _REF_PHRASES)


def classify_intent(user_message: str, turns: list[Turn]) -> IntentClassification:
    norm = normalize_vietnamese_text(user_message)
    last = last_sql_turn(turns or [])
    toks = norm.split()
    tokset = set(toks)
    padded = f" {norm} "

    # No prior SQL turn: either a fresh question, or a dangling back-reference.
    if last is None:
        if _looks_like_bare_reference(tokset, padded, len(toks)):
            return IntentClassification(
                intent=INSUFFICIENT_CONTEXT,
                reason="back-reference with no previous query")
        return IntentClassification(intent=NEW_QUERY, reason="no previous query")

    # A prior SQL turn exists -- most-specific cue wins.
    hit = _contains_any(norm, ASK_SQL_CUES)
    if hit:
        return IntentClassification(intent=ASK_ABOUT_PREVIOUS_SQL, reason=f"ask-sql cue: {hit}")

    hit = _contains_any(norm, EXPLAIN_CUES)  # before ASK_RESULT: "tại sao ... cao nhất"
    if hit:
        return IntentClassification(intent=EXPLAIN_PREVIOUS_RESULT, reason=f"explain cue: {hit}")

    hit = _contains_any(norm, ASK_RESULT_CUES)
    if hit:
        return IntentClassification(intent=ASK_ABOUT_PREVIOUS_RESULT, reason=f"ask-result cue: {hit}")

    drill = _contains_any(norm, DRILL_DOWN_CUES)
    if drill and _has_strong_ref(tokset, padded):
        return IntentClassification(
            intent=DRILL_DOWN_PREVIOUS_RESULT, reason=f"drill-down cue: {drill} + back-reference")

    if looks_like_follow_up(user_message):
        return IntentClassification(intent=REFINE_PREVIOUS_QUERY, reason="follow-up cue")

    return IntentClassification(intent=NEW_QUERY, reason="new question despite history")
