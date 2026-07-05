"""Phase 19: location resolver (backend/analysis/geo_resolver.py) against the bundled sales.db.

Reads the read-only ``data/sales.db`` (KH_005 → its vi_tri, an area name → its coords, NV_003 →
its route coords). Never raises; an unresolved reference returns ``ok=False`` + a VN hint.
"""
from backend.analysis import geo_resolver as R


def test_resolve_customer_id():
    loc = R.resolve_location("Tìm cửa hàng tiềm năng quanh khách hàng KH_005 bán kính 500m")
    assert loc.ok and loc.source == "customer"
    assert 10.0 < loc.lat < 11.0 and 106.0 < loc.lng < 107.0   # Quận 7, HCM
    assert loc.radius_m == 500


def test_resolve_area_name():
    loc = R.resolve_location("quanh Quận 7 bán kính 800m")
    assert loc.ok and loc.source == "area"
    assert 10.0 < loc.lat < 11.0 and loc.radius_m == 800


def test_resolve_employee_route():
    loc = R.resolve_location("quanh tuyến của nhân viên NV_003")
    assert loc.ok and loc.source == "employee_route"
    assert loc.lat != 0.0 and loc.lng != 0.0


def test_radius_parsing_and_clamp():
    assert R.parse_radius_m("bán kính 1.5km") == 1500
    assert R.parse_radius_m("bán kính 300m") == 300
    # a huge radius is clamped to GEO_MAX_RADIUS_M
    assert R.parse_radius_m("bán kính 99km") == R.config.GEO_MAX_RADIUS_M
    # no radius → default
    assert R.parse_radius_m("không có bán kính") == R.config.GEO_DEFAULT_RADIUS_M


def test_unresolved_location():
    loc = R.resolve_location("phân tích gì đó không có địa điểm rõ ràng")
    assert not loc.ok and loc.reason


def test_missing_customer_id():
    loc = R.resolve_location("quanh khách hàng KH_9999")
    assert not loc.ok
