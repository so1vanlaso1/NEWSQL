"""The single web-search tool: SearxNG adapter (plan §16.2).

Returns BOTH a model-facing string (``text``) and the structured results the backend uses
to build hard-provenance web evidence (``results``). Ported to httpx (matching
``llm/client.py``), reading config, using the project logger.

Discipline (same as ``llm/client.py``): **never raises**. Every failure — zero results,
timeout, transport/JSON error — maps to a Vietnamese model-facing sentence and an empty
``results`` list, so the research stage always degrades cleanly to the offline report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from backend import config
from backend.common.logging import get_logger

log = get_logger(__name__)


@dataclass
class SearchResult:
    text: str = ""                              # model-facing joined snippets
    results: list = field(default_factory=list)  # [{"title","url","snippet","published"}]


def _base_search_url() -> str:
    """SEARXNG_URL normalized to end in ``/search`` (appended only if missing)."""
    base = (config.SEARXNG_URL or "").rstrip("/")
    return base if base.endswith("/search") else f"{base}/search"


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _headers() -> dict:
    h = {"Accept": "application/json"}
    # Harmless for a plain SearxNG; required when SEARXNG_URL points at an ngrok tunnel.
    if getattr(config, "LLM_NGROK_SKIP_WARNING", True):
        h["ngrok-skip-browser-warning"] = "true"
    return h


def search_internet(query: str) -> SearchResult:
    """Query SearxNG and return ranked, truncated, structured results. Never raises."""
    q = (query or "").strip()
    if not q:
        return SearchResult(text="Không có truy vấn tìm kiếm.", results=[])

    url = _base_search_url()
    params = {"q": q, "format": "json", "language": config.SEARCH_LANGUAGE}
    try:
        with httpx.Client(timeout=config.SEARCH_TIMEOUT_SEC, follow_redirects=True) as c:
            resp = c.get(url, params=params, headers=_headers())
        if resp.status_code != 200:
            log.warning("searxng HTTP %s for %r", resp.status_code, q)
            return SearchResult(
                text=f"Công cụ tìm kiếm trả về lỗi HTTP {resp.status_code}. Tiếp tục với dữ liệu nội bộ.",
                results=[])
        data = resp.json()
    except httpx.TimeoutException:
        log.warning("searxng timeout for %r", q)
        return SearchResult(
            text="Công cụ tìm kiếm không phản hồi (timeout). Tiếp tục với dữ liệu nội bộ.",
            results=[])
    except Exception as exc:  # noqa: BLE001 - the tool must never raise
        log.warning("searxng error for %r: %s", q, exc)
        return SearchResult(
            text=f"Công cụ tìm kiếm tạm thời không khả dụng: {exc}",
            results=[])

    raw = data.get("results") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return SearchResult(
            text=f"Không tìm thấy kết quả nào cho: {q}",
            results=[])

    # Rank by score desc when present; SearxNG here omits "score", so absent scores default
    # to 0 and a stable sort preserves the engine's original ranking.
    ranked = sorted(
        [r for r in raw if isinstance(r, dict)],
        key=lambda r: r.get("score", 0) or 0,
        reverse=True,
    )[: config.SEARCH_MAX_RESULTS]

    results: list = []
    text_blocks: list = []
    for i, r in enumerate(ranked, 1):
        title = (r.get("title") or "").strip()
        snippet = _truncate(r.get("content") or "", config.SEARCH_MAX_SNIPPET_CHARS)
        link = (r.get("url") or "").strip()
        published = r.get("publishedDate") or r.get("published") or None
        results.append({"title": title, "url": link, "snippet": snippet, "published": published})
        text_blocks.append(
            f"[{i}] {title} ({published or 'không rõ ngày'})\n"
            f"Nội dung: {snippet}\nNguồn: {link}")

    return SearchResult(text="\n\n".join(text_blocks), results=results)
