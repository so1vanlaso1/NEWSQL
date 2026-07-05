from backend.analysis import writer
from backend.analysis.models import AdvisorOutput, ChartSpec, ChartSeries, EvidenceItem


def test_writer_without_client_returns_skeleton_report():
    evidence = [
        EvidenceItem(
            evidence_id="ev1", title="Doanh thu kỳ này so với kỳ trước",
            metric="doanh_thu", status="success",
            columns=["ky", "gia_tri"],
            rows=[{"ky": "ky_nay", "gia_tri": 880}, {"ky": "ky_truoc", "gia_tri": 1000}],
            profile={"shape": "kpi", "current": 880, "previous": 1000, "trend": "down",
                     "absolute_change": -120, "pct_change": -12.0, "value_field": "gia_tri"},
        )
    ]
    charts = [
        ChartSpec(chart_id="c1", type="grouped_bar", title="Doanh thu",
                  evidence_id="ev1", x_field="ky",
                  series=[ChartSeries(name="gia_tri", value_field="gia_tri")],
                  data=evidence[0].rows, unit="VND")
    ]
    advice = AdvisorOutput(
        driver_summary="Doanh thu giảm 12%.",
        interpretation_bullets=["Có thể do mất khách."],
        improvement_bullets=["Rà soát khách hàng giảm mua."],
        next_questions=["Phân tích sâu theo sản phẩm"],
    )

    events = list(writer.stream_report(
        client=None, title="Phân tích doanh thu", question="Vì sao doanh thu giảm?",
        evidence=evidence, charts=charts, advice=advice, caveats=["Chỉ tính đơn NORMAL."],
    ))

    assert len(events) == 1
    kind, result = events[0]
    assert kind == "done"
    assert result.used_fallback is True
    assert "Báo cáo rút gọn" in result.report_markdown
    assert "Doanh thu kỳ này" in result.report_markdown
    assert "Phân tích sâu theo sản phẩm" in result.report_markdown

