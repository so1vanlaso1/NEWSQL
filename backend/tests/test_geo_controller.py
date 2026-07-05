"""Phase 19: the GEO_PROSPECT controller end-to-end (backend/analysis/geo_controller.py).

Drives ``run_geo_prospect`` with Google Places + the customer DB mocked. Asserts the SSE stage
sequence, that prospects exclude already-customers / non-retail, that the prospect table + maps
links + chart are produced, that the LLM narration streams (and falls back to a skeleton), and
that the review + turn persist. Location resolution uses the bundled sales.db (Quận 7).
"""
from backend.analysis import geo_controller, geo_prospect
from backend.analysis.review_store import ReviewStore
from backend.llm.client import LlmResult
from backend.memory.store import ConversationStore
from backend.tools.places_nearby import PlacesResult

_CENTER = (10.723753745574822, 106.66238377755505)  # VT_005 / Quận 7


def _places():
    lat, lng = _CENTER
    return [
        {"place_id": "a", "name": "Existing Mart", "types": ["supermarket"],
         "primary_type": "supermarket", "address": "x", "lat": lat, "lng": lng,
         "rating": 4.0, "phone": "", "maps_url": "http://m/a"},              # matched (same point)
        {"place_id": "b", "name": "New Circle K", "types": ["convenience_store"],
         "primary_type": "convenience_store", "address": "y", "lat": lat + 0.003, "lng": lng,
         "rating": 4.2, "phone": "", "maps_url": "http://m/b"},              # prospect
        {"place_id": "c", "name": "Vietcombank ATM", "types": ["atm"],
         "primary_type": "atm", "address": "z", "lat": lat, "lng": lng,
         "rating": None, "phone": "", "maps_url": "http://m/c"},            # not sellable → dropped
    ]


def _customers():
    lat, lng = _CENTER
    return [{"khach_hang_id": "KH_X", "ten_khach_hang": "Existing Mart",
             "so_dien_thoai": "0900000000", "vi_do": lat, "kinh_do": lng}]


def _setup(monkeypatch, status="OK", places=None):
    monkeypatch.setattr(geo_controller.places_nearby, "search_nearby",
                        lambda **k: PlacesResult(
                            results=(places if places is not None else _places()), status=status))
    monkeypatch.setattr(geo_prospect, "fetch_existing_customers", lambda a, b, c: _customers())


class _StreamClient:
    def resolve_model(self):
        return "fake-model"

    def stream_chat(self, system, user, **k):
        yield ("delta", "Cơ hội tốt quanh khu vực. ")
        yield ("delta", "## Gợi ý mời hàng\n- Ghé sớm.")
        yield ("done", LlmResult(content="Cơ hội tốt quanh khu vực. ## Gợi ý mời hàng\n- Ghé sớm."))

    def chat(self, *a, **k):
        return LlmResult(content="")


def _run(tmp_path, message, client=None):
    store = ConversationStore(path=tmp_path / "conversations.db")
    rs = ReviewStore(path=tmp_path / "conversations.db")
    cid = store.create()
    events = list(geo_controller.run_geo_prospect(
        message=message, conversation_id=cid, turns=[], store=store, review_store=rs,
        client=client))
    return events, store, rs, cid


def _final(events):
    finals = [e for e in events if e.get("type") == "final"]
    assert finals, "controller must emit a final event"
    return finals[0]["response"]


def test_full_flow_persists_and_ranks(monkeypatch, tmp_path):
    _setup(monkeypatch)
    events, store, rs, cid = _run(
        tmp_path, "Tìm cửa hàng tiềm năng quanh Quận 7 bán kính 800m", client=_StreamClient())

    steps = [e["step"] for e in events if e.get("type") == "step"]
    for expected in ("locate", "geo", "match", "charts", "write", "save"):
        assert expected in steps, f"missing SSE step {expected}"

    resp = _final(events)
    assert resp["mode"] == "GEO_PROSPECT" and resp["review_id"].startswith("rv_")
    assert resp["analytic_status"] == "complete"

    ev = resp["evidence"][0]
    prospect_names = [r["Tên cửa hàng"] for r in ev["rows"]]
    assert "New Circle K" in prospect_names
    assert "Existing Mart" not in prospect_names and "Vietcombank ATM" not in prospect_names

    assert resp["charts"] and resp["charts"][0]["type"] == "horizontal_bar"
    assert "[Mở](http://m/b)" in resp["report_markdown"]      # clickable maps link in the table

    got = rs.get_review(resp["review_id"])
    assert got is not None and got.mode == "GEO_PROSPECT" and len(got.evidence) == 1
    turns = store.load_all(cid)
    assert turns and turns[-1].review_id == resp["review_id"] and turns[-1].intent == "GEO_PROSPECT"


def test_llm_narration_streams(monkeypatch, tmp_path):
    _setup(monkeypatch)
    events, *_ = _run(tmp_path, "quanh Quận 7 bán kính 800m", client=_StreamClient())
    tokens = [e for e in events if e.get("type") == "token"]
    assert tokens, "the LLM narration should stream token events"
    assert "Gợi ý mời hàng" in _final(events)["report_markdown"]


def test_skeleton_without_llm(monkeypatch, tmp_path):
    _setup(monkeypatch)
    events, *_ = _run(tmp_path, "quanh Quận 7 bán kính 800m", client=None)
    resp = _final(events)
    assert resp["analytic_status"] == "complete"
    assert "## Gợi ý mời hàng" in resp["report_markdown"]     # deterministic skeleton prose
    assert "## Danh sách cửa hàng tiềm năng" in resp["report_markdown"]


def test_unresolved_location_is_friendly(monkeypatch, tmp_path):
    _setup(monkeypatch)
    events, store, rs, cid = _run(
        tmp_path, "tìm cửa hàng tiềm năng nhưng chưa rõ ở đâu cả", client=None)
    resp = _final(events)
    assert resp["analytic_status"] == "failed" and resp["error"] == "unresolved_location"
    assert not resp.get("review_id")
    turns = store.load_all(cid)
    assert turns and turns[-1].intent == "GEO_PROSPECT"


def test_no_key_degrades(monkeypatch, tmp_path):
    _setup(monkeypatch, status="NO_KEY", places=[])
    events, *_ = _run(tmp_path, "quanh Quận 7 bán kính 800m", client=None)
    resp = _final(events)
    assert resp["analytic_status"] == "degraded"
    assert any("GOOGLE_MAPS_API_KEY" in c for c in resp["caveats"])
