"""Phase 17: single-shot web-search planner (backend/analysis/research.py).

A fake LLM emits ``search_internet`` tool calls in ONE response; SearxNG is stubbed. Asserts
the backend executes the calls, builds ``source_type="web"`` evidence, honors the ≤5-call and
≤3-sources caps, skips malformed calls without aborting, and — critically — that the research
model is executed exactly once (no agentic re-invocation, plan §16.4).
"""
from backend import config
from backend.analysis import research
from backend.analysis.review_store import ReviewStore
from backend.llm.client import LlmResult
from backend.tools import registry
from backend.tools.search_internet import SearchResult


class FakeClient:
    def __init__(self, tool_calls, error=None):
        self._tool_calls = tool_calls
        self._error = error
        self.calls = 0

    def resolve_model(self):
        return "fake"

    def chat(self, system, user, **kwargs):
        self.calls += 1
        # Native tool-calling disables JSON mode; the research stage must not force it.
        assert kwargs.get("json_object") is False
        assert kwargs.get("tools") is registry.SEARCH_TOOLS_SCHEMA
        return LlmResult(tool_calls=list(self._tool_calls), error=self._error)


def _tc(query):
    return {"id": "c", "name": "search_internet", "arguments": {"query": query}}


def _fake_search(nresults):
    def _search(query):
        results = [{"title": f"{query} #{i}", "url": f"http://x/{i}",
                    "snippet": "s", "published": None} for i in range(nresults)]
        return SearchResult(text="t", results=results)
    return _search


def _store(tmp_path):
    return ReviewStore(path=tmp_path / "conv.db")


def test_executes_and_builds_web_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_ENABLED", True)
    monkeypatch.setattr(registry, "search_internet", _fake_search(2))
    client = FakeClient([_tc("giá vàng"), _tc("giá xăng")])
    out = research.run_research(
        title="Doanh thu giảm", evidence_items=[], window=None, dimensions=[],
        client=client, review_store=_store(tmp_path), review_id="rv1")
    assert out.skipped_reason == ""
    assert client.calls == 1                       # model executed ONCE, never re-invoked
    assert out.queries == ["giá vàng", "giá xăng"]
    assert len(out.sources) == 4                   # 2 queries x 2 results
    assert all(ev.source_type == "web" for ev in out.evidence)
    assert [s["n"] for s in out.sources] == [1, 2, 3, 4]  # running global citation index


def test_no_tool_calls_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_ENABLED", True)
    client = FakeClient([])
    out = research.run_research(
        title="x", evidence_items=[], window=None, dimensions=[],
        client=client, review_store=_store(tmp_path), review_id="rv1")
    assert out.skipped_reason and not out.sources
    assert client.calls == 1


def test_disabled_skips_without_calling_model(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_ENABLED", False)
    client = FakeClient([_tc("q")])
    out = research.run_research(
        title="x", evidence_items=[], window=None, dimensions=[],
        client=client, review_store=_store(tmp_path), review_id="rv1")
    assert out.skipped_reason and client.calls == 0


def test_malformed_calls_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_ENABLED", True)
    monkeypatch.setattr(registry, "search_internet", _fake_search(1))
    tool_calls = [
        {"name": "search_internet", "arguments": {}},          # missing query -> skip
        {"name": "unknown_tool", "arguments": {"query": "x"}},  # unknown name -> skip
        _tc("hợp lệ"),                                          # valid -> executes
    ]
    client = FakeClient(tool_calls)
    out = research.run_research(
        title="x", evidence_items=[], window=None, dimensions=[],
        client=client, review_store=_store(tmp_path), review_id="rv1")
    assert out.queries == ["hợp lệ"]               # only the valid call ran; others skipped
    assert len(out.sources) == 1


def test_max_calls_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_ENABLED", True)
    monkeypatch.setattr(config, "SEARCH_MAX_CALLS_PER_REVIEW", 2)
    monkeypatch.setattr(registry, "search_internet", _fake_search(1))
    client = FakeClient([_tc(f"q{i}") for i in range(5)])
    out = research.run_research(
        title="x", evidence_items=[], window=None, dimensions=[],
        client=client, review_store=_store(tmp_path), review_id="rv1")
    assert len(out.queries) == 2                   # capped at SEARCH_MAX_CALLS_PER_REVIEW


def test_sources_per_query_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_ENABLED", True)
    monkeypatch.setattr(config, "SEARCH_MAX_SOURCES_PER_QUERY", 3)
    monkeypatch.setattr(registry, "search_internet", _fake_search(10))
    client = FakeClient([_tc("q")])
    out = research.run_research(
        title="x", evidence_items=[], window=None, dimensions=[],
        client=client, review_store=_store(tmp_path), review_id="rv1")
    assert len(out.sources) == 3                   # capped per query


def test_cache_hit_skips_search(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEARCH_ENABLED", True)
    store = _store(tmp_path)

    hits = {"n": 0}

    def _counting_search(query):
        hits["n"] += 1
        return SearchResult(text="t", results=[{"title": "a", "url": "http://a",
                                                "snippet": "s", "published": None}])

    monkeypatch.setattr(registry, "search_internet", _counting_search)
    # First review populates the cache; second identical query is served from it.
    for _ in range(2):
        research.run_research(title="x", evidence_items=[], window=None, dimensions=[],
                              client=FakeClient([_tc("cùng truy vấn")]),
                              review_store=store, review_id="rv1")
    assert hits["n"] == 1                          # SearxNG hit once; 2nd was a cache hit
