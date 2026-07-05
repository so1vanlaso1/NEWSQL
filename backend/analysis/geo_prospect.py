"""Geo prospecting core: dedupe Google places vs existing customers, categorize, rank,
and compute market penetration (Phase 19).

Pure/deterministic — no LLM, never raises. Used by BOTH the ``GEO_PROSPECT`` mode
(``geo_controller``) and the analytic geo-enrichment tool (``geo_research``). Existing
customers are read from the read-only ``sales.db`` via ``query_runner.run_query`` (so tests
can point ``config.DB_PATH`` at a temp DB).
"""
from __future__ import annotations

import math

from backend import config
from backend.analysis.models import ChartSeries, ChartSpec
from backend.common.vn_text import normalize_vietnamese_text
from backend.execution.query_runner import run_query

# Google Places (New) type → our loai_khach_hang code. Whatever ``types`` come back, the first
# mapped one wins (primary_type is tried first). Unmapped types are not sellable outlets.
TYPE_MAP = {
    "convenience_store": "CONVENIENCE_STORE",
    "supermarket": "MINI_SUPERMARKET",
    "grocery_or_supermarket": "MINI_SUPERMARKET",
    "department_store": "MINI_SUPERMARKET",
    "shopping_mall": "MINI_SUPERMARKET",
    "grocery_store": "GROCERY",
    "food_store": "GROCERY",
    "market": "GROCERY",
    "wholesaler": "WHOLESALE_SHOP",
    "warehouse_store": "WHOLESALE_SHOP",
    "restaurant": "HORECA",
    "cafe": "HORECA",
    "coffee_shop": "HORECA",
    "bar": "HORECA",
    "bakery": "HORECA",
    "meal_takeaway": "HORECA",
    "store": "GROCERY",  # generic retail fallback
}

