"""Phase 19: Google Places (New) adapter (backend/tools/places_nearby.py) with httpx mocked.

Asserts the JSON→PlacesResult contract and the never-raises discipline (no key / zero results /
timeout / transport error / HTTP error / bad coords all map to a VN sentence + empty results +
a status code). The New API uses POST, so the fake client exposes ``.post``.
"""
import httpx
import pytest

from backend import config
from backend.tools import places_nearby as pn


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

        def post(self, *a, **k):
            if exc is not None:
                raise exc
            return resp

    return _Client


def _install(monkeypatch, resp=None, exc=None, key="test-key"):
    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", key)
    monkeypatch.setattr(pn.httpx, "Client", _fake_client_factory(resp, exc))


def _payload(*names):
    return {"places": [
        {"id": f"p{i}", "displayName": {"text": n}, "types": ["convenience_store"],
         "primaryType": "convenience_store", "formattedAddress": f"addr {i}",
         "location": {"latitude": 10.0 + i / 1000, "longitude": 106.0 + i / 1000},
         "rating": 4.0, "nationalPhoneNumber": f"028 000 {i}"}
        for i, n in enumerate(names, 1)]}


def test_parse_new_api_shape(monkeypatch):
    _install(monkeypatch, _Resp(_payload("Circle K", "Bách Hoá Xanh")))
    out = pn.search_nearby(latitude=10.0, longitude=106.0, radius_m=800)
    assert out.status == "OK"
    assert [r["name"] for r in out.results] == ["Circle K", "Bách Hoá Xanh"]
    r0 = out.results[0]
    assert set(r0) >= {"place_id", "name", "primary_type", "types", "address", "lat", "lng",
                       "rating", "phone", "maps_url"}
    assert "query_place_id=p1" in r0["maps_url"]


def test_zero_results(monkeypatch):
    _install(monkeypatch, _Resp({"places": []}))
    out = pn.search_nearby(latitude=10.0, longitude=106.0, radius_m=800)
    assert out.results == [] and out.status == "ZERO_RESULTS" and "Không tìm thấy" in out.text


def test_no_key_short_circuits(monkeypatch):
    # A blank key must NOT make any HTTP call; it degrades with a NO_KEY status.
    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", "")

    def _boom(*a, **k):
        raise AssertionError("must not hit the network without a key")

    monkeypatch.setattr(pn.httpx, "Client", _boom)
    out = pn.search_nearby(latitude=10.0, longitude=106.0, radius_m=800)
    assert out.results == [] and out.status == "NO_KEY"


def test_http_error_never_raises(monkeypatch):
    _install(monkeypatch, _Resp({"error": {"code": 403}}, status=403))
    out = pn.search_nearby(latitude=10.0, longitude=106.0, radius_m=800)
    assert out.results == [] and out.status == "ERROR" and "403" in out.text


def test_timeout_never_raises(monkeypatch):
    _install(monkeypatch, exc=httpx.TimeoutException("timeout"))
    out = pn.search_nearby(latitude=10.0, longitude=106.0, radius_m=800)
    assert out.results == [] and out.status == "ERROR" and "timeout" in out.text.lower()


def test_transport_error_never_raises(monkeypatch):
    _install(monkeypatch, exc=RuntimeError("boom"))
    out = pn.search_nearby(latitude=10.0, longitude=106.0, radius_m=800)
    assert out.results == [] and out.status == "ERROR"


def test_bad_coordinates(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", "test-key")
    out = pn.search_nearby(latitude="not-a-number", longitude=106.0, radius_m=800)
    assert out.results == [] and out.status == "ERROR" and "hợp lệ" in out.text.lower()


def test_radius_and_count_clamped(monkeypatch):
    captured = {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            captured["body"] = json
            return _Resp(_payload("A"))

    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setattr(config, "GEO_MAX_RADIUS_M", 5000)
    monkeypatch.setattr(pn.httpx, "Client", _Client)
    pn.search_nearby(latitude=10.0, longitude=106.0, radius_m=999999, max_results=999)
    assert captured["body"]["locationRestriction"]["circle"]["radius"] == 5000.0
    assert captured["body"]["maxResultCount"] == 20
