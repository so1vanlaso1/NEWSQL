"""Evidence-only analytic follow-up answerer (Phase 15, plan §9)."""
from __future__ import annotations

import re
import uuid
from typing import Iterator, Optional

from backend.analysis import evidence as evidence_mod
from backend.analysis.models import ChartSpec, EvidenceItem, ReviewRecord
from backend.analysis.mode_detector import ANALYTIC_FOLLOWUP
from backend.common.logging import get_logger
from backend.common.vn_text import normalize_vietnamese_text
from backend.llm import review_prompts
from backend.llm.client import LlmClient
from backend.llm.response_parser import extract_json_object

log = get_logger(__name__)

SQL_TERMS = ("sql", "cau sql", "cho xem sql", "hien sql", "query", "truy van")
EVIDENCE_TERMS = ("bang chung", "evidence", "hien bang", "show table", "du lieu", "kiem tra gi")
CHART_TERMS = ("bieu do", "chart", "ve bieu do", "show chart")
NEW_ANALYSIS_TERMS = (
    "chay lai", "truy van moi", "phan tich moi", "loc them", "them dieu kien",
    "so sanh voi", "theo ngay", "theo tuan", "theo quy", "nam 2024", "nam 2026",
)
STOPWORDS = {
    "cho", "xem", "hay", "voi", "cua", "cac", "nhung", "nay", "do", "tai", "sao",
    "vi", "noi", "the", "nao", "bang", "chung", "phan", "tich", "show", "what",
    "why", "the", "and", "from", "used",
}


def _step(step: str, status: str, **extra) -> dict:
    return {"type": "step", "step": step, "status": status, **extra}


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    padded = f" {text} "
    return any(f" {normalize_vietnamese_text(t)} " in padded for t in terms)


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9_]+", normalize_vietnamese_text(text or ""))
        if len(t) >= 3 and t not in STOPWORDS
    }


def _evidence_text(ev: EvidenceItem) -> str:
    row_bits: list[str] = []
    for row in (ev.rows or [])[:5]:
        row_bits.extend(str(v) for v in row.values() if v is not None)
    return " ".join([
        ev.title or "",
        ev.purpose or "",
        ev.metric or "",
        evidence_mod.profile_sentence(ev),
        ev.sql or "",
        " ".join(row_bits),
    ])


def _match_evidence(question: str, evidence: list[EvidenceItem], limit: int = 3) -> list[EvidenceItem]:
    qtok = _tokens(question)
    if not qtok:
        return [ev for ev in evidence if ev.status == "success"][:limit]
    scored: list[tuple[int, EvidenceItem]] = []
    for ev in evidence:
        etok = _tokens(_evidence_text(ev))
        score = len(qtok & etok)
        if score:
            scored.append((score, ev))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [ev for _, ev in scored[:limit]]


def _charts_for_evidence(charts: list[ChartSpec], evidence: list[EvidenceItem]) -> list[ChartSpec]:
    ids = {ev.evidence_id for ev in evidence}
    return [c for c in charts if c.evidence_id in ids] if ids else list(charts)


def _render_sql(review: ReviewRecord, evidence: list[EvidenceItem]) -> str:
    items = [ev for ev in evidence if ev.sql] or [ev for ev in review.evidence if ev.sql]
    lines = ["## SQL đã dùng"]
    if not items:
        lines.append("- Báo cáo này không có SQL được lưu cho phần phù hợp.")
    for i, ev in enumerate(items, 1):
        lines += ["", f"### {i}. {ev.title}", "```sql", ev.sql.strip(), "```"]
    return "\n".join(lines)


def _render_evidence(evidence: list[EvidenceItem]) -> str:
    lines = ["## Bằng chứng liên quan"]
    if not evidence:
        lines.append("- Không tìm thấy bảng bằng chứng khớp trực tiếp với câu hỏi.")
    for ev in evidence:
        lines.append(f"- {ev.evidence_id}: {evidence_mod.profile_sentence(ev)}")
    return "\n".join(lines)


def _render_charts(charts: list[ChartSpec]) -> str:
    lines = ["## Biểu đồ đã lưu"]
    if not charts:
        lines.append("- Báo cáo này chưa có biểu đồ phù hợp.")
    for c in charts:
        lines.append(f"- {c.chart_id}: {c.title} ({c.type}).")
    return "\n".join(lines)


def _fallback_answer(question: str, review: ReviewRecord) -> tuple[str, list[EvidenceItem], list[ChartSpec], list[str], bool]:
    text = normalize_vietnamese_text(question)
    matches = _match_evidence(question, review.evidence)
    charts = _charts_for_evidence(review.charts, matches)

    if _contains(text, SQL_TERMS):
        if not matches:
            matches = [ev for ev in review.evidence if ev.sql]
            charts = _charts_for_evidence(review.charts, matches)
        return _render_sql(review, matches), matches, charts, ["Phân tích sâu hơn phần này"], False
    if _contains(text, CHART_TERMS):
        return _render_charts(charts or review.charts), matches, charts or review.charts, ["Cho xem bảng chứng cứ"], False
    if _contains(text, EVIDENCE_TERMS):
        return _render_evidence(matches), matches, charts, ["Cho xem SQL đã dùng"], False
    if any(term in text for term in (normalize_vietnamese_text(t) for t in NEW_ANALYSIS_TERMS)) and not matches:
        return (
            "Câu hỏi này có vẻ cần chạy một phân tích mới ngoài bằng chứng đã lưu. "
            "Bạn hãy gửi nó như một yêu cầu phân tích mới để hệ thống lập task SQL an toàn.",
            [],
            [],
            ["Chạy phân tích mới cho câu hỏi này"],
            True,
        )

    if matches:
        answer = " ".join(evidence_mod.profile_sentence(ev) for ev in matches[:2])
        return answer, matches, charts, ["Cho xem SQL đã dùng", "Phân tích sâu hơn phần này"], False
    return (
        "Tôi chưa tìm thấy bằng chứng đã lưu khớp trực tiếp với câu hỏi này. "
        "Nếu muốn, hãy đặt một yêu cầu phân tích mới để hệ thống chạy thêm SQL.",
        [],
        [],
        ["Chạy phân tích mới"],
        True,
    )


