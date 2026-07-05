"""Phase 19: the analytic geo-enrichment broker (backend/analysis/geo_research.py).

Mirrors the web-research broker test: a fake client emits a ``find_nearby_stores`` tool call;
the broker resolves the area (bundled sales.db), queries mocked Google Places, and builds
``source_type="geo"`` evidence + penetration context. Every degrade path yields a skipped_reason.
"""
from backend import config
from backend.analysis import geo_prospect
from backend.analysis import geo_research as GR
from backend.analysis.review_store import ReviewStore
from backend.llm.client import LlmResult
from backend.tools.places_nearby import PlacesResult


class _ToolClient:
    def __init__(self, tool_calls):
        self._tc = tool_calls

    def resolve_model(self):
        return "fake-model"

    def chat(self, system, user, **k):
        return LlmResult(content="", tool_calls=self._tc)


def _setup(monkeypatch, places=None):
    monkeypatch.setattr(config, "GEO_ENABLED", True)
    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setattr(GR.places_nearby, "search_nearby", lambda **k: PlacesResult(
        results=(places if places is not None else [
            {"place_id": "b", "name": "New Circle K", "types": ["convenience_store"],
             "primary_type": "convenience_store", "address": "y", "lat": 10.726, "lng": 106.665,
             "rating": 4.2, "phone": "", "maps_url": "http://m/b"}]), status="OK"))
    monkeypatch.setattr(geo_prospect, "fetch_existing_customers", lambda a, b, c: [])


def _call(client, tmp_path):
    rs = ReviewStore(path=tmp_path / "conversations.db")
    return GR.run_geo_enrichment(
        title="Doanh thu Quận 7", question="phân tích doanh thu khu vực Quận 7",
        evidence_items=[], client=client, review_store=rs, review_id="rv_test")


def _tc():
    return [{"id": "1", "name": "find_nearby_stores", "arguments": {"area": "Quận 7"}}]


def test_builds_geo_evidence(monkeypatch, tmp_path):
    _setup(monkeypatch)
    res = _call(_ToolClient(_tc()), tmp_path)
    assert not res.skipped_reason
    assert len(res.evidence) == 1 and res.evidence[0].source_type == "geo"
    assert res.geo_context and "penetration_pct" in res.geo_context[0]
    assert res.charts and res.charts[0].type == "horizontal_bar"
    # penetration profile is narratable by the writer (shape="geo")
    assert res.evidence[0].profile.get("shape") == "geo"


def test_skips_without_key(monkeypatch, tmp_path):
    _setup(monkeypatch)
    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", "")
    res = _call(_ToolClient(_tc()), tmp_path)
    assert res.skipped_reason and not res.evidence


def test_skips_without_tool_call(monkeypatch, tmp_path):
    _setup(monkeypatch)
    res = _call(_ToolClient([]), tmp_path)
    assert res.skipped_reason and not res.evidence


def test_skips_without_client(monkeypatch, tmp_path):
    _setup(monkeypatch)
    res = _call(None, tmp_path)
    assert res.skipped_reason


def test_skips_when_disabled(monkeypatch, tmp_path):
    _setup(monkeypatch)
    monkeypatch.setattr(config, "GEO_ENABLED", False)
    res = _call(_ToolClient(_tc()), tmp_path)
    assert res.skipped_reason
