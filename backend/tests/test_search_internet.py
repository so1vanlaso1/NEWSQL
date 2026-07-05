"""Phase 17: SearxNG adapter (backend/tools/search_internet.py) with httpx mocked.

Asserts the JSON->SearchResult contract (plan §16.2): score-sort with missing-score fallback,
top-N cap, snippet truncation, the structured results shape, and the never-raises discipline
(zero results / timeout / transport error all map to a VN sentence + empty results).
"""
import httpx
import pytest

from backend import config
from backend.tools import search_internet as si


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = "{}"

    def json(self):
        return self._payload


def _fake_client_factory(resp=None, exc=None):
    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            if exc is not None:
                raise exc
            return resp

    return _Client


def _install(monkeypatch, resp=None, exc=None):
    monkeypatch.setattr(si.httpx, "Client", _fake_client_factory(resp, exc))


def test_score_sort_and_topn(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_MAX_RESULTS", 2)
    payload = {"results": [
        {"title": "low", "url": "http://a", "content": "x", "score": 0.1},
        {"title": "high", "url": "http://b", "content": "y", "score": 0.9},
        {"title": "mid", "url": "http://c", "content": "z", "score": 0.5},
    ]}
    _install(monkeypatch, _Resp(payload))
    out = si.search_internet("q")
    assert [r["title"] for r in out.results] == ["high", "mid"]  # sorted desc, capped at 2
    assert "[1] high" in out.text and "Nguồn: http://b" in out.text


def test_missing_score_preserves_order(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_MAX_RESULTS", 5)
    payload = {"results": [
        {"title": "first", "url": "http://a", "content": "x"},
        {"title": "second", "url": "http://b", "content": "y"},
    ]}
    _install(monkeypatch, _Resp(payload))
    out = si.search_internet("q")
    assert [r["title"] for r in out.results] == ["first", "second"]  # stable, no score


def test_snippet_truncation(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_MAX_RESULTS", 5)
    monkeypatch.setattr(config, "SEARCH_MAX_SNIPPET_CHARS", 10)
    payload = {"results": [{"title": "t", "url": "http://a", "content": "0123456789ABCDEF"}]}
    _install(monkeypatch, _Resp(payload))
    out = si.search_internet("q")
    snip = out.results[0]["snippet"]
    assert snip.endswith("...") and len(snip) <= 13  # 10 chars + "..."


def test_structured_shape(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_MAX_RESULTS", 5)
    payload = {"results": [
        {"title": "t", "url": "http://a", "content": "c", "publishedDate": "2025-01-01"}]}
    _install(monkeypatch, _Resp(payload))
    out = si.search_internet("q")
    assert set(out.results[0].keys()) == {"title", "url", "snippet", "published"}
    assert out.results[0]["published"] == "2025-01-01"


def test_zero_results(monkeypatch):
    _install(monkeypatch, _Resp({"results": []}))
    out = si.search_internet("q")
    assert out.results == [] and "Không tìm thấy" in out.text


def test_timeout_never_raises(monkeypatch):
    _install(monkeypatch, exc=httpx.TimeoutException("timeout"))
    out = si.search_internet("q")
    assert out.results == [] and "timeout" in out.text.lower()


def test_transport_error_never_raises(monkeypatch):
    _install(monkeypatch, exc=RuntimeError("boom"))
    out = si.search_internet("q")
    assert out.results == [] and "không khả dụng" in out.text.lower()


def test_http_error_never_raises(monkeypatch):
    _install(monkeypatch, _Resp({}, status=502))
    out = si.search_internet("q")
    assert out.results == [] and "502" in out.text


def test_empty_query_short_circuits(monkeypatch):
    # No HTTP call needed; an empty query returns a VN sentence + no results.
    out = si.search_internet("   ")
    assert out.results == []


def test_base_url_appends_search(monkeypatch):
    monkeypatch.setattr(config, "SEARXNG_URL", "https://host.example")
    assert si._base_search_url() == "https://host.example/search"
    monkeypatch.setattr(config, "SEARXNG_URL", "https://host.example/search")
    assert si._base_search_url() == "https://host.example/search"  # not doubled
