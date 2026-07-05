"""Single-shot web-search planner (plan §16.4).

The 2nd of the 3 LLM calls a web-enriched review makes (planner -> THIS -> writer). It runs
AFTER the SQL tasks execute + profile, so it is seeded with the real findings. The model
emits ``search_internet`` tool calls in ONE response; the backend validates + executes each
(cache-first, <= SEARCH_MAX_CALLS_PER_REVIEW), builds ``source_type="web"`` evidence, and
hands the sources to the writer. **The research model is never re-invoked** (no agentic loop).

Every failure — search disabled, no LLM, LLM error, no/one malformed tool call, SearxNG down,
zero results — degrades to an empty web context + a ``skipped_reason``; the SQL report always
ships in full (plan §16.6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from backend import config
from backend.analysis import evidence as evidence_mod
from backend.analysis.models import DateWindow
from backend.common.logging import get_logger
from backend.llm import review_prompts
from backend.llm.client import LlmClient
from backend.tools import cache, registry

log = get_logger(__name__)


@dataclass
class ResearchResult:
    sources: list = field(default_factory=list)   # [{n,title,url,snippet,published,retrieved_at}]
    evidence: list = field(default_factory=list)  # list[EvidenceItem], source_type="web"
    queries: list = field(default_factory=list)   # executed queries (logging / SSE)
    skipped_reason: str = ""                       # non-empty => research skipped (offline notice)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_research(*, title: str, evidence_items: list, window: Optional[DateWindow],
                 dimensions: Optional[list], client: Optional[LlmClient],
                 review_store, review_id: str, created_at: str = "") -> ResearchResult:
    """Plan + execute web searches in one model turn. Never raises."""
    if not config.SEARCH_ENABLED:
        return ResearchResult(skipped_reason="Tìm kiếm web đang tắt")
    if client is None:
        return ResearchResult(skipped_reason="Không có mô hình để lập kế hoạch tìm kiếm")

    try:
        system = review_prompts.build_research_system_prompt()
        user = review_prompts.build_research_user_prompt(
            title=title, evidence=evidence_items, window=window, dimensions=dimensions)
        res = client.chat(
            system, user,
            tools=registry.SEARCH_TOOLS_SCHEMA, tool_choice="auto",
            temperature=config.LLM_TEMPERATURE_SQL, max_tokens=config.LLM_MAX_TOKENS_SQL,
            json_object=False)
    except Exception as exc:  # noqa: BLE001 - research never raises
        log.warning("research LLM call failed: %s", exc)
        return ResearchResult(skipped_reason=f"Lỗi gọi mô hình tìm kiếm: {exc}")

    if res.error:
        return ResearchResult(skipped_reason=f"Lỗi mô hình: {res.error}")
    if not res.tool_calls:
        # The model declined to search, or the endpoint does not support native tool-calling.
        return ResearchResult(skipped_reason="Mô hình không phát lệnh tìm kiếm nào")

    retrieved_at = _now()
    sources: list = []
    web_evidence: list = []
    queries: list = []
    n = 0
    calls = 0
    for tc in res.tool_calls:
        if calls >= config.SEARCH_MAX_CALLS_PER_REVIEW:
            break
        ok, reason, clean = registry.validate_tool_call(tc.get("name"), tc.get("arguments"))
        if not ok:
            log.info("skipping invalid tool call: %s", reason)
            continue  # malformed/hallucinated call skipped; the remaining calls still run
        calls += 1
        query = clean["query"]
        queries.append(query)

        # Cache-first within the TTL. Only NON-empty results are cached: search_internet
        # returns [] for both a genuinely-empty query AND a transient failure (timeout / HTTP
        # error), so caching empties would suppress research for 24h after a blip. A zero-result
        # query simply re-queries next review (rare + cheap); a transient error self-heals.
        results = cache.get_cached(review_store, query, config.SEARCH_CACHE_TTL_HOURS)
        if results is None:
            results = registry.dispatch("search_internet", clean).results
            if results:
                cache.put_cached(review_store, query, results)

        for src in (results or [])[: config.SEARCH_MAX_SOURCES_PER_QUERY]:
            if not (src.get("url") or src.get("title")):
                continue
            n += 1
            web_evidence.append(evidence_mod.build_web_evidence(
                f"{review_id}_web{n}", review_id, n=n, query=query, source=src,
                retrieved_at=retrieved_at, created_at=created_at or retrieved_at))
            sources.append({
                "n": n,
                "title": src.get("title") or src.get("url") or "",
                "url": src.get("url") or "",
                "snippet": src.get("snippet") or "",
                "published": src.get("published"),
                "retrieved_at": retrieved_at,
            })

    if not sources:
        return ResearchResult(queries=queries, skipped_reason="Không có kết quả web phù hợp")
    return ResearchResult(sources=sources, evidence=web_evidence, queries=queries)
