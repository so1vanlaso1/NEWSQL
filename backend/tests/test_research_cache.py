"""Phase 17: research cache (backend/tools/cache.py + ReviewStore CRUD).

Round-trip, normalized-query key (trivial variants share a hit), and TTL expiry (a stale
entry is a miss). The cache stores structured results so a hit still yields evidence data.
"""
import json
from datetime import datetime, timedelta, timezone

from backend.analysis.review_store import ReviewStore
from backend.tools import cache


def _store(tmp_path):
    return ReviewStore(path=tmp_path / "conv.db")


def test_put_get_roundtrip(tmp_path):
    store = _store(tmp_path)
    results = [{"title": "t", "url": "http://a", "snippet": "s", "published": None}]
    cache.put_cached(store, "Giá Vàng   Hôm Nay", results)
    # Case + whitespace variants share a key (diacritics are preserved, not stripped).
    got = cache.get_cached(store, "giá vàng hôm nay", ttl_hours=24)
    assert got == results


def test_normalize_query():
    assert cache.normalize_query("  Giá   VÀNG  ") == "giá vàng"


def test_miss_returns_none(tmp_path):
    store = _store(tmp_path)
    assert cache.get_cached(store, "chưa từng tra", ttl_hours=24) is None


def test_ttl_expiry(tmp_path):
    store = _store(tmp_path)
    stale = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds")
    store.put_research_cache(cache.normalize_query("q"), json.dumps([{"url": "http://x"}]), stale)
    assert cache.get_cached(store, "q", ttl_hours=24) is None      # 48h old, 24h TTL -> stale
    assert cache.get_cached(store, "q", ttl_hours=72) is not None   # within a 72h TTL


def test_cached_empty_is_a_hit(tmp_path):
    # A zero-result query is cached as [] and returns [] (a hit, not None) within the TTL,
    # so the research stage does not re-hit SearxNG for a known-empty query.
    store = _store(tmp_path)
    cache.put_cached(store, "khong co gi", [])
    assert cache.get_cached(store, "khong co gi", ttl_hours=24) == []
