"""Single-shot geolocation-tool broker for analytic reviews (Phase 19).

Parallel to ``research.run_research``: it hands the model ONE native tool (find_nearby_stores)
in a single turn; for each emitted call the backend resolves the area → coords, queries Google
Places (cache-first), computes market penetration, and builds ``source_type="geo"`` evidence for
the writer. **The model is never re-invoked** (no agentic loop).

Every failure — geo disabled, no key, no LLM, LLM error, no/one malformed call, unresolved area,
Places down, zero results — degrades to an empty geo context + a ``skipped_reason``; the SQL
report always ships in full.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend import config
from backend.analysis import evidence as evidence_mod
from backend.analysis import geo_prospect, geo_resolver
from backend.common.logging import get_logger
from backend.llm import review_prompts
from backend.llm.client import LlmClient
from backend.tools import cache, places_nearby, registry

log = get_logger(__name__)


@dataclass
class GeoEnrichmentResult:
    evidence: list = field(default_factory=list)      # list[EvidenceItem], source_type="geo"
    charts: list = field(default_factory=list)        # list[ChartSpec] (one per geo evidence)
    geo_context: list = field(default_factory=list)   # [{area,label,radius_m,...penetration}]
    queries: list = field(default_factory=list)       # areas the model asked about
    skipped_reason: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_geo_enrichment(*, title: str, question: str, evidence_items: list,
                       client: Optional[LlmClient], review_store, review_id: str,
                       created_at: str = "") -> GeoEnrichmentResult:
    """Plan + execute find_nearby_stores calls in one model turn. Never raises."""
    if not config.GEO_ENABLED:
        return GeoEnrichmentResult(skipped_reason="Tính năng geo đang tắt")
    if not config.GOOGLE_MAPS_API_KEY:
        return GeoEnrichmentResult(skipped_reason="Chưa cấu hình GOOGLE_MAPS_API_KEY")
    if client is None:
        return GeoEnrichmentResult(skipped_reason="Không có mô hình để gọi công cụ geo")

    try:
        system = review_prompts.build_geo_research_system_prompt()
        user = review_prompts.build_geo_research_user_prompt(
            title=title, question=question, evidence=evidence_items)
        res = client.chat(
            system, user,
            tools=registry.GEO_TOOLS_SCHEMA, tool_choice="auto",
            temperature=config.LLM_TEMPERATURE_SQL, max_tokens=config.LLM_MAX_TOKENS_SQL,
            json_object=False)
    except Exception as exc:  # noqa: BLE001 - geo enrichment never raises
        log.warning("geo enrichment LLM call failed: %s", exc)
        return GeoEnrichmentResult(skipped_reason=f"Lỗi gọi mô hình geo: {exc}")

    if res.error:
        return GeoEnrichmentResult(skipped_reason=f"Lỗi mô hình: {res.error}")
    if not res.tool_calls:
        return GeoEnrichmentResult(skipped_reason="Mô hình không gọi công cụ geo")

    created = created_at or _now()
    evidence: list = []
    charts: list = []
    geo_context: list = []
    queries: list = []
    calls = 0
    n = 0
    for tc in res.tool_calls:
        if calls >= config.GEO_MAX_CALLS_PER_REVIEW:
            break
        ok, reason, clean = registry.validate_tool_call(tc.get("name"), tc.get("arguments"))
        if not ok or (tc.get("name") or "").strip() != "find_nearby_stores":
            log.info("skipping geo tool call: %s", reason or tc.get("name"))
            continue
        calls += 1
        area = clean["area"]
        queries.append(area)

        loc = geo_resolver.resolve_location(area, default_radius_m=clean.get("radius_m"))
        if not loc.ok:
            geo_context.append({"area": area, "note": loc.reason})
            continue

        types = places_nearby.DEFAULT_INCLUDED_TYPES
        results = cache.get_cached_places(
            review_store, loc.lat, loc.lng, loc.radius_m, types, config.GEO_CACHE_TTL_HOURS)
        if results is None:
            pr = places_nearby.search_nearby(
                latitude=loc.lat, longitude=loc.lng, radius_m=loc.radius_m, included_types=types)
            results = pr.results
            if results:  # cache only non-empty (a blip/zero must not suppress for the TTL)
                cache.put_cached_places(
                    review_store, loc.lat, loc.lng, loc.radius_m, types, results)

        analysis = geo_prospect.analyze_area(
            center_lat=loc.lat, center_lng=loc.lng, radius_m=loc.radius_m, places=results)
        pen = analysis["penetration"]
        n += 1
        ev_id = f"{review_id}_geo{n}"
        ev = evidence_mod.build_geo_evidence(
            ev_id, review_id, title=f"Độ phủ thị trường quanh {loc.label}", label=loc.label,
            prospects=analysis["prospects"], penetration=pen, created_at=created)
        chart = geo_prospect.category_chart(
            chart_id=f"cgeo{n}", evidence_id=ev_id, prospects=analysis["prospects"])
        if chart is not None:
            ev.chart_id = chart.chart_id
            charts.append(chart)
        evidence.append(ev)
        geo_context.append({"area": loc.label, "radius_m": loc.radius_m, **pen})

    if not evidence and not geo_context:
        return GeoEnrichmentResult(queries=queries,
                                   skipped_reason="Không thu thập được dữ liệu cửa hàng lân cận")
    return GeoEnrichmentResult(evidence=evidence, charts=charts, geo_context=geo_context,
                               queries=queries)
