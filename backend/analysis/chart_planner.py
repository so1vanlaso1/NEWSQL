"""Deterministic chart specs from ``chart_rule`` entries (plan §17.1-17.2). No LLM.

Each evidence item's ``kind`` (a chart shape: kpi_comparison | composition | trend | top_n)
is looked up in the KB's ``chart_rule`` entries — which are policy, hot-reloaded, and
owner-editable — to pick a chart type and its caps. Editing a chart_rule changes the next
review's chart with no restart. Only aggregated/profiled rows are charted, capped at
``ANALYTIC_CHART_MAX_POINTS``; unsuitable data (too few rows, too many categories, a "none"
rule) yields no chart and the table still ships.
"""
from __future__ import annotations

from backend import config
from backend.analysis.evidence import is_money
from backend.analysis.models import ChartSeries, ChartSpec, EvidenceItem

# Defaults used when a chart_rule entry for a shape is missing (KB not seeded).
_DEFAULT_RULES = {
    "kpi_comparison": {"chart_type": "grouped_bar", "max_categories": 2, "min_rows": 2},
    "trend": {"chart_type": "line", "max_categories": 36, "min_rows": 2},
    "top_n": {"chart_type": "horizontal_bar", "max_categories": 12, "min_rows": 2},
    "composition": {"chart_type": "stacked_bar", "max_categories": 12, "min_rows": 2},
    "raw": {"chart_type": "none", "max_categories": 0, "min_rows": 0},
}


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


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


def _rules_by_shape(chart_rules: list[dict]) -> dict[str, dict]:
    out = dict(_DEFAULT_RULES)
    for r in chart_rules or []:
        shape = r.get("shape")
        if shape:
            out[shape] = r
    return out


def _unit(metric: str, value_fields: list[str]) -> str:
    return "VND" if any(is_money(metric, f) for f in value_fields) else ""


def plan_chart(ev: EvidenceItem, rules_by_shape: dict[str, dict], next_id: int) -> ChartSpec | None:
    """One chart spec for one evidence item, or None when no chart is warranted."""
    if ev.status != "success" or not ev.rows:
        return None
    rule = rules_by_shape.get(ev.kind) or _DEFAULT_RULES.get(ev.kind)
    if not rule:
        return None
    chart_type = rule.get("chart_type", "none")
    if chart_type in ("none", None) or ev.kind == "raw":
        return None

    columns = ev.columns
    numeric = _numeric_columns(columns, ev.rows)
    if not numeric:
        return None
    label_col = _label_column(columns, numeric)
    max_categories = int(rule.get("max_categories", 12) or 12)
    min_rows = int(rule.get("min_rows", 2) or 0)

    if len(ev.rows) < min_rows:
        return None

    # Series: comparison shapes plot every numeric column; single-value shapes plot the first.
    if ev.kind in ("composition",):
        value_fields = numeric
    else:
        value_fields = numeric[:1]
    series = [ChartSeries(name=f, value_field=f) for f in value_fields]

    limit = min(max_categories or config.ANALYTIC_CHART_MAX_POINTS, config.ANALYTIC_CHART_MAX_POINTS)
    rows = ev.rows[:limit] if ev.kind in ("top_n", "composition") else ev.rows[:config.ANALYTIC_CHART_MAX_POINTS]
    # Guard: too many categories for a categorical chart -> table only.
    if ev.kind in ("top_n", "composition") and len(ev.rows) > max_categories and max_categories:
        rows = ev.rows[:max_categories]

    data = [{label_col: r.get(label_col), **{f: r.get(f) for f in value_fields}} for r in rows]
    chart_id = f"c{next_id}"
    spec = ChartSpec(
        chart_id=chart_id, type=chart_type, title=ev.title,
        x_field=label_col, series=series, data=data,
        unit=_unit(ev.metric, value_fields), evidence_id=ev.evidence_id,
        notes=rule.get("notes", ""))
    return spec


def plan_charts(evidence: list[EvidenceItem], chart_rules: list[dict]) -> list[ChartSpec]:
    """Build chart specs for all chartable evidence and link each chart to its evidence.

    Mutates ``evidence[i].chart_id`` so the evidence knows its chart (plan §15.2). Returns
    the chart specs in evidence order.
    """
    rules_by_shape = _rules_by_shape(chart_rules)
    charts: list[ChartSpec] = []
    n = 0
    for ev in evidence:
        n_try = n + 1
        spec = plan_chart(ev, rules_by_shape, n_try)
        if spec is not None:
            n = n_try
            ev.chart_id = spec.chart_id
            charts.append(spec)
    return charts
