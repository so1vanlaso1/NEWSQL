from types import SimpleNamespace

from backend.analysis import followup
from backend.analysis.models import ChartSpec, EvidenceItem, ReviewRecord


class _Store:
    def save_non_sql_turn(self, *args, **kwargs):
        return SimpleNamespace(turn_id=kwargs.get("turn_id") or "turn1")


def _review():
    ev = EvidenceItem(
        evidence_id="ev1", review_id="rv1", task_id="t1",
        title="Doanh thu theo vùng", purpose="Tìm vùng kéo giảm",
        metric="doanh_thu", source_type="sql", status="success",
        sql="SELECT v.ten_vung AS nhom, SUM(ct.thanh_tien) AS ky_nay FROM ...",
        columns=["nhom", "ky_nay", "ky_truoc"],
        rows=[{"nhom": "Miền Trung", "ky_nay": 100, "ky_truoc": 220}],
        profile={
            "shape": "by_dimension",
            "biggest_mover": {"label": "Miền Trung", "change": -120},
            "top3_concentration": 0.8,
        },
    )
    return ReviewRecord(
        review_id="rv1", conversation_id="c1", turn_id="t0",
        mode="ANALYTIC_MODE", question="Vì sao doanh thu giảm?",
        findings_summary="Miền Trung kéo giảm mạnh.",
        report_markdown="## Báo cáo", evidence=[ev],
        charts=[ChartSpec(chart_id="c1", type="stacked_bar", title="Theo vùng",
                          evidence_id="ev1", x_field="nhom", data=ev.rows)],
        caveats=["Chỉ tính đơn NORMAL."],
        follow_up_suggestions=["Cho xem SQL đã dùng"],
    )


def _final(events):
    finals = [e for e in events if e.get("type") == "final"]
    assert finals
    return finals[0]["response"]


def test_show_sql_special_never_needs_llm():
    events = list(followup.handle_followup(
        message="Cho xem SQL đã dùng", conversation_id="c1",
        review=_review(), store=_Store(), client=None))
    resp = _final(events)

    assert resp["mode"] == "ANALYTIC_FOLLOWUP"
    assert resp["review_id"] == "rv1"
    assert "```sql" in resp["report_markdown"]
    assert resp["evidence"][0]["evidence_id"] == "ev1"


def test_keyword_fallback_answers_from_matching_evidence():
    events = list(followup.handle_followup(
        message="Vì sao Miền Trung giảm mạnh nhất?", conversation_id="c1",
        review=_review(), store=_Store(), client=None))
    resp = _final(events)

    assert "Miền Trung" in resp["answer"]
    assert resp["needs_sql"] is False
    assert resp["evidence"][0]["evidence_id"] == "ev1"

