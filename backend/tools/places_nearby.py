"""Google Places API (New) "Nearby Search" tool (Phase 19).

Mirrors ``search_internet.py``: returns BOTH a model-facing string (``text``) and the structured
results the backend uses to build geo evidence (``results``). Ported to httpx (matching
``llm/client.py``), reading config, using the project logger.

Discipline (same as ``search_internet.py`` / ``llm/client.py``): **never raises**. Every failure
— blank key, timeout, transport/JSON error, HTTP error, zero results — maps to a Vietnamese
model-facing sentence + an empty ``results`` list + a ``status`` code, so both consumers (the
GEO_PROSPECT mode and the analytic geo-enrichment tool) degrade cleanly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import httpx

from backend import config
from backend.common.logging import get_logger

log = get_logger(__name__)

# The New Places API endpoint (Nearby Search). Requires an X-Goog-Api-Key + a FieldMask header.
_ENDPOINT = "https://places.googleapis.com/v1/places:searchNearby"
# Only the fields we need — a FieldMask is REQUIRED and also controls billing SKU/cost.
_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.types",
    "places.primaryType",
    "places.formattedAddress",
    "places.location",
    "places.rating",
    "places.nationalPhoneNumber",
])
# Default retail place types to request (all valid New-API "Table A" types). The caller can
# override; the geo layer still classifies whatever ``types`` come back (see geo_prospect.TYPE_MAP).
DEFAULT_INCLUDED_TYPES = [
    "convenience_store", "supermarket", "grocery_store", "market",
    "department_store", "shopping_mall",
]


@dataclass
class PlacesResult:
    text: str = ""                                # model-facing joined place descriptions
    results: list = field(default_factory=list)   # [{place_id,name,primary_type,types,address,lat,lng,rating,phone,maps_url}]
    status: str = "OK"                            # OK | NO_KEY | ZERO_RESULTS | ERROR
    error: str = ""


def _maps_url(lat, lng, place_id: str) -> str:
    q = f"{lat}%2C{lng}"
    base = f"https://www.google.com/maps/search/?api=1&query={q}"
    return f"{base}&query_place_id={place_id}" if place_id else base


def search_nearby(*, latitude, longitude, radius_m: Optional[int] = None,
                  included_types: Optional[list] = None,
                  max_results: Optional[int] = None) -> PlacesResult:
    """Query Google Places (New) Nearby Search around (lat, lng). Never raises."""
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError):
        return PlacesResult(text="Toạ độ (vĩ độ/kinh độ) không hợp lệ.", status="ERROR",
                            error="bad_coordinates")

    radius = config.GEO_DEFAULT_RADIUS_M if radius_m is None else radius_m
    try:
        radius = int(radius)
    except (TypeError, ValueError):
        radius = config.GEO_DEFAULT_RADIUS_M
    radius = max(1, min(radius, config.GEO_MAX_RADIUS_M))

    count = config.GEO_MAX_RESULTS if max_results is None else max_results
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = config.GEO_MAX_RESULTS
    count = max(1, min(count, 20))  # the New API caps a page at 20

    types = [t for t in (included_types or DEFAULT_INCLUDED_TYPES) if t]

    api_key = config.GOOGLE_MAPS_API_KEY
    if not api_key:
        return PlacesResult(
            text="Chưa cấu hình GOOGLE_MAPS_API_KEY nên không tra cứu được cửa hàng lân cận.",
            status="NO_KEY", error="no_key")

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": _FIELD_MASK,
    }
    body = {
        "includedTypes": types,
        "maxResultCount": count,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": float(radius),
            }
        },
        "languageCode": "vi",
        "regionCode": "VN",
    }

    try:
        with httpx.Client(timeout=config.GEO_TIMEOUT_SEC, follow_redirects=True) as c:
            resp = c.post(_ENDPOINT, headers=headers, json=body)
        if resp.status_code != 200:
            snippet = (resp.text or "")[:200]
            log.warning("places API HTTP %s for (%s,%s): %s", resp.status_code, lat, lng, snippet)
            return PlacesResult(
                text=f"Google Places trả về lỗi HTTP {resp.status_code}. Tiếp tục với dữ liệu nội bộ.",
                status="ERROR", error=f"http_{resp.status_code}")
        data = resp.json()
    except httpx.TimeoutException:
        log.warning("places API timeout for (%s,%s)", lat, lng)
        return PlacesResult(text="Google Places không phản hồi (timeout).", status="ERROR",
                            error="timeout")
    except Exception as exc:  # noqa: BLE001 - the tool must never raise
        log.warning("places API error for (%s,%s): %s", lat, lng, exc)
        return PlacesResult(text=f"Google Places tạm thời không khả dụng: {exc}", status="ERROR",
                            error=str(exc))

    raw = data.get("places") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return PlacesResult(text=f"Không tìm thấy cửa hàng nào trong bán kính {radius}m.",
                            status="ZERO_RESULTS")

    results: list = []
    text_blocks: list = []
    for i, p in enumerate(raw[:count], 1):
        if not isinstance(p, dict):
            continue
        name = ((p.get("displayName") or {}).get("text") or "").strip()
        if not name:
            continue
        loc = p.get("location") or {}
        plat, plng = loc.get("latitude"), loc.get("longitude")
        place_id = p.get("id") or ""
        types_list = [t for t in (p.get("types") or []) if isinstance(t, str)]
        primary = p.get("primaryType") or (types_list[0] if types_list else "")
        address = (p.get("formattedAddress") or "").strip()
        rating = p.get("rating")
        phone = (p.get("nationalPhoneNumber") or "").strip()
        results.append({
            "place_id": place_id,
            "name": name,
            "primary_type": primary,
            "types": types_list,
            "address": address,
            "lat": plat,
            "lng": plng,
            "rating": rating,
            "phone": phone,
            "maps_url": _maps_url(plat, plng, place_id),
        })
        rating_s = f" (⭐ {rating})" if rating else ""
        text_blocks.append(
            f"[{i}] {name}{rating_s}\nLoại: {primary}\nĐịa chỉ: {address}\nToạ độ: {plat}, {plng}")

    if not results:
        return PlacesResult(text=f"Không tìm thấy cửa hàng phù hợp trong bán kính {radius}m.",
                            status="ZERO_RESULTS")
    return PlacesResult(text="\n\n".join(text_blocks), results=results, status="OK")
