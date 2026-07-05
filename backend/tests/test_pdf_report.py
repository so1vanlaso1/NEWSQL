"""PDF export of a persisted review (backend/analysis/pdf_report.py + the /pdf endpoint).

Builds a rich ReviewRecord (markdown with a GFM table, every chart type, SQL + web evidence,
sources, caveats, follow-ups) and asserts the renderer produces a valid PDF without raising,
that fonts degrade gracefully, and that the read endpoint returns application/pdf (404 when
the review is absent). No LLM, no network — pure rendering.
"""
import pytest
from fastapi import HTTPException

from backend.analysis import pdf_report
from backend.analysis.models import (
    ChartSeries,
    ChartSpec,
    EvidenceItem,
    ReviewPlan,
    ReviewRecord,
)


def _record() -> ReviewRecord:
    charts = [
        ChartSpec(chart_id="c1", type="grouped_bar", title="Doanh thu theo tháng",
                  x_field="thang", unit="VND",
                  series=[ChartSeries(name="Kỳ này", value_field="cur"),
                          ChartSeries(name="Kỳ trước", value_field="prev")],
                  data=[{"thang": "2025-01", "cur": 1200000, "prev": 1000000},
                        {"thang": "2025-02", "cur": 1500000, "prev": 1300000}],
                  evidence_id="e1"),
        ChartSpec(chart_id="c2", type="line", title="Xu hướng", x_field="ky", unit="",
                  series=[ChartSeries(name="Số đơn", value_field="v")],
                  data=[{"ky": "T1", "v": 10}, {"ky": "T2", "v": 14}, {"ky": "T3", "v": 9}],
                  evidence_id="e2"),
        ChartSpec(chart_id="c3", type="horizontal_bar", title="Top khách hàng",
                  x_field="ten", unit="VND",
                  series=[ChartSeries(name="Doanh thu", value_field="dt")],
                  data=[{"ten": "An Phát", "dt": 900000}, {"ten": "Bình Minh", "dt": 700000}],
                  evidence_id="e3"),
        ChartSpec(chart_id="c4", type="stacked_bar", title="Cơ cấu", x_field="nhom", unit="VND",
                  series=[ChartSeries(name="A", value_field="a"), ChartSeries(name="B", value_field="b")],
                  data=[{"nhom": "X", "a": 5, "b": 3}, {"nhom": "Y", "a": 2, "b": 6}],
                  evidence_id="e4"),
    ]
    evidence = [
        EvidenceItem(evidence_id="e1", review_id="rv1", kind="kpi_comparison", source_type="sql",
                     metric="doanh_thu", title="Doanh thu tổng",
                     columns=["thang", "gia_tri"],
                     rows=[{"thang": "2025-01", "gia_tri": 1200000},
                           {"thang": "2025-02", "gia_tri": 1500000}],
                     sql="SELECT ...", status="success"),
        EvidenceItem(evidence_id="e9", review_id="rv1", kind="raw", source_type="sql",
                     title="Bước lỗi", columns=[], rows=[], status="failed"),
        EvidenceItem(evidence_id="ew", review_id="rv1", kind="web", source_type="web",
                     title="Bài báo thị trường",
                     web={"n": 1, "url": "https://example.com/a", "snippet": "..."},
                     status="success"),
    ]
    report_md = (
        "## Tóm tắt điều hành\n"
        "- Doanh thu **tăng** 20% so với kỳ trước.\n"
        "- Xem chi tiết ở [nguồn](https://example.com/a).\n\n"
        "## Bảng số liệu\n"
        "| Tháng | Doanh thu |\n"
        "| --- | --- |\n"
        "| 2025-01 | 1.200.000 |\n"
        "| 2025-02 | 1.500.000 |\n\n"
        "> Ghi chú: số liệu đã kiểm chứng.\n"
    )
    return ReviewRecord(
        review_id="rv1", conversation_id="cv1", turn_id="t1", mode="ANALYTIC_MODE",
        question="Doanh thu quý này thế nào?",
        plan=ReviewPlan(analysis_title="Phân tích doanh thu quý"),
        report_markdown=report_md, evidence=evidence, charts=charts,
        sources=[{"n": 1, "title": "Bài báo thị trường", "url": "https://example.com/a",
                  "retrieved_at": "2025-07-01"}],
        caveats=["Dữ liệu chỉ tới 2025-06-24."],
        follow_up_suggestions=["Phân tích theo khu vực?"],
        status="complete", created_at="2025-07-03T00:00:00+00:00")


def test_render_produces_valid_pdf():
    pdf = pdf_report.render_review_pdf(_record())
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF"), "output must be a PDF"
    assert pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 3000, "a full report should be more than a stub"


def test_every_chart_type_renders():
    st = pdf_report._styles()
    for c in _record().charts:
        dr = pdf_report._chart_drawing(c, st)
        assert dr is not None, f"chart {c.chart_id} ({c.type}) should render a drawing"


def test_empty_and_none_charts_are_skipped():
    st = pdf_report._styles()
    assert pdf_report._chart_drawing(ChartSpec(chart_id="n", type="none"), st) is None
    assert pdf_report._chart_drawing(
        ChartSpec(chart_id="e", type="grouped_bar",
                  series=[ChartSeries(name="A", value_field="a")], data=[]), st) is None


def test_markdown_table_and_headings_parse():
    st = pdf_report._styles()
    flows = pdf_report._markdown_flowables(
        "## H\n| a | b |\n| - | - |\n| 1 | 2 |\n\n- bullet\n", st)
    from reportlab.platypus import Table
    assert any(isinstance(f, Table) for f in flows), "GFM table must become a reportlab Table"


def test_vietnamese_font_registered_when_available():
    # This host has Arial/Segoe/Tahoma; the resolver must pick a TTF, not fall back to Helvetica.
    pdf_report._FONT = ""
    pdf_report._FONT_BOLD = ""
    font, bold = pdf_report._ensure_fonts()
    assert font != "Helvetica", "a Vietnamese-capable TTF should be resolved on this host"


def test_font_fallback_never_raises(monkeypatch):
    # Simulate a host with no usable TTF anywhere: must fall back to Helvetica and still render.
    monkeypatch.setattr(pdf_report, "_FONT", "")
    monkeypatch.setattr(pdf_report, "_FONT_BOLD", "")
    monkeypatch.setattr(pdf_report, "_REGULAR_CANDIDATES", [])
    monkeypatch.setattr(pdf_report, "_BOLD_CANDIDATES", [])
    font, bold = pdf_report._ensure_fonts()
    assert font == "Helvetica" and bold == "Helvetica-Bold"
    pdf = pdf_report.render_review_pdf(_record())
    assert pdf.startswith(b"%PDF")
    # Restore so a real TTF is used by subsequent tests (module globals are process-wide).
    pdf_report._FONT = ""
    pdf_report._FONT_BOLD = ""


def test_filename_is_ascii_safe():
    rec = ReviewRecord(review_id="rv/../weird id!")
    name = pdf_report.pdf_filename(rec)
    assert name.startswith("bao-cao-") and name.endswith(".pdf")
    assert "/" not in name and " " not in name


def test_pdf_endpoint_returns_pdf_and_404():
    from backend.api import analysis
    from backend.analysis.review_store import get_review_store

    get_review_store().save_review(_record())
    resp = analysis.get_review_pdf("rv1")
    assert resp.media_type == "application/pdf"
    assert resp.body.startswith(b"%PDF")
    assert "attachment" in resp.headers["content-disposition"]

    with pytest.raises(HTTPException) as ei:
        analysis.get_review_pdf("does-not-exist")
    assert ei.value.status_code == 404
