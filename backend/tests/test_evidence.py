"""Phase 14: evidence construction + metric-aware money formatting (plan §15.2)."""
from backend.analysis import evidence
from backend.analysis.models import TaskResult


def test_is_money_decided_by_metric_first():
    assert evidence.is_money("doanh_thu", "gia_tri") is True
    assert evidence.is_money("so_don_hang", "gia_tri") is False   # count metric, generic alias
    assert evidence.is_money("so_khach_hang", "gia_tri") is False
    assert evidence.is_money("", "doanh_thu") is True             # money-named field
    assert evidence.is_money("", "gia_tri") is False              # ambiguous alias -> no unit


def _tr(metric, rows):
    return TaskResult(task_id="t1", title="Chỉ số", expected_shape="kpi", metric=metric,
                      status="success", columns=["ky", "gia_tri"], rows=rows,
                      row_count=len(rows), sql="SELECT 1")


def test_build_evidence_carries_metric_and_caps_rows():
    tr = _tr("doanh_thu", [{"ky": "ky_nay", "gia_tri": 820}, {"ky": "ky_truoc", "gia_tri": 1040}])
    ev = evidence.build_evidence("ev1", "rv1", tr, {"shape": "kpi", "current": 820})
    assert ev.metric == "doanh_thu"
    assert ev.kind == "kpi_comparison"
    assert ev.source_type == "sql"


def test_profile_sentence_currency_follows_metric():
    from backend.analysis import profiler
    rev_rows = [{"ky": "ky_nay", "gia_tri": 820}, {"ky": "ky_truoc", "gia_tri": 1040}]
    cnt_rows = [{"ky": "ky_nay", "gia_tri": 39}, {"ky": "ky_truoc", "gia_tri": 40}]

    rev = evidence.build_evidence("e1", "rv", _tr("doanh_thu", rev_rows),
                                  profiler.profile("kpi", ["ky", "gia_tri"], rev_rows))
    cnt = evidence.build_evidence("e2", "rv", _tr("so_don_hang", cnt_rows),
                                  profiler.profile("kpi", ["ky", "gia_tri"], cnt_rows))
    assert "₫" in evidence.profile_sentence(rev)
    assert "₫" not in evidence.profile_sentence(cnt)


def test_failed_task_sentence_is_safe():
    tr = TaskResult(task_id="t", title="Hỏng", status="failed", error="boom")
    ev = evidence.build_evidence("e", "rv", tr, {})
    assert "không chạy được" in evidence.profile_sentence(ev)
