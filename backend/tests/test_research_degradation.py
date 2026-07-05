"""Phase 17: web-research degradation (plan §16.6, §26).

Whenever research can't produce web evidence — disabled, no LLM, SearxNG down / zero results —
the review still ships the full offline SQL report with a one-line caveat, and the SQL
evidence path is never blocked.
"""
from backend import config
from backend.analysis import controller, research
from backend.analysis.review_store import ReviewStore
from backend.knowledge import analysis_meta
from backend.llm.client import LlmResult
from backend.memory.store import ConversationStore
from backend.retrieval.context_builder import RetrievalService
from backend.tools import registry
from backend.tools.search_internet import SearchResult


class _FakeClient:
    """Emits one tool call; used to reach the SearxNG dispatch under degradation."""
    def resolve_model(self):
        return "fake"

    def chat(self, system, user, **kwargs):
        return LlmResult(tool_calls=[{"name": "search_internet",
                                      "arguments": {"query": "q"}}])


def test_searxng_down_skips_without_sources(tmp_path, monkeypatch):
    # search_internet never raises: an unreachable/zero-result SearxNG yields empty results,
    # so research produces no sources and reports a skip reason (report still ships upstream).
    monkeypatch.setattr(config, "SEARCH_ENABLED", True)
    monkeypatch.setattr(registry, "search_internet",
                        lambda q: SearchResult(text="Không tìm thấy", results=[]))
    out = research.run_research(
        title="x", evidence_items=[], window=None, dimensions=[],
        client=_FakeClient(), review_store=ReviewStore(path=tmp_path / "conv.db"),
        review_id="rv1")
    assert out.sources == [] and out.skipped_reason


# ---- controller-level: a research skip keeps the full SQL report -------------
def _seed_kb(kb):
    kb.stage("metric", {
        "metric": "doanh_thu", "formula": "SUM(chi_tiet_don_hang_ban.thanh_tien)",
        "required_tables": ["chi_tiet_don_hang_ban"], "aliases": ["doanh thu", "revenue"],
        "direction": "higher_is_better", "decomposition": ["so_don_hang"],
        "interpretation_down": "giảm do mất khách"})
    for e in analysis_meta.build_analysis_entries("2024-01-01", "2025-06-24"):
        kb.stage(e["type"], e["body"])
    kb.embed_pending()


def test_controller_research_skip_keeps_sql_report(kb, tmp_path, monkeypatch):
    # Search ENABLED but no LLM -> research is skipped; the deterministic SQL pipeline still
    # produces evidence + a report, and a web-unavailable caveat is attached.
    monkeypatch.setattr(config, "SEARCH_ENABLED", True)
    _seed_kb(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    store = ConversationStore(path=tmp_path / "conversations.db")
    review_store = ReviewStore(path=tmp_path / "conversations.db")
    cid = store.create()

    events = list(controller.run_review(
        message="Phân tích vì sao doanh thu giảm tháng 5 2025?", conversation_id=cid,
        turns=[], rsvc=rsvc, mode="ANALYTIC_MODE", seed=None, store=store,
        review_store=review_store, client=None))

    steps = [(e.get("step"), e.get("status")) for e in events if e.get("type") == "step"]
    assert ("research", "skipped") in steps       # research attempted (enabled) then skipped

    resp = [e for e in events if e.get("type") == "final"][0]["response"]
    assert len(resp["evidence"]) >= 2             # SQL evidence path intact
    assert resp["sources"] == []                  # no web sources
    assert any("nguồn web" in c.lower() for c in resp["caveats"])  # one-line notice
    assert resp["report_markdown"].startswith("## ")


def test_controller_search_disabled_is_silent(kb, tmp_path, monkeypatch):
    # With search OFF, there is no research step and no web caveat (byte-clean offline path).
    monkeypatch.setattr(config, "SEARCH_ENABLED", False)
    _seed_kb(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    store = ConversationStore(path=tmp_path / "conversations.db")
    review_store = ReviewStore(path=tmp_path / "conversations.db")
    cid = store.create()

    events = list(controller.run_review(
        message="Phân tích doanh thu tháng 5 2025", conversation_id=cid, turns=[],
        rsvc=rsvc, mode="ANALYTIC_MODE", seed=None, store=store,
        review_store=review_store, client=None))

    assert "research" not in [e.get("step") for e in events if e.get("type") == "step"]
    resp = [e for e in events if e.get("type") == "final"][0]["response"]
    assert not any("nguồn web" in c.lower() for c in resp["caveats"])
