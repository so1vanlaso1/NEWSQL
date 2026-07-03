"""Resolve the current vs comparison period for a review (plan §13.4 date logic).

Deterministic, no LLM. The window is derived, in priority order, from:

1. an explicit period in the question ("tháng 3/2025", "quý 1 2025", "năm 2024"),
2. a ``YYYY-MM`` filter carried on the ReviewSeed (Flow B reuses the source query's period),
3. otherwise the last *full* month in the data window vs the month before it.

The comparison period is the immediately preceding period of the same granularity
(previous month / quarter / year). Both the planner (as a suggestion) and the fallback
pack consume the result, so a review always has a concrete, valid date range even with
the LLM disabled.
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Optional

from backend.analysis.models import DateWindow, ReviewSeed
from backend.common.vn_text import normalize_vietnamese_text

_MONTH_YEAR = re.compile(r"thang\s*(\d{1,2})\s*[/-]\s*(\d{4})")
# "tháng 5 năm 2025" and the "năm"-less "tháng 5 2025" (year immediately after the month).
_MONTH_NAM_YEAR = re.compile(r"thang\s*(\d{1,2})\s+(?:nam\s+)?(\d{4})")
_MONTH_ONLY = re.compile(r"thang\s*(\d{1,2})(?!\s*[/-]?\s*\d)")
_QUARTER = re.compile(r"quy\s*([1-4])(?:\s*(?:nam\s*)?(\d{4}))?")
_YEAR_ONLY = re.compile(r"nam\s*(\d{4})")
_YYYY_MM = re.compile(r"(\d{4})-(\d{2})")


def _parse_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_window(year: int, month: int) -> DateWindow:
    f, t = _month_bounds(year, month)
    py, pm = _prev_month(year, month)
    cf, ct = _month_bounds(py, pm)
    return DateWindow(
        date_from=f.isoformat(), date_to=t.isoformat(),
        compare_from=cf.isoformat(), compare_to=ct.isoformat(),
        label=f"{year:04d}-{month:02d}", compare_label=f"{py:04d}-{pm:02d}")


def _quarter_window(year: int, q: int) -> DateWindow:
    start_month = (q - 1) * 3 + 1
    f, _ = _month_bounds(year, start_month)
    _, t = _month_bounds(year, start_month + 2)
    pq, py = (4, year - 1) if q == 1 else (q - 1, year)
    ps = (pq - 1) * 3 + 1
    cf, _ = _month_bounds(py, ps)
    _, ct = _month_bounds(py, ps + 2)
    return DateWindow(
        date_from=f.isoformat(), date_to=t.isoformat(),
        compare_from=cf.isoformat(), compare_to=ct.isoformat(),
        label=f"Q{q}/{year}", compare_label=f"Q{pq}/{py}")


def _year_window(year: int) -> DateWindow:
    return DateWindow(
        date_from=date(year, 1, 1).isoformat(), date_to=date(year, 12, 31).isoformat(),
        compare_from=date(year - 1, 1, 1).isoformat(),
        compare_to=date(year - 1, 12, 31).isoformat(),
        label=str(year), compare_label=str(year - 1))


def _default_year_for_month(month: int, data_min: date, data_max: date) -> int:
    """Most recent year whose ``month`` starts within the data window (so 'tháng 3'
    resolves to the latest available March, and a month past the window falls back a year)."""
    for year in range(data_max.year, data_min.year - 1, -1):
        start, _ = _month_bounds(year, month)
        if data_min <= start <= data_max:
            return year
    return data_max.year


def _last_full_month(data_max: date) -> DateWindow:
    """The last *complete* month at or before ``data_max`` (an incomplete trailing month
    like 2025-06-24 steps back to the previous full month)."""
    last_day = calendar.monthrange(data_max.year, data_max.month)[1]
    if data_max.day >= last_day:
        y, m = data_max.year, data_max.month
    else:
        y, m = _prev_month(data_max.year, data_max.month)
    return _month_window(y, m)


def resolve_window(question: str, seed: Optional[ReviewSeed],
                   data_min: str, data_max: str) -> DateWindow:
    dmin = _parse_date(data_min) or date(2024, 1, 1)
    dmax = _parse_date(data_max) or date.today()
    text = normalize_vietnamese_text(question)

    m = _MONTH_YEAR.search(text) or _MONTH_NAM_YEAR.search(text)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return _month_window(year, month)

    m = _QUARTER.search(text)
    if m:
        q = int(m.group(1))
        year = int(m.group(2)) if m.group(2) else _default_year_for_month((q - 1) * 3 + 1, dmin, dmax)
        return _quarter_window(year, q)

    m = _MONTH_ONLY.search(text)
    if m:
        month = int(m.group(1))
        if 1 <= month <= 12:
            return _month_window(_default_year_for_month(month, dmin, dmax), month)

    m = _YEAR_ONLY.search(text)
    if m:
        return _year_window(int(m.group(1)))

    # Seed filter (Flow B): reuse the source query's YYYY-MM period when present.
    if seed is not None and seed.ok:
        for f in seed.base_filters:
            ym = _YYYY_MM.search(str(f))
            if ym:
                return _month_window(int(ym.group(1)), int(ym.group(2)))

    return _last_full_month(dmax)