LOAI_LABEL = {
    "CONVENIENCE_STORE": "Cửa hàng tiện lợi",
    "MINI_SUPERMARKET": "Siêu thị mini",
    "GROCERY": "Tạp hoá",
    "WHOLESALE_SHOP": "Đại lý sỉ",
    "HORECA": "Nhà hàng/KS/Cafe",
}


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance in metres. Returns +inf if any coordinate is missing/invalid."""
    try:
        la1, lo1, la2, lo2 = (math.radians(float(lat1)), math.radians(float(lng1)),
                              math.radians(float(lat2)), math.radians(float(lng2)))
    except (TypeError, ValueError):
        return float("inf")
    dlat, dlng = la2 - la1, lo2 - lo1
    a = math.sin(dlat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlng / 2) ** 2
    return 2 * 6371000.0 * math.asin(min(1.0, math.sqrt(a)))


def map_place_category(types, primary_type: str = "") -> tuple[str, bool]:
    """(loai_khach_hang code, sellable?). Empty code + False when no type maps to an outlet."""
    for t in [primary_type] + list(types or []):
        loai = TYPE_MAP.get((t or "").strip().lower())
        if loai:
            return loai, loai in config.GEO_SELLABLE_TYPES
    return "", False


def _last9(phone) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return digits[-9:] if len(digits) >= 9 else ""


def _name_match(a: str, b: str) -> bool:
    """Loose name equality on normalized (không-dấu) names: equal or one contains the other."""
    if not a or not b:
        return False
    return a == b or a in b or b in a


def fetch_existing_customers(center_lat, center_lng, radius_m) -> list[dict]:
    """Existing customers (with coords) inside a bbox around the center. Never raises."""
    try:
        clat, clng, rad = float(center_lat), float(center_lng), float(radius_m)
    except (TypeError, ValueError):
        return []
    half = rad + config.GEO_DEDUP_RADIUS_M + 200.0
    dlat = half / 111320.0
    dlng = half / (111320.0 * max(0.1, math.cos(math.radians(clat))))
    sql = (
        "SELECT kh.khach_hang_id, kh.ten_khach_hang, kh.so_dien_thoai, kh.loai_khach_hang_id, "
        "v.vi_do, v.kinh_do "
        "FROM khach_hang kh JOIN vi_tri v ON kh.vi_tri_id = v.vi_tri_id "
        f"WHERE v.vi_do BETWEEN {clat - dlat} AND {clat + dlat} "
        f"AND v.kinh_do BETWEEN {clng - dlng} AND {clng + dlng}"
    )
    res = run_query(sql)
    return list(res.rows) if not res.error else []


def is_existing_customer(place: dict, customers: list[dict], dedup_m: float) -> bool:
    """True when a Google place is (very likely) an outlet already in the customer DB."""
    plat, plng = place.get("lat"), place.get("lng")
    pname = normalize_vietnamese_text(place.get("name"))
    pphone = _last9(place.get("phone"))
    for c in customers:
        if pphone and pphone == _last9(c.get("so_dien_thoai")):
            return True
        dist = haversine_m(plat, plng, c.get("vi_do"), c.get("kinh_do"))
        if dist <= 30:  # essentially the same point
            return True
        if dist <= dedup_m and _name_match(pname, normalize_vietnamese_text(c.get("ten_khach_hang"))):
            return True
    return False


def analyze_area(*, center_lat, center_lng, radius_m, places: list[dict]) -> dict:
    """Split nearby sellable places into prospects vs already-customers + penetration stats.

    Returns ``{prospects, matched, penetration, customers_in_area}`` where each prospect/matched
    dict is the Google place enriched with ``loai_code``, ``loai_label``, ``distance_m``.
    """
    customers = fetch_existing_customers(center_lat, center_lng, radius_m)
    sellable: list[dict] = []
    for p in places or []:
        loai, ok = map_place_category(p.get("types"), p.get("primary_type"))
        if not ok:
            continue
        q = dict(p)
        q["loai_code"] = loai
        q["loai_label"] = LOAI_LABEL.get(loai, loai)
        q["distance_m"] = round(haversine_m(center_lat, center_lng, p.get("lat"), p.get("lng")))
        sellable.append(q)

    prospects, matched = [], []
    for p in sellable:
        (matched if is_existing_customer(p, customers, config.GEO_DEDUP_RADIUS_M)
         else prospects).append(p)
    prospects.sort(key=lambda x: x["distance_m"])

    try:
        rad = float(radius_m)
    except (TypeError, ValueError):
        rad = config.GEO_DEFAULT_RADIUS_M
    customers_in_area = sum(
        1 for c in customers
        if haversine_m(center_lat, center_lng, c.get("vi_do"), c.get("kinh_do")) <= rad)

    nearby_total = len(sellable)
    penetration = {
        "nearby_total": nearby_total,
        "matched_nearby": len(matched),
        "prospects": nearby_total - len(matched),   # all untapped (before the display cap)
        "customers_in_area": customers_in_area,
        "penetration_pct": round(len(matched) / nearby_total * 100, 1) if nearby_total else 0.0,
    }
    return {
        "prospects": prospects[: config.GEO_MAX_RESULTS],
        "matched": matched,
        "penetration": penetration,
        "customers_in_area": customers_in_area,
    }


def prospects_by_category(prospects: list[dict]) -> list[dict]:
    """[{loai_label, so_cua_hang}] counts for the by-category chart (stable order)."""
    order = ["CONVENIENCE_STORE", "MINI_SUPERMARKET", "GROCERY", "WHOLESALE_SHOP", "HORECA"]
    counts: dict[str, int] = {}
    for p in prospects:
        counts[p.get("loai_code", "")] = counts.get(p.get("loai_code", ""), 0) + 1
    rows = [{"loai_label": LOAI_LABEL.get(code, code), "so_cua_hang": counts[code]}
            for code in order if counts.get(code)]
    # any codes outside the known order (shouldn't happen) appended last
    for code, n in counts.items():
        if code not in order and n:
            rows.append({"loai_label": LOAI_LABEL.get(code, code or "Khác"), "so_cua_hang": n})
    return rows


def category_chart(*, chart_id: str, evidence_id: str, prospects: list[dict],
                   title: str = "Số cửa hàng tiềm năng theo ngành hàng") -> ChartSpec | None:
    """A horizontal_bar ChartSpec of prospects-per-category, or None when there are none."""
    rows = prospects_by_category(prospects)
    if not rows:
        return None
    return ChartSpec(
        chart_id=chart_id, type="horizontal_bar", title=title, x_field="loai_label",
        series=[ChartSeries(name="Số cửa hàng", value_field="so_cua_hang")],
        data=rows, unit="", evidence_id=evidence_id, notes="")