def _parse_followup_response(content: str) -> dict | None:
    data = extract_json_object(content)
    return data if isinstance(data, dict) else None


def _final_response(*, conversation_id: str, turn_id: str, review: ReviewRecord,
                    answer: str, evidence: list[EvidenceItem], charts: list[ChartSpec],
                    suggestions: list[str], llm_model: str = "", error: str | None = None) -> dict:
    return {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "intent": ANALYTIC_FOLLOWUP,
        "needs_sql": False,
        "answer": answer,
        "mode": ANALYTIC_FOLLOWUP,
        "review_id": review.review_id,
        "report_markdown": answer,
        "evidence": [e.model_dump() for e in evidence],
        "charts": [c.model_dump() for c in charts],
        "sources": review.sources,
        "follow_up_suggestions": suggestions,
        "caveats": review.caveats,
        "analytic_status": review.status,
        "llm_model": llm_model,
        "error": error,
    }


def handle_followup(*, message: str, conversation_id: str, review: ReviewRecord,
                    store, client: Optional[LlmClient]) -> Iterator[dict]:
    """Answer from stored review evidence only. Emits ChatResponse-compatible final event."""
    yield _step("retrieve", "done", review_id=review.review_id)
    text = normalize_vietnamese_text(message)
    matches = _match_evidence(message, review.evidence)
    charts = _charts_for_evidence(review.charts, matches)
    answer = ""
    suggestions: list[str] = []
    llm_model = ""
    err: str | None = None

    if _contains(text, SQL_TERMS) or _contains(text, EVIDENCE_TERMS) or _contains(text, CHART_TERMS):
        answer, matches, charts, suggestions, _ = _fallback_answer(message, review)
    elif client is None:
        answer, matches, charts, suggestions, needs_new = _fallback_answer(message, review)
        err = "needs_new_analysis" if needs_new else None
    else:
        yield _step("llm", "start")
        system = review_prompts.build_followup_system_prompt()
        user = review_prompts.build_followup_user_prompt(
            question=message, review_question=review.question,
            evidence=review.evidence, charts=review.charts, caveats=review.caveats)
        res = client.chat(system, user, json_object=True)
        llm_model = res.model
        if res.error:
            answer, matches, charts, suggestions, needs_new = _fallback_answer(message, review)
            err = "needs_new_analysis" if needs_new else None
            yield _step("llm", "done", error=res.error)
        else:
            data = _parse_followup_response(res.content)
            if not data:
                answer, matches, charts, suggestions, needs_new = _fallback_answer(message, review)
                err = "needs_new_analysis" if needs_new else None
                yield _step("llm", "done", error="invalid follow-up JSON")
            else:
                ids = set(data.get("matching_evidence_ids") or [])
                selected = [ev for ev in review.evidence if ev.evidence_id in ids]
                if selected:
                    matches = selected
                    charts = _charts_for_evidence(review.charts, matches)
                answer = str(data.get("answer") or "").strip()
                suggestions = [str(s) for s in (data.get("follow_up_suggestions") or []) if str(s).strip()]
                if data.get("needs_new_analysis"):
                    err = "needs_new_analysis"
                    if not answer:
                        answer = (
                            "Câu hỏi này cần phân tích mới ngoài bằng chứng đã lưu. "
                            "Hãy gửi nó như một yêu cầu phân tích mới."
                        )
                if not answer:
                    answer, matches, charts, suggestions, needs_new = _fallback_answer(message, review)
                    err = "needs_new_analysis" if needs_new else None
                yield _step("llm", "done", model=res.model)

    if not suggestions:
        suggestions = ["Cho xem SQL đã dùng", "Cho xem bảng chứng cứ"]

    yield _step("save", "start")
    turn_id = uuid.uuid4().hex
    try:
        saved = store.save_non_sql_turn(
            conversation_id, message, intent=ANALYTIC_FOLLOWUP,
            answer=answer, answer_from_memory=answer, review_id=review.review_id,
            llm_model=llm_model, error=err or "", turn_id=turn_id)
        turn_id = saved.turn_id
    except Exception:  # noqa: BLE001 - never drop the answer on persistence failure
        log.exception("failed to persist analytic follow-up for review %s", review.review_id)
    yield _step("save", "done", review_id=review.review_id, review_status=review.status)

    yield {
        "type": "final",
        "response": _final_response(
            conversation_id=conversation_id, turn_id=turn_id, review=review,
            answer=answer, evidence=matches, charts=charts, suggestions=suggestions,
            llm_model=llm_model, error=err),
    }
