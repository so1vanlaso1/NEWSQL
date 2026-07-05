"""Final analytic report writer with deterministic skeleton fallback (Phase 15).

The writer is the second offline analytic LLM boundary. It streams markdown when the model
is available and falls back to a complete rule-built report when the model is unavailable,
empty, or errors mid-call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

from backend import config
from backend.analysis import evidence as evidence_mod
from backend.analysis.models import AdvisorOutput, ChartSpec, EvidenceItem
from backend.llm import review_prompts
from backend.llm.client import LlmClient, LlmResult


@dataclass
class WriteResult:
    report_markdown: str
    used_fallback: bool = False
    error: str = ""


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        s = " ".join(str(item or "").split())
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _bullet_section(lines: list[str], heading: str, bullets: list[str]) -> None:
    lines += ["", f"## {heading}"]
    if bullets:
        lines.extend(f"- {b}" for b in _dedupe(bullets))
    else:
        lines.append("- Chưa có đủ tín hiệu đáng tin cậy để kết luận phần này.")


def skeleton_report(
    *,
    title: str,
    evidence: list[EvidenceItem],
    charts: list[ChartSpec],
    advice: AdvisorOutput,
    caveats: list[str],
    sources: Optional[list[dict]] = None,
    reason: str = "",
) -> str:
    """Complete deterministic markdown report used when the writer LLM is unavailable."""
    ok_evidence = [ev for ev in evidence if ev.status == "success"]
    failed = [ev for ev in evidence if ev.status != "success"]
    chart_by_ev = {c.evidence_id: c for c in charts}

    lines = [
        f"## {title or 'Báo cáo phân tích'}",
        "",
        "> Báo cáo rút gọn (LLM không phản hồi) - số liệu và bảng bên dưới là chính xác; "
        "phần diễn giải tự động được tạo từ quy tắc.",
    ]
    if reason:
        lines.append(f"> Lý do: {reason}")

    _bullet_section(
        lines,
        "Tóm tắt điều hành",
        [advice.driver_summary] + [evidence_mod.profile_sentence(ev) for ev in ok_evidence[:3]],
    )

    _bullet_section(lines, "Bằng chứng chính", [evidence_mod.profile_sentence(ev) for ev in ok_evidence])

    lines += ["", "## Biểu đồ"]
    if charts:
        for c in charts:
            ev_note = f" (nguồn: {c.evidence_id})" if c.evidence_id else ""
            lines.append(f"- {c.title or c.chart_id}: {c.type}{ev_note}.")
    else:
        lines.append("- Không có biểu đồ phù hợp; xem bảng bằng chứng.")

    lines += ["", "## Diễn giải"]
    if advice.interpretation_bullets:
        lines.extend(f"- {b}" for b in advice.interpretation_bullets)
    else:
        lines.append("- Chưa đủ tín hiệu để diễn giải ngoài các số liệu đã chạy.")

    _bullet_section(lines, "Khuyến nghị cải thiện", advice.improvement_bullets)

    lines += ["", "## Bảng cần xem"]
    for ev in evidence:
        status = "" if ev.status == "success" else f" ({ev.status})"
        chart = chart_by_ev.get(ev.evidence_id)
        suffix = f"; biểu đồ {chart.chart_id}" if chart else ""
        lines.append(f"- {ev.title}{status}{suffix}.")
    if failed:
        lines.append("- Một số bước không có dữ liệu hoặc không chạy được; xem phần lưu ý.")

    if sources:
        lines += ["", "## Bối cảnh thị trường"]
        for s in sources:
            n = s.get("n")
            title = s.get("title") or s.get("url") or ""
            snippet = (s.get("snippet") or "").strip()
            tail = f" — {snippet}" if snippet else ""
            lines.append(f"- [{n}] {title}{tail}")

    _bullet_section(lines, "Phân tích tiếp theo", advice.next_questions)
    _bullet_section(lines, "Lưu ý", caveats)
    return "\n".join(lines).strip()


def stream_report(
    *,
    client: Optional[LlmClient],
    title: str,
    question: str,
    evidence: list[EvidenceItem],
    charts: list[ChartSpec],
    advice: AdvisorOutput,
    caveats: list[str],
    sources: Optional[list[dict]] = None,
) -> Iterator[Tuple[str, object]]:
    """Yield ("delta", text) chunks and a final ("done", WriteResult). Never raises."""
    fallback = lambda reason="": WriteResult(
        skeleton_report(
            title=title, evidence=evidence, charts=charts, advice=advice,
            caveats=caveats, sources=sources or [], reason=reason),
        used_fallback=True,
        error=reason,
    )

    if client is None:
        yield ("done", fallback("không có LLM writer"))
        return

    system = review_prompts.build_writer_system_prompt()
    user = review_prompts.build_writer_user_prompt(
        question=question, title=title, evidence=evidence, charts=charts,
        advice=advice, caveats=caveats, sources=sources or [])

    parts: list[str] = []
    result: LlmResult | None = None
    try:
        for kind, payload in client.stream_chat(
            system,
            user,
            temperature=config.LLM_TEMPERATURE_WRITER,
            max_tokens=config.LLM_MAX_TOKENS_WRITER,
            json_object=False,
        ):
            if kind == "delta":
                text = str(payload)
                parts.append(text)
                yield ("delta", text)
            elif isinstance(payload, LlmResult):
                result = payload
    except Exception as exc:  # noqa: BLE001 - writer failure degrades to skeleton
        yield ("done", fallback(f"{exc.__class__.__name__}: {exc}"))
        return

    content = "".join(parts).strip()
    if result is not None and result.error:
        yield ("done", fallback(result.error))
        return
    if not content and result is not None:
        content = (result.content or "").strip()
    if not content:
        yield ("done", fallback("writer trả về rỗng"))
        return
    yield ("done", WriteResult(report_markdown=content, used_fallback=False))

