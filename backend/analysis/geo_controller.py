"""GEO_PROSPECT mode controller (Phase 19).

A standalone analytic-style turn: resolve a location from the DB, query Google Places for
nearby retail outlets, drop the ones already in the customer DB, and present the un-served
stores as ranked sales prospects. The **LLM writes the narrative/advice** (streamed) while the
prospect table + numbers stay deterministic; a deterministic skeleton is used when the LLM is
unavailable. Mirrors ``controller.run_review``'s generator/SSE contract so ``api/chat.py`` wires
it in one branch and the frontend renders it as an analytic report. Never raises mid-stream.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Iterator, Optional

from backend import config
from backend.analysis import evidence as evidence_mod
from backend.analysis import geo_prospect, geo_resolver
from backend.analysis.models import ReviewRecord
from backend.common.logging import get_logger
from backend.llm import review_prompts
from backend.llm.client import LlmClient, LlmResult
from backend.tools import cache, places_nearby

log = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _step(step: str, status: str, **extra) -> dict:
    return {"type": "step", "step": step, "status": status, **extra}


def _dedupe(items: list[str]) -> list[str]:
    out, seen = [], set()
    for it in items:
        s = " ".join(str(it or "").split())
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _cell(v) -> str:
    return str("" if v is None else v).replace("|", "/").replace("\n", " ").strip()


def _prospect_table_md(prospects: list[dict]) -> str:
    if not prospects:
        return "_Không có cửa hàng tiềm năng nào trong bán kính đã chọn._"
    lines = ["| Cửa hàng | Ngành hàng | Khoảng cách | Địa chỉ | Bản đồ |",
             "|---|---|---|---|---|"]
    for p in prospects:
        dist = p.get("distance_m")
        dist_s = f"{dist}m" if dist is not None else ""
        url = p.get("maps_url") or ""
        maps = f"[Mở]({url})" if url else ""
        lines.append(f"| {_cell(p.get('name'))} | {_cell(p.get('loai_label'))} | {dist_s} "
                     f"| {_cell(p.get('address'))} | {maps} |")
    return "\n".join(lines)


def _penetration_line(label: str, pen: dict) -> str:
    return (f"Quanh **{label}** có khoảng **{pen.get('nearby_total', 0)}** cửa hàng bán lẻ, "
            f"trong đó **{pen.get('customers_in_area', 0)}** đã là khách hàng và còn "
            f"**~{pen.get('prospects', 0)}** cửa hàng tiềm năng "
            f"(độ phủ ~{pen.get('penetration_pct', 0)}%).")


def _skeleton_prose(label: str, pen: dict, prospects: list[dict]) -> str:
    if not prospects:
        return ("Chưa tìm được cửa hàng tiềm năng mới trong bán kính đã chọn. "
                "Hãy thử mở rộng bán kính hoặc chọn khu vực khác.")
    top = ", ".join(p.get("name", "") for p in prospects[:3] if p.get("name"))
    lines = [
        f"Có **{len(prospects)}** cửa hàng gần {label} chưa nằm trong tập khách hàng — "
        "đây là các điểm nên ưu tiên tới mời hàng.",
        "",
        "## Gợi ý mời hàng",
        f"- Ưu tiên các cửa hàng gần nhất: {top}." if top else "- Ưu tiên các cửa hàng gần nhất.",
        "- Chuẩn bị bảng giá + chương trình khuyến mãi theo đúng ngành hàng của từng cửa hàng.",
        "- Ghi nhận kết quả mỗi lần ghé để cập nhật vào tuyến bán hàng.",
    ]
    return "\n".join(lines)


def _assemble_report(label: str, prose: str, pen: dict, prospects: list[dict],
                     caveats: list[str]) -> str:
    parts = [f"## Cửa hàng tiềm năng quanh {label}", "", _penetration_line(label, pen), "",
             prose.strip(), "", "## Danh sách cửa hàng tiềm năng", _prospect_table_md(prospects)]
    if caveats:
        parts += ["", "## Lưu ý"] + [f"- {c}" for c in caveats]
    return "\n".join(parts).strip()


def _geo_final_response(*, conversation_id: str, turn_id: str, answer: str,
                        review: Optional[ReviewRecord], status: str, llm_model: str,
                        error: Optional[str] = None) -> dict:
    """A ChatResponse-compatible dict (analytic keys are known ChatResponse fields)."""
    resp = {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "intent": "GEO_PROSPECT",
        "needs_sql": False,
        "answer": answer,
        "mode": "GEO_PROSPECT",
        "analytic_status": status,
        "llm_model": llm_model,
        "error": error,
    }
    if review is not None:
        resp.update({
            "review_id": review.review_id,
            "report_markdown": review.report_markdown,
            "evidence": [e.model_dump() for e in review.evidence],
            "charts": [c.model_dump() for c in review.charts],
            "sources": review.sources,
            "follow_up_suggestions": review.follow_up_suggestions,
            "caveats": review.caveats,
        })
    return resp


def run_geo_prospect(*, message: str, conversation_id: str, turns, store, review_store,
                     client: Optional[LlmClient] = None, t0: Optional[float] = None) -> Iterator[dict]:
    """Run one geo-prospecting turn, yielding SSE events then a final response. Never raises."""
    llm_model = client.resolve_model() if client is not None else ""
    review_id = "rv_" + uuid.uuid4().hex[:16]
    created = _now()

    # ---- stage 1: locate --------------------------------------------------
    yield _step("locate", "start", query=message)
    loc = geo_resolver.resolve_location(message)
    if not loc.ok:
        yield _step("locate", "done", ok=False)
        turn_id = uuid.uuid4().hex
        try:
            saved = store.save_non_sql_turn(
                conversation_id, message, intent="GEO_PROSPECT", answer=loc.reason,
                turn_id=turn_id, error="unresolved_location", llm_model=llm_model)
            turn_id = saved.turn_id
        except Exception:  # noqa: BLE001 - never abort the stream on a persist error
            log.exception("failed to persist unresolved geo turn")
        yield {"type": "final", "response": _geo_final_response(
            conversation_id=conversation_id, turn_id=turn_id, answer=loc.reason, review=None,
            status="failed", llm_model=llm_model, error="unresolved_location")}
        return
    yield _step("locate", "done", ok=True, label=loc.label, radius=loc.radius_m, source=loc.source)

    # ---- stage 2: nearby places (cache-first) -----------------------------
    yield _step("geo", "start", label=loc.label, radius=loc.radius_m)
    types = places_nearby.DEFAULT_INCLUDED_TYPES
    results = cache.get_cached_places(
        review_store, loc.lat, loc.lng, loc.radius_m, types, config.GEO_CACHE_TTL_HOURS)
    place_status = "CACHE"
    if results is None:
        pr = places_nearby.search_nearby(
            latitude=loc.lat, longitude=loc.lng, radius_m=loc.radius_m, included_types=types)
        results = pr.results
        place_status = pr.status
        if results:
            cache.put_cached_places(review_store, loc.lat, loc.lng, loc.radius_m, types, results)
    yield _step("geo", "done", found=len(results), place_status=place_status)

    # ---- stage 3: dedupe vs existing customers + penetration --------------
    yield _step("match", "start")
    analysis = geo_prospect.analyze_area(
        center_lat=loc.lat, center_lng=loc.lng, radius_m=loc.radius_m, places=results)
    prospects = analysis["prospects"]
    pen = analysis["penetration"]
    yield _step("match", "done", prospect_count=len(prospects),
                matched_count=pen.get("matched_nearby", 0))

    # ---- evidence + chart -------------------------------------------------
    ev_id = f"{review_id}_geo1"
    ev = evidence_mod.build_geo_evidence(
        ev_id, review_id, title=f"Cửa hàng tiềm năng gần {loc.label}", label=loc.label,
        prospects=prospects, penetration=pen, created_at=created)

    yield _step("charts", "start")
    charts = []
    chart = geo_prospect.category_chart(chart_id="cgeo1", evidence_id=ev_id, prospects=prospects)
    if chart is not None:
        ev.chart_id = chart.chart_id
        charts.append(chart)
        yield {"type": "chart", "chart": chart.model_dump()}
    yield {"type": "evidence", "evidence": ev.model_dump()}
    yield _step("charts", "done", chart_count=len(charts))

    # ---- caveats ----------------------------------------------------------
    caveats: list[str] = []
    if place_status == "NO_KEY":
        caveats.append("Chưa cấu hình GOOGLE_MAPS_API_KEY nên không tra cứu được cửa hàng thực tế.")
    elif place_status == "ERROR":
        caveats.append("Không gọi được Google Places; kết quả có thể chưa đầy đủ.")
    elif place_status == "ZERO_RESULTS" or not results:
        caveats.append("Không tìm thấy cửa hàng nào trong bán kính; thử tăng bán kính.")
    if loc.area_note:
        caveats.append(loc.area_note)
    caveats.append("Toạ độ dùng tâm khu vực trong dữ liệu mẫu; cần khảo sát thực địa trước khi chào hàng.")
    caveats = _dedupe(caveats)

    # ---- stage 4: LLM narrative (deterministic table underneath) ----------
    yield _step("write", "start")
    used_fallback = False
    werr = ""
    if not prospects or client is None:
        prose = _skeleton_prose(loc.label, pen, prospects)
        used_fallback = client is None
    else:
        compact = [{"name": p.get("name"), "nganh_hang": p.get("loai_label"),
                    "khoang_cach_m": p.get("distance_m"), "dia_chi": p.get("address"),
                    "maps_url": p.get("maps_url"), "danh_gia": p.get("rating")}
                   for p in prospects]
        system = review_prompts.build_geo_writer_system_prompt()
        user = review_prompts.build_geo_writer_user_prompt(
            question=message, label=loc.label, penetration=pen, prospects=compact, caveats=caveats)
        parts: list[str] = []
        result: LlmResult | None = None
        try:
            for kind, payload in client.stream_chat(
                    system, user, temperature=config.LLM_TEMPERATURE_WRITER,
                    max_tokens=config.LLM_MAX_TOKENS_WRITER, json_object=False):
                if kind == "delta":
                    parts.append(str(payload))
                    yield {"type": "token", "delta": str(payload)}
                elif isinstance(payload, LlmResult):
                    result = payload
        except Exception as exc:  # noqa: BLE001 - writer failure degrades to skeleton
            werr = f"{exc.__class__.__name__}: {exc}"
        content = "".join(parts).strip()
        if result is not None and result.error:
            werr = result.error
        if not content and result is not None:
            content = (result.content or "").strip()
        if content and not werr:
            prose = content
        else:
            prose = _skeleton_prose(loc.label, pen, prospects)
            used_fallback = True
    if used_fallback and prospects:
        caveats = _dedupe(caveats + ["Phần diễn giải dùng bản rút gọn vì LLM không phản hồi đầy đủ."])
    yield _step("write", "done", fallback=used_fallback, error=werr)

    report_md = _assemble_report(loc.label, prose, pen, prospects, caveats)
    status = "complete" if prospects else "degraded"
    findings = (f"Tìm thấy {len(prospects)} cửa hàng tiềm năng quanh {loc.label} "
                f"(độ phủ ~{pen.get('penetration_pct', 0)}%)." if prospects
                else f"Chưa tìm được cửa hàng tiềm năng mới quanh {loc.label}.")
    followups = [f"Mở rộng bán kính quanh {loc.label} lên 1km",
                 "Chỉ tìm cửa hàng tiện lợi",
                 f"Phân tích doanh thu khu vực {loc.label}"]

    review = ReviewRecord(
        review_id=review_id, conversation_id=conversation_id, mode="GEO_PROSPECT",
        question=message, findings_summary=findings, report_markdown=report_md,
        evidence=[ev], charts=charts, sources=[], follow_up_suggestions=followups,
        caveats=caveats, status=status, created_at=created)

    # ---- stage 5: persist (review first, then link the turn) --------------
    yield _step("save", "start")
    turn_id = uuid.uuid4().hex
    review.turn_id = turn_id
    review_saved = False
    try:
        review_store.save_review(review)
        review_saved = True
    except Exception:  # noqa: BLE001 - a persist error must not drop the answer
        log.exception("failed to persist geo review %s", review_id)
    try:
        saved = store.save_non_sql_turn(
            conversation_id, message, intent="GEO_PROSPECT", answer=findings, turn_id=turn_id,
            review_id=review_id if review_saved else "", llm_model=llm_model)
        turn_id = saved.turn_id
    except Exception:  # noqa: BLE001 - never abort the stream on a turn-persist error
        log.exception("failed to persist geo turn for review %s", review_id)
    yield _step("save", "done", review_id=review_id if review_saved else "", review_status=status)

    yield {"type": "final", "response": _geo_final_response(
        conversation_id=conversation_id, turn_id=turn_id, answer=findings, review=review,
        status=status, llm_model=llm_model, error=None)}
