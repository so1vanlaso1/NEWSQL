"""Resolve a natural-language location reference to a center coordinate (Phase 19).

The GEO_PROSPECT mode and the analytic geo tool both need a lat/lng center. Per the approved
design the center is **DB-resolved** from ``vi_tri`` — a customer (KH_*), an employee's route
(NV_* → phan_cong_tuyen → tuyen_ban_hang → vi_tri), or an area name (province/district/ward);
a raw ``lat,lng`` is an internal fallback only. Reads the read-only ``sales.db`` via
``query_runner.run_query`` (so tests can repoint ``config.DB_PATH``). Never raises: an
unresolved reference returns ``ok=False`` + a Vietnamese hint.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from backend import config
from backend.common.vn_text import normalize_vietnamese_text
from backend.execution.query_runner import run_query

_KH_RE = re.compile(r"\bkh[_\s]?0*(\d{1,4})\b", re.I)
_NV_RE = re.compile(r"\bnv[_\s]?0*(\d{1,4})\b", re.I)
_LATLNG_RE = re.compile(r"(-?\d{1,2}[.,]\d{3,})\s*[,; ]\s*(-?\d{2,3}[.,]\d{3,})")
_RADIUS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(km|m)(?![a-z])")


@dataclass
class GeoLocation:
    ok: bool = False
    reason: str = ""
    lat: float = 0.0
    lng: float = 0.0
    label: str = ""
    radius_m: int = 0
    source: str = ""       # customer | employee_route | area | latlng
    area_note: str = ""    # extra caveat (e.g. employee route ended / multiple routes)


_UNRESOLVED = (
    "Chưa xác định được vị trí. Hãy nêu rõ: mã khách hàng (VD: KH_005), khu vực "
    "(quận/tỉnh, VD: Quận 7), hoặc nhân viên (VD: NV_003)."
)


def parse_radius_m(text: str, default_m: Optional[int] = None) -> int:
    """Extract a radius in metres from free text; fall back to the default; clamp to the max."""
    base = config.GEO_DEFAULT_RADIUS_M if default_m is None else int(default_m)
    # Search the RAW (lowercased) text, not the không-dấu form: normalization strips the decimal
    # point ("1.5km" → "1 5km"), which would misread the radius.
    m = _RADIUS_RE.search(str(text or "").lower())
    if m:
        try:
            val = float(m.group(1).replace(",", "."))
            base = int(val * 1000) if m.group(2) == "km" else int(val)
        except (TypeError, ValueError):
            pass
    return max(1, min(base, config.GEO_MAX_RADIUS_M))


def _rows(sql: str) -> list[dict]:
    res = run_query(sql)
    return list(res.rows) if not res.error else []


def _customer_center(kh_id: str) -> Optional[dict]:
    rows = _rows(
        "SELECT kh.ten_khach_hang, v.vi_do, v.kinh_do, v.tinh_thanh, v.quan_huyen "
        "FROM khach_hang kh JOIN vi_tri v ON kh.vi_tri_id = v.vi_tri_id "
        f"WHERE kh.khach_hang_id = '{kh_id}'")
    return rows[0] if rows else None


def _employee_center(nv_id: str) -> Optional[dict]:
    # Active assignments first (end date null/blank/future), then the most recent by start date.
    rows = _rows(
        "SELECT v.vi_do, v.kinh_do, v.tinh_thanh, v.quan_huyen, t.ten_tuyen, pc.ngay_ket_thuc "
        "FROM phan_cong_tuyen pc "
        "JOIN tuyen_ban_hang t ON pc.tuyen_id = t.tuyen_id "
        "JOIN vi_tri v ON t.vi_tri_id = v.vi_tri_id "
        f"WHERE pc.nhan_vien_id = '{nv_id}' AND v.vi_do IS NOT NULL "
        "ORDER BY (CASE WHEN pc.ngay_ket_thuc IS NULL OR pc.ngay_ket_thuc = '' "
        "OR pc.ngay_ket_thuc >= date('now') THEN 0 ELSE 1 END), pc.ngay_bat_dau DESC "
        "LIMIT 1")
    return rows[0] if rows else None


def _area_center(norm_text: str) -> Optional[dict]:
    rows = _rows("SELECT DISTINCT tinh_thanh, quan_huyen, phuong_xa, vi_do, kinh_do FROM vi_tri")
    # Prefer the most specific match: ward, then district, then province.
    for field in ("phuong_xa", "quan_huyen", "tinh_thanh"):
        for r in rows:
            val = normalize_vietnamese_text(r.get(field))
            if val and f" {val} " in f" {norm_text} ":
                label = " · ".join(v for v in (r.get("quan_huyen"), r.get("tinh_thanh")) if v)
                return {"vi_do": r.get("vi_do"), "kinh_do": r.get("kinh_do"),
                        "label": r.get(field), "area": label}
    return None


def resolve_location(text: str, *, default_radius_m: Optional[int] = None) -> GeoLocation:
    """Resolve a message/area string to a center coordinate. Never raises."""
    raw = str(text or "")
    norm = normalize_vietnamese_text(raw)
    radius = parse_radius_m(raw, default_radius_m)

    # 1) Explicit customer id.
    m = _KH_RE.search(raw)
    if m:
        kh_id = f"KH_{int(m.group(1)):03d}"
        row = _customer_center(kh_id)
        if row and row.get("vi_do") is not None:
            return GeoLocation(ok=True, lat=float(row["vi_do"]), lng=float(row["kinh_do"]),
                               label=f"{row.get('ten_khach_hang') or kh_id}", radius_m=radius,
                               source="customer")
        return GeoLocation(reason=f"Không tìm thấy toạ độ cho khách hàng {kh_id}.", radius_m=radius)

    # 2) Explicit employee id → route area.
    m = _NV_RE.search(raw)
    if m:
        nv_id = f"NV_{int(m.group(1)):03d}"
        row = _employee_center(nv_id)
        if row and row.get("vi_do") is not None:
            ended = bool(row.get("ngay_ket_thuc"))
            note = "Dùng tuyến gần nhất của nhân viên." if ended else ""
            label = f"tuyến {row.get('ten_tuyen') or nv_id}"
            return GeoLocation(ok=True, lat=float(row["vi_do"]), lng=float(row["kinh_do"]),
                               label=label, radius_m=radius, source="employee_route", area_note=note)
        return GeoLocation(reason=f"Không tìm thấy tuyến/vị trí cho nhân viên {nv_id}.", radius_m=radius)

    # 3) Area name (province / district / ward).
    area = _area_center(norm)
    if area and area.get("vi_do") is not None:
        return GeoLocation(ok=True, lat=float(area["vi_do"]), lng=float(area["kinh_do"]),
                           label=area.get("label") or "khu vực", radius_m=radius, source="area")

    # 4) Customer by name (demo names are generic; low priority).
    if len(norm) >= 4:
        for c in _rows("SELECT ten_khach_hang, khach_hang_id, vi_tri_id FROM khach_hang"):
            cn = normalize_vietnamese_text(c.get("ten_khach_hang"))
            if cn and len(cn) >= 4 and f" {cn} " in f" {norm} ":
                row = _customer_center(c["khach_hang_id"])
                if row and row.get("vi_do") is not None:
                    return GeoLocation(ok=True, lat=float(row["vi_do"]), lng=float(row["kinh_do"]),
                                       label=c.get("ten_khach_hang"), radius_m=radius, source="customer")

    # 5) Raw lat,lng (internal fallback).
    m = _LATLNG_RE.search(raw)
    if m:
        try:
            lat = float(m.group(1).replace(",", "."))
            lng = float(m.group(2).replace(",", "."))
            return GeoLocation(ok=True, lat=lat, lng=lng, label=f"{lat:.4f}, {lng:.4f}",
                               radius_m=radius, source="latlng")
        except (TypeError, ValueError):
            pass

    return GeoLocation(ok=False, reason=_UNRESOLVED, radius_m=radius)
