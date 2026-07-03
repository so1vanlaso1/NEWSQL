"""Phase 14: deterministic chart planner (plan §17.1-17.2)."""
from backend.analysis import chart_planner
from backend.analysis.models import EvidenceItem


def _ev(kind, columns, rows, eid="ev1", metric=""):
    return EvidenceItem(evidence_id=eid, kind=kind, source_type="sql", title="T",
                        metric=metric, columns=columns, rows=rows, status="success")


def test_kpi_comparison_makes_grouped_bar():
    ev = _ev("kpi_comparison", ["ky", "gia_tri"],
             [{"ky": "ky_nay", "gia_tri": 820}, {"ky": "ky_truoc", "gia_tri": 1040}],
             metric="doanh_thu")
    charts = chart_planner.plan_charts([ev], [])
    assert len(charts) == 1
    c = charts[0]
    assert c.type == "grouped_bar"
    assert c.x_field == "ky"
    assert [s.value_field for s in c.series] == ["gia_tri"]
    assert c.unit == "VND"                 # money metric -> VND despite the generic alias
    assert ev.chart_id == c.chart_id       # evidence linked back to its chart


def test_count_metric_kpi_has_no_currency_unit():
    # The KPI alias is always 'gia_tri'; money-ness is decided by the metric, so a count
    # metric (so_don_hang) must NOT get a VND unit.
    ev = _ev("kpi_comparison", ["ky", "gia_tri"],
             [{"ky": "ky_nay", "gia_tri": 39}, {"ky": "ky_truoc", "gia_tri": 40}],
             metric="so_don_hang")
    c = chart_planner.plan_charts([ev], [])[0]
    assert c.unit == ""


def test_trend_makes_line():
    ev = _ev("trend", ["thang", "gia_tri"],
             [{"thang": "2025-01", "gia_tri": 1}, {"thang": "2025-02", "gia_tri": 2}])
    charts = chart_planner.plan_charts([ev], [])
    assert charts[0].type == "line"


def test_top_n_makes_horizontal_bar_capped_to_max_categories():
    rows = [{"ten": f"E{i}", "gia_tri": 100 - i} for i in range(15)]
    ev = _ev("top_n", ["ten", "gia_tri"], rows)
    charts = chart_planner.plan_charts([ev], [])
    assert charts[0].type == "horizontal_bar"
    assert len(charts[0].data) == 12       # default max_categories for top_n


def test_composition_stacks_both_periods():
    ev = _ev("composition", ["nhom", "ky_nay", "ky_truoc"],
             [{"nhom": "A", "ky_nay": 10, "ky_truoc": 20},
              {"nhom": "B", "ky_nay": 30, "ky_truoc": 25}])
    charts = chart_planner.plan_charts([ev], [])
    c = charts[0]
    assert c.type == "stacked_bar"
    assert [s.value_field for s in c.series] == ["ky_nay", "ky_truoc"]


def test_raw_kind_yields_no_chart():
    ev = _ev("raw", ["a", "b"], [{"a": 1, "b": 2}, {"a": 3, "b": 4}])
    assert chart_planner.plan_charts([ev], []) == []


def test_failed_or_empty_evidence_has_no_chart():
    ev = EvidenceItem(evidence_id="e", kind="kpi_comparison", status="failed",
                      columns=["ky", "gia_tri"], rows=[])
    assert chart_planner.plan_charts([ev], []) == []


def test_chart_rule_edit_is_live():
    # Editing the chart_rule for a shape changes the next review's chart with no restart.
    ev = _ev("trend", ["thang", "gia_tri"],
             [{"thang": "2025-01", "gia_tri": 1}, {"thang": "2025-02", "gia_tri": 2}])
    off = chart_planner.plan_charts([ev], [{"shape": "trend", "chart_type": "none"}])
    assert off == []
    ev2 = _ev("trend", ["thang", "gia_tri"],
              [{"thang": "2025-01", "gia_tri": 1}, {"thang": "2025-02", "gia_tri": 2}])
    on = chart_planner.plan_charts([ev2], [{"shape": "trend", "chart_type": "line"}])
    assert on[0].type == "line"


def test_min_rows_guard_skips_single_row():
    ev = _ev("trend", ["thang", "gia_tri"], [{"thang": "2025-01", "gia_tri": 1}])
    assert chart_planner.plan_charts([ev], []) == []
