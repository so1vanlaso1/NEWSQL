"""Phase 13/14: the analytic turn through the real ``/api/chat`` handler (offline LLM).

Exercises the chat.py routing branch, the controller, and the ChatResponse serialization of
an analytic turn without any network: the (unreachable) planner LLM degrades to the
deterministic fallback pack, so the turn still returns evidence + charts + a persisted review.
"""
from backend.analysis.models import ReviewPlan
from backend.analysis.review_store import get_review_store
from backend.api import analysis as analysis_api
from backend.api import chat, state
from backend.knowledge import analysis_meta
from backend.memory.store import get_conversation_store
from backend.retrieval.context_builder import RetrievalService


def _seed(kb):
    # Stage (like the production seeder) so cross-entry validation ordering isn't an issue.
    kb.stage("metric", {"metric": "doanh_thu",
                        "formula": "SUM(chi_tiet_don_hang_ban.thanh_tien)",
                        "required_tables": ["chi_tiet_don_hang_ban"], "aliases": ["doanh thu"]})
    for e in analysis_meta.build_analysis_entries("2024-01-01", "2025-06-24"):
        kb.stage(e["type"], e["body"])
    kb.embed_pending()


def test_analytic_turn_via_chat_handler(kb, monkeypatch):
    _seed(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    monkeypatch.setattr(state, "_retrieval", rsvc, raising=False)

    resp = chat.chat(chat.ChatRequest(message="Phân tích vì sao doanh thu giảm tháng 5 2025?"), rsvc)

    assert resp.mode == "ANALYTIC_MODE"
    assert resp.review_id.startswith("rv_")
    assert resp.needs_sql is False
    assert len(resp.evidence) >= 2
    assert len(resp.charts) >= 1
    assert resp.analytic_status in ("complete", "degraded")
    assert resp.report_markdown.startswith("## ")

    # The review is persisted and linked to a saved turn.
    review = get_review_store().get_review(resp.review_id)
    assert review is not None and review.turn_id == resp.turn_id
    turns = get_conversation_store().load_all(resp.conversation_id)
    assert turns[-1].review_id == resp.review_id


def test_normal_turn_still_works(kb, monkeypatch):
    _seed(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    monkeypatch.setattr(state, "_retrieval", rsvc, raising=False)

    # A plain lookup is not analytic; with the LLM offline it degrades to the friendly
    # "unavailable" answer — but crucially it does NOT go down the analytic path.
    resp = chat.chat(chat.ChatRequest(message="Top 10 khách hàng theo doanh thu"), rsvc)
    assert resp.mode == ""
    assert resp.review_id == ""
    assert resp.evidence == []


def test_mode_downgrade_reroutes_to_normal_pipeline(kb, monkeypatch):
    _seed(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    monkeypatch.setattr(state, "_retrieval", rsvc, raising=False)
    # Planner decides this "analytic-looking" turn is really a normal lookup.
    monkeypatch.setattr("backend.analysis.planner.plan_review",
                        lambda *a, **k: ReviewPlan(mode_downgrade="NORMAL_SQL"))

    resp = chat.chat(chat.ChatRequest(message="Phân tích doanh thu tháng 5 2025"), rsvc)
    # Fell through to the normal pipeline: no analytic artifacts, and the offline-LLM
    # normal path produced its friendly unavailable answer (not an analytic report).
    assert resp.mode == ""
    assert resp.review_id == ""
    assert resp.report_markdown == ""
    assert resp.evidence == []


def test_review_read_endpoints(kb, monkeypatch):
    _seed(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    monkeypatch.setattr(state, "_retrieval", rsvc, raising=False)
    resp = chat.chat(chat.ChatRequest(message="Phân tích vì sao doanh thu giảm tháng 5 2025?"), rsvc)

    review = analysis_api.get_review(resp.review_id)
    assert review.review_id == resp.review_id
    assert len(review.evidence) == len(resp.evidence)
    assert review.evidence[0].source_type == "sql"

    listing = analysis_api.list_conversation_reviews(resp.conversation_id)
    assert any(r["review_id"] == resp.review_id for r in listing["reviews"])


def test_geo_prospect_turn_via_chat_handler(kb, monkeypatch):
    """Phase 19: a GEO_PROSPECT message routes through chat.py to the geo controller and
    returns a persisted geo review (Google Places + the customer DB mocked; LLM off → skeleton)."""
    _seed(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    monkeypatch.setattr(state, "_retrieval", rsvc, raising=False)

    from backend.analysis import geo_controller, geo_prospect
    from backend.tools.places_nearby import PlacesResult

    lat, lng = 10.723753745574822, 106.66238377755505  # Quận 7
    places = [{"place_id": "b", "name": "New Circle K", "types": ["convenience_store"],
               "primary_type": "convenience_store", "address": "y", "lat": lat + 0.003,
               "lng": lng, "rating": 4.2, "phone": "", "maps_url": "http://m/b"}]
    monkeypatch.setattr(geo_controller.places_nearby, "search_nearby",
                        lambda **k: PlacesResult(results=places, status="OK"))
    monkeypatch.setattr(geo_prospect, "fetch_existing_customers", lambda a, b, c: [])
    monkeypatch.setattr(chat, "get_client", lambda: None)  # skeleton narration, no network

    resp = chat.chat(chat.ChatRequest(
        message="Tìm cửa hàng tiềm năng quanh Quận 7 bán kính 800m"), rsvc)

    assert resp.mode == "GEO_PROSPECT" and resp.review_id.startswith("rv_")
    assert resp.needs_sql is False
    assert len(resp.evidence) == 1 and resp.evidence[0]["source_type"] == "geo"
    assert resp.charts and resp.charts[0]["type"] == "horizontal_bar"
    assert "New Circle K" in resp.report_markdown and "[Mở](http://m/b)" in resp.report_markdown

    review = get_review_store().get_review(resp.review_id)
    assert review is not None and review.mode == "GEO_PROSPECT" and review.turn_id == resp.turn_id
    turns = get_conversation_store().load_all(resp.conversation_id)
    assert turns[-1].review_id == resp.review_id and turns[-1].intent == "GEO_PROSPECT"


def test_analytic_followup_routes_to_stored_review(kb, monkeypatch):
    _seed(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    monkeypatch.setattr(state, "_retrieval", rsvc, raising=False)

    first = chat.chat(chat.ChatRequest(message="Phân tích vì sao doanh thu giảm tháng 5 2025?"), rsvc)
    follow = chat.chat(chat.ChatRequest(
        conversation_id=first.conversation_id,
        message="Cho xem SQL đã dùng"), rsvc)

    assert follow.mode == "ANALYTIC_FOLLOWUP"
    assert follow.review_id == first.review_id
    assert "```sql" in follow.report_markdown
    assert follow.needs_sql is False
