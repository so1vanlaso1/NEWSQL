"""Turn raw task rows into compact profiles (plan §15.1). Pure functions, no LLM.

Each ``expected_shape`` gets a profile the advisor (Phase 15) and writer read structurally:

    kpi           current, previous, absolute_change, pct_change, trend
    by_dimension  per-row change, top +/- contributors, top-3 concentration, biggest mover
    trend         direction, best/worst period, last-vs-first change
    top_n         ranking, leader share, gap to #2

Every profile carries a ``warnings`` list (empty result, divide-by-zero, all-null) so the
report can flag a shaky number instead of stating it flatly.
"""
from __future__ import annotations

from typing import Optional


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def pct_change(current, previous) -> Optional[float]:
    """Percent change, or None when the base is 0/None (plan §15.1)."""
    if previous in (0, None) or current is None:
        return None
    return round((current - previous) / previous * 100, 2)


def _numeric_columns(columns: list[str], rows: list[dict]) -> list[str]:
    out: list[str] = []
    for c in columns:
        nonnull = [r.get(c) for r in rows if r.get(c) is not None]
        if nonnull and all(_is_number(v) for v in nonnull):
            out.append(c)
    return out


def _label_column(columns: list[str], numeric: list[str]) -> str:
    for c in columns:
        if c not in numeric:
            return c
    return columns[0] if columns else ""


def _num(v) -> float:
    return float(v) if _is_number(v) else 0.0


def _kpi(columns: list[str], rows: list[dict]) -> dict:
    numeric = _numeric_columns(columns, rows)
    value_col = numeric[0] if numeric else (columns[-1] if columns else "")
    warnings: list[str] = []

    def _row_value(pred) -> Optional[float]:
        for r in rows:
            if pred(r):
                v = r.get(value_col)
                return _num(v) if _is_number(v) else None
        return None

    label_col = _label_column(columns, numeric)
    cur = prev = None
    if label_col:
        cur = _row_value(lambda r: "nay" in str(r.get(label_col, "")).lower())
        prev = _row_value(lambda r: "truoc" in str(r.get(label_col, "")).lower())
    if cur is None and prev is None and len(rows) >= 2:
        cur = _num(rows[0].get(value_col)) if _is_number(rows[0].get(value_col)) else None
        prev = _num(rows[1].get(value_col)) if _is_number(rows[1].get(value_col)) else None
    elif cur is None and len(rows) == 1:
        cur = _num(rows[0].get(value_col)) if _is_number(rows[0].get(value_col)) else None

    change = (cur - prev) if (cur is not None and prev is not None) else None
    pct = pct_change(cur, prev)
    if prev in (0, None):
        warnings.append("no_previous_baseline")
    if cur is None:
        warnings.append("missing_current_value")
    trend = "flat"
    if change is not None:
        trend = "down" if change < 0 else "up" if change > 0 else "flat"
    return {
        "shape": "kpi", "value_field": value_col,
        "current": cur, "previous": prev,
        "absolute_change": change, "pct_change": pct,
        "trend": trend, "warnings": warnings,
    }


def _contribution(rows: list[dict], label_col: str, cur_col: str,
                  prev_col: Optional[str]) -> dict:
    items = []
    for r in rows:
        cur = _num(r.get(cur_col))
        prev = _num(r.get(prev_col)) if prev_col else 0.0
        items.append({
            "label": r.get(label_col), "current": cur, "previous": prev,
            "change": round(cur - prev, 4) if prev_col else cur,
        })
    total_current = round(sum(i["current"] for i in items), 4)
    total_previous = round(sum(i["previous"] for i in items), 4) if prev_col else None
    total_change = round(sum(i["change"] for i in items), 4)

    by_change = sorted(items, key=lambda x: x["change"])
    top_negative = [i for i in by_change if i["change"] < 0][:3]
    top_positive = [i for i in reversed(by_change) if i["change"] > 0][:3]
    by_abs = sorted(items, key=lambda x: abs(x["change"]), reverse=True)
    biggest_mover = by_abs[0] if by_abs else None
    top3_abs = sum(abs(i["change"]) for i in by_abs[:3])
    total_abs = sum(abs(i["change"]) for i in items)
    concentration = round(top3_abs / total_abs, 4) if total_abs else None

    # Share of the current-period total held by the top row (for single-period ranking).
    ranked = sorted(items, key=lambda x: x["current"], reverse=True)
    leader_share = round(ranked[0]["current"] / total_current, 4) if (ranked and total_current) else None
    return {
        "label_field": label_col, "current_field": cur_col,
        "previous_field": prev_col or "",
        "total_current": total_current, "total_previous": total_previous,
        "total_change": total_change,
        "top_positive": top_positive, "top_negative": top_negative,
        "biggest_mover": biggest_mover,
        "top3_concentration": concentration, "leader_share": leader_share,
        "n_groups": len(items),
    }


