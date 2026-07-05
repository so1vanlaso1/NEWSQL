"""Research cache (plan §16.3, §20.1): a repeated query within the TTL skips the SearxNG hit.

The ``research_cache`` table lives in conversations.db and is owned by ``ReviewStore`` (schema
+ connection + thread-safety in one place); this module holds the key-normalization and TTL
policy and delegates storage to the store. Structured results are cached — a hit still yields
the results used to build evidence, it just never touches the network.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from backend.common.logging import get_logger

log = get_logger(__name__)


def normalize_query(query: str) -> str:
    """Cache key: lowercased, whitespace-collapsed, stripped (so trivial variants share a hit)."""
    return " ".join((query or "").lower().split())


def _parse_iso(text: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def get_cached(store, query: str, ttl_hours: float) -> Optional[list]:
    """Return cached structured results if a fresh entry exists, else None. Never raises."""
    key = normalize_query(query)
    if not key:
        return None
    try:
        row = store.get_research_cache(key)
    except Exception as exc:  # noqa: BLE001 - a cache miss must never break research
        log.warning("research cache read failed for %r: %s", key, exc)
        return None
    if not row:
        return None
    created = _parse_iso(row.get("created_at", ""))
    if created is not None and ttl_hours > 0:
        age_h = (datetime.now(timezone.utc) - created).total_seconds() / 3600.0
        if age_h > ttl_hours:
            return None  # stale
    try:
        results = json.loads(row.get("results_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    return results if isinstance(results, list) else None


def put_cached(store, query: str, results: list) -> None:
    """Cache structured results under the normalized query. Never raises."""
    key = normalize_query(query)
    if not key:
        return
    try:
        store.put_research_cache(
            key,
            json.dumps(results or [], ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    except Exception as exc:  # noqa: BLE001 - failing to cache must never break research
        log.warning("research cache write failed for %r: %s", key, exc)
