"""Phase 19: prospecting core (backend/analysis/geo_prospect.py) — pure, no network/DB.

Haversine, Google-type → loai mapping, dedupe vs existing customers, ranking, penetration math,
and the by-category chart. ``fetch_existing_customers`` is monkeypatched so no DB is touched.
"""
from backend.analysis import geo_prospect as P


def test_haversine_zero_and_known():
    assert P.haversine_m(10.0, 106.0, 10.0, 106.0) < 1.0
    d = P.haversine_m(10.0, 106.0, 11.0, 106.0)   # ~1° latitude ≈ 111 km
    assert 110_000 < d < 112_000
    assert P.haversine_m(None, 106.0, 10.0, 106.0) == float("inf")


def test_category_map():
    assert P.map_place_category(["convenience_store"], "convenience_store") == ("CONVENIENCE_STORE", True)
    assert P.map_place_category(["supermarket"], "supermarket") == ("MINI_SUPERMARKET", True)
    assert P.map_place_category(["restaurant"], "restaurant")[0] == "HORECA"
    assert P.map_place_category(["bank", "atm"], "bank") == ("", False)   # not an outlet


def _place(pid, name, lat, lng, ptype="convenience_store", phone=""):
    return {"place_id": pid, "name": name, "types": [ptype], "primary_type": ptype,
            "address": "addr", "lat": lat, "lng": lng, "rating": 4.1, "phone": phone,
            "maps_url": "http://maps/" + pid}


def test_analyze_area_dedupe_and_penetration(monkeypatch):
    customers = [{"khach_hang_id": "KH_1", "ten_khach_hang": "Existing Mart",
                  "so_dien_thoai": "0900000001", "vi_do": 10.0, "kinh_do": 106.0}]
    monkeypatch.setattr(P, "fetch_existing_customers", lambda a, b, c: customers)

    places = [
        _place("a", "Existing Mart", 10.0, 106.0, "supermarket"),          # same point → matched
        _place("b", "New Shop", 10.005, 106.0, "convenience_store"),       # ~550 m → prospect
        _place("c", "Vietcombank ATM", 10.0, 106.0, "atm"),               # not sellable → dropped
    ]
    res = P.analyze_area(center_lat=10.0, center_lng=106.0, radius_m=2000, places=places)

    names = [p["name"] for p in res["prospects"]]
    assert "New Shop" in names
    assert "Existing Mart" not in names and "Vietcombank ATM" not in names

    pen = res["penetration"]
    assert pen["nearby_total"] == 2          # supermarket + convenience (ATM dropped)
    assert pen["matched_nearby"] == 1
    assert pen["prospects"] == 1
    assert pen["customers_in_area"] == 1


def test_prospects_ranked_by_distance(monkeypatch):
    monkeypatch.setattr(P, "fetch_existing_customers", lambda a, b, c: [])
    places = [
        _place("far", "Far", 10.02, 106.0),     # ~2.2 km
        _place("near", "Near", 10.002, 106.0),  # ~220 m
    ]
    res = P.analyze_area(center_lat=10.0, center_lng=106.0, radius_m=5000, places=places)
    assert [p["name"] for p in res["prospects"]] == ["Near", "Far"]


def test_phone_dedupe(monkeypatch):
    customers = [{"khach_hang_id": "KH_1", "ten_khach_hang": "Whatever",
                  "so_dien_thoai": "0912345678", "vi_do": 20.0, "kinh_do": 100.0}]
    monkeypatch.setattr(P, "fetch_existing_customers", lambda a, b, c: customers)
    # Same phone but far away → still treated as an existing customer (phone is the strongest signal).
    places = [_place("x", "Some Store", 10.0, 106.0, phone="091 234 5678")]
    res = P.analyze_area(center_lat=10.0, center_lng=106.0, radius_m=2000, places=places)
    assert res["prospects"] == [] and res["penetration"]["matched_nearby"] == 1


def test_category_chart():
    prospects = [
        {"loai_code": "CONVENIENCE_STORE", "loai_label": "Cửa hàng tiện lợi"},
        {"loai_code": "CONVENIENCE_STORE", "loai_label": "Cửa hàng tiện lợi"},
        {"loai_code": "GROCERY", "loai_label": "Tạp hoá"},
    ]
    ch = P.category_chart(chart_id="c1", evidence_id="e1", prospects=prospects)
    assert ch is not None and ch.type == "horizontal_bar" and ch.evidence_id == "e1"
    assert ch.x_field == "loai_label" and ch.series[0].value_field == "so_cua_hang"
    assert ch.data == [{"loai_label": "Cửa hàng tiện lợi", "so_cua_hang": 2},
                       {"loai_label": "Tạp hoá", "so_cua_hang": 1}]
    assert P.category_chart(chart_id="c2", evidence_id="e2", prospects=[]) is None