def _by_dimension(columns: list[str], rows: list[dict]) -> dict:
    numeric = _numeric_columns(columns, rows)
    label_col = _label_column(columns, numeric)
    warnings: list[str] = []
    cur_col = "ky_nay" if "ky_nay" in numeric else (numeric[0] if numeric else "")
    prev_col = None
    if "ky_truoc" in numeric:
        prev_col = "ky_truoc"
    elif len(numeric) >= 2 and numeric[0] == cur_col:
        prev_col = numeric[1]
    if not cur_col:
        warnings.append("no_numeric_column")
        return {"shape": "by_dimension", "warnings": warnings}
    prof = _contribution(rows, label_col, cur_col, prev_col)
    # top3_concentration is None only when every group's change is zero (total_abs == 0).
    if prof.get("top3_concentration") is None:
        warnings.append("no_change")
    prof.update({"shape": "by_dimension", "warnings": warnings})
    return prof


def _trend(columns: list[str], rows: list[dict]) -> dict:
    numeric = _numeric_columns(columns, rows)
    label_col = _label_column(columns, numeric)
    value_col = numeric[0] if numeric else (columns[-1] if columns else "")
    warnings: list[str] = []
    series = [{"period": r.get(label_col), "value": _num(r.get(value_col))} for r in rows]
    if not series:
        return {"shape": "trend", "warnings": ["empty_result"], "value_field": value_col}
    first, last = series[0]["value"], series[-1]["value"]
    change = round(last - first, 4)
    best = max(series, key=lambda s: s["value"])
    worst = min(series, key=lambda s: s["value"])
    direction = "down" if change < 0 else "up" if change > 0 else "flat"
    return {
        "shape": "trend", "value_field": value_col, "label_field": label_col,
        "direction": direction, "first": first, "last": last,
        "absolute_change": change, "pct_change": pct_change(last, first),
        "best_period": best, "worst_period": worst,
        "n_periods": len(series), "warnings": warnings,
    }


def _top_n(columns: list[str], rows: list[dict]) -> dict:
    numeric = _numeric_columns(columns, rows)
    label_col = _label_column(columns, numeric)
    value_col = numeric[0] if numeric else (columns[-1] if columns else "")
    ranking = [{"rank": i + 1, "label": r.get(label_col), "value": _num(r.get(value_col))}
               for i, r in enumerate(rows)]
    if not ranking:
        return {"shape": "top_n", "warnings": ["empty_result"], "value_field": value_col}
    total = round(sum(x["value"] for x in ranking), 4)
    leader = ranking[0]
    second = ranking[1]["value"] if len(ranking) > 1 else 0.0
    warnings = ["zero_total"] if not total else []
    return {
        "shape": "top_n", "value_field": value_col, "label_field": label_col,
        "leader": leader["label"], "leader_value": leader["value"],
        "leader_share": round(leader["value"] / total, 4) if total else None,
        "gap_to_second": round(leader["value"] - second, 4),
        "total": total, "n": len(ranking),
        "ranking": ranking[:10], "warnings": warnings,
    }


_DISPATCH = {"kpi": _kpi, "by_dimension": _by_dimension, "trend": _trend, "top_n": _top_n}


def profile(expected_shape: str, columns: list[str], rows: list[dict]) -> dict:
    """Profile a task result by its expected shape (never raises)."""
    columns = columns or []
    rows = rows or []
    if not rows:
        return {"shape": expected_shape, "warnings": ["empty_result"]}
    # A shape whose value columns are entirely null is not analyzable.
    numeric = _numeric_columns(columns, rows)
    if columns and not numeric and expected_shape in ("kpi", "trend", "top_n"):
        return {"shape": expected_shape, "warnings": ["all_null_or_non_numeric"]}
    fn = _DISPATCH.get(expected_shape, _kpi)
    try:
        return fn(columns, rows)
    except Exception:  # noqa: BLE001 - profiling must never break a review
        return {"shape": expected_shape, "warnings": ["profile_error"]}
