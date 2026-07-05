"""Render a persisted ``ReviewRecord`` to a PDF (analytic + charts + evidence + sources).

This is an on-demand export stage of the review pipeline: the controller already persists a
complete ``ReviewRecord`` (report markdown, chart specs, profiled evidence, web sources,
caveats, follow-ups); this module turns any such record — live or reopened from history —
into a self-contained PDF. It re-renders the charts from their ``ChartSpec`` using ReportLab's
native graphics (no browser, no headless Chromium), so the export runs fully offline.

ReportLab is pure-Python (no GTK/pango system libs), which matters on Windows where WeasyPrint
is painful to install. Following the codebase's fallback-everywhere convention, every step
degrades instead of raising: a missing Vietnamese font falls back to a built-in face, a broken
chart/table is skipped with a note, and ``render_review_pdf`` never throws on report content.
"""
from __future__ import annotations

import html
import io
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.analysis import evidence as evidence_mod
from backend.analysis.models import ChartSpec, EvidenceItem, ReviewRecord

# Chart palette mirrors the frontend ChartRenderer so a PDF chart matches the on-screen one.
_PALETTE = ["#4c8bf5", "#2ecc71", "#f1c40f", "#e67e22", "#9b59b6", "#1abc9c"]
_INK = colors.HexColor("#1f2733")
_MUTED = colors.HexColor("#5b6472")
_RULE = colors.HexColor("#d7deea")
_HEAD_BG = colors.HexColor("#eef2fb")
_LINK = colors.HexColor("#2563eb")

# Registered once; the resolved family name is reused for every style. "" until _ensure_fonts runs.
_FONT = ""
_FONT_BOLD = ""

# TTF search order: a repo-bundled font first (portable), then common Windows / Linux / macOS
# faces that cover Vietnamese diacritics. Helvetica (built-in) is the last-resort fallback and
# does NOT render Vietnamese well — bundle a font under backend/assets/fonts to guarantee it.
_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_REGULAR_CANDIDATES = [
    _ASSET_DIR / "NotoSans-Regular.ttf",
    _ASSET_DIR / "DejaVuSans.ttf",
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/tahoma.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
]
_BOLD_CANDIDATES = [
    _ASSET_DIR / "NotoSans-Bold.ttf",
    _ASSET_DIR / "DejaVuSans-Bold.ttf",
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("C:/Windows/Fonts/tahomabd.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    Path("/Library/Fonts/Arial Bold.ttf"),
]


def _first_existing(candidates: list[Path]) -> Optional[Path]:
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _ensure_fonts() -> tuple[str, str]:
    """Register a Vietnamese-capable TTF (regular + bold) once; return (regular, bold) names.

    Falls back to ReportLab's built-in Helvetica if no TTF is found — the PDF still renders,
    only the Vietnamese diacritics may be missing glyphs. Registration failures never raise.
    """
    global _FONT, _FONT_BOLD
    if _FONT:
        return _FONT, _FONT_BOLD
    regular = _first_existing(_REGULAR_CANDIDATES)
    if regular is None:
        _FONT, _FONT_BOLD = "Helvetica", "Helvetica-Bold"
        return _FONT, _FONT_BOLD
    try:
        pdfmetrics.registerFont(TTFont("ReportBody", str(regular)))
        name = "ReportBody"
        bold = _first_existing(_BOLD_CANDIDATES)
        if bold is not None:
            pdfmetrics.registerFont(TTFont("ReportBody-Bold", str(bold)))
            bold_name = "ReportBody-Bold"
        else:
            # Only a regular face available: reuse it for bold roles (headings still read).
            bold_name = name
        pdfmetrics.registerFontFamily(name, normal=name, bold=bold_name)
        _FONT, _FONT_BOLD = name, bold_name
    except Exception:  # noqa: BLE001 - a bad TTF must not break export
        _FONT, _FONT_BOLD = "Helvetica", "Helvetica-Bold"
    return _FONT, _FONT_BOLD


# ---- styles ----------------------------------------------------------------
def _styles() -> dict[str, ParagraphStyle]:
    font, bold = _ensure_fonts()
    base = getSampleStyleSheet()["Normal"]
    mk = lambda **kw: ParagraphStyle(parent=base, **{"fontName": font, "textColor": _INK, **kw})
    return {
        "title": mk(name="rTitle", fontName=bold, fontSize=18, leading=23, spaceAfter=2),
        "sub": mk(name="rSub", fontSize=9.5, leading=13, textColor=_MUTED, spaceAfter=1),
        "h2": mk(name="rH2", fontName=bold, fontSize=13.5, leading=18, spaceBefore=12, spaceAfter=4),
        "h3": mk(name="rH3", fontName=bold, fontSize=11.5, leading=15, spaceBefore=8, spaceAfter=3),
        "body": mk(name="rBody", fontSize=10, leading=15, spaceAfter=4, alignment=TA_LEFT),
        "bullet": mk(name="rBullet", fontSize=10, leading=15, leftIndent=14, spaceAfter=2,
                     bulletIndent=2),
        "note": mk(name="rNote", fontSize=9.5, leading=13, textColor=_MUTED, leftIndent=8,
                   borderPadding=(2, 2, 2, 6)),
        "caption": mk(name="rCap", fontName=bold, fontSize=10, leading=13, spaceBefore=6, spaceAfter=3),
        "cell": mk(name="rCell", fontSize=8.5, leading=11),
        "cellhead": mk(name="rCellH", fontName=bold, fontSize=8.5, leading=11, textColor=colors.white),
        "source": mk(name="rSrc", fontSize=9, leading=13, leftIndent=14, spaceAfter=2),
    }


# ---- inline + block markdown ----------------------------------------------
def _inline(text: str) -> str:
    """Escape XML then re-apply the small markdown subset ReportLab paragraphs understand."""
    s = html.escape(str(text or ""), quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        lambda m: f'<link href="{html.escape(m.group(2), quote=True)}" color="#2563eb"><u>{m.group(1)}</u></link>',
        s,
    )
    return s


_SEP_RE = re.compile(r"^\s*:?-{2,}:?\s*$")


def _split_table_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def _is_table_sep(line: str) -> bool:
    return "|" in line and all(_SEP_RE.match(c) or c == "" for c in _split_table_row(line))


def _table_flowable(rows: list[list[str]], st: dict) -> Optional[Table]:
    if not rows:
        return None
    header, body = rows[0], rows[1:]
    ncols = max(len(r) for r in rows)
    header = header + [""] * (ncols - len(header))
    data = [[Paragraph(_inline(c), st["cellhead"]) for c in header]]
    for r in body:
        r = r + [""] * (ncols - len(r))
        data.append([Paragraph(_inline(c), st["cell"]) for c in r])
    tbl = Table(data, repeatRows=1, hAlign="LEFT")
    tbl.setStyle(_table_style(header_rows=1))
    return tbl


def _markdown_flowables(md: str, st: dict) -> list:
    """A constrained markdown renderer: headings, bullets, numbered lists, blockquotes,
    GFM tables and paragraphs. Anything else falls through as a plain paragraph line."""
    out: list = []
    para: list[str] = []
    table: list[str] = []

    def flush_para():
        if para:
            out.append(Paragraph(_inline(" ".join(para)), st["body"]))
            para.clear()

    def flush_table():
        if not table:
            return
        parsed = [_split_table_row(l) for l in table if not _is_table_sep(l)]
        tbl = _table_flowable(parsed, st)
        if tbl is not None:
            out.append(Spacer(1, 2))
            out.append(tbl)
            out.append(Spacer(1, 4))
        table.clear()

    for raw in (md or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        is_table_line = "|" in stripped and stripped.count("|") >= 1 and not stripped.startswith("#")
        if is_table_line:
            flush_para()
            table.append(line)
            continue
        flush_table()

        if not stripped:
            flush_para()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush_para()
            level = len(m.group(1))
            out.append(Paragraph(_inline(m.group(2)), st["h2"] if level <= 2 else st["h3"]))
            continue
        m = re.match(r"^>\s?(.*)$", stripped)
        if m:
            flush_para()
            out.append(Paragraph(_inline(m.group(1)), st["note"]))
            continue
        m = re.match(r"^[-*+]\s+(.*)$", stripped)
        if m:
            flush_para()
            out.append(Paragraph("• " + _inline(m.group(1)), st["bullet"]))
            continue
        m = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if m:
            flush_para()
            out.append(Paragraph(f"{m.group(1)}. " + _inline(m.group(2)), st["bullet"]))
            continue
        para.append(stripped)

    flush_table()
    flush_para()
    return out


# ---- tables ----------------------------------------------------------------
def _table_style(header_rows: int = 1) -> TableStyle:
    cmds = [
        ("GRID", (0, 0), (-1, -1), 0.4, _RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [colors.white, colors.HexColor("#f6f8fc")]),
    ]
    if header_rows:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, header_rows - 1), colors.HexColor("#3f5a8a")),
            ("LINEBELOW", (0, header_rows - 1), (-1, header_rows - 1), 0.6, _RULE),
        ]
    return TableStyle(cmds)


def _fmt_cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return evidence_mod._fmt_number(v)
    return str(v)


def _evidence_table(ev: EvidenceItem, st: dict) -> list:
    """One profiled SQL evidence item: title, purpose, then its capped result table."""
    out: list = [Paragraph(_inline(ev.title or ev.evidence_id or "Bằng chứng"), st["caption"])]
    if ev.status != "success":
        out.append(Paragraph(_inline(f"Trạng thái: {ev.status}"), st["sub"]))
    if ev.purpose:
        out.append(Paragraph(_inline(ev.purpose), st["sub"]))
    cols = list(ev.columns or [])
    rows = list(ev.rows or [])
    if cols and rows:
        data = [[Paragraph(_inline(str(c)), st["cellhead"]) for c in cols]]
        for r in rows:
            data.append([Paragraph(_inline(_fmt_cell(r.get(c))), st["cell"]) for c in cols])
        tbl = Table(data, repeatRows=1, hAlign="LEFT")
        tbl.setStyle(_table_style(header_rows=1))
        out.append(tbl)
    else:
        out.append(Paragraph(_inline("Không có dữ liệu cho bước này."), st["sub"]))
    out.append(Spacer(1, 6))
    return out


# ---- charts ----------------------------------------------------------------
def _num(v):
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _axis_formatter(unit: str):
    def fmt(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        s = f"{int(f):,}".replace(",", ".") if f == int(f) else f"{f:,.1f}".replace(",", ".")
        return f"{s} ₫" if unit == "VND" else s
    return fmt


def _legend(names: list[str], font: str) -> Legend:
    lg = Legend()
    lg.fontName = font
    lg.fontSize = 8
    lg.alignment = "right"
    lg.columnMaximum = 1
    lg.boxAnchor = "nw"
    lg.dxTextSpace = 4
    lg.deltay = 12
    lg.colorNamePairs = [
        (colors.HexColor(_PALETTE[i % len(_PALETTE)]), n) for i, n in enumerate(names)
    ]
    return lg


def _chart_drawing(chart: ChartSpec, st: dict) -> Optional[Drawing]:
    """Render a ChartSpec to a ReportLab Drawing. Returns None for empty/none charts."""
    font, _ = _ensure_fonts()
    data = [r for r in (chart.data or []) if isinstance(r, dict)]
    series = [s for s in (chart.series or []) if getattr(s, "value_field", "")]
    if chart.type == "none" or not data or not series:
        return None

    cats = [str(r.get(chart.x_field, "")) for r in data]
    width, height = 17 * cm, 7.6 * cm
    dr = Drawing(width, height)

    if chart.type == "line":
        chart_obj = HorizontalLineChart()
        chart_obj.data = [[_num(r.get(s.value_field)) or 0 for r in data] for s in series]
        chart_obj.categoryAxis.categoryNames = cats
        chart_obj.joinedLines = 1
        for i in range(len(series)):
            chart_obj.lines[i].strokeColor = colors.HexColor(_PALETTE[i % len(_PALETTE)])
            chart_obj.lines[i].strokeWidth = 1.6
    elif chart.type == "horizontal_bar":
        chart_obj = HorizontalBarChart()
        chart_obj.data = [[_num(r.get(series[0].value_field)) or 0 for r in data]]
        chart_obj.categoryAxis.categoryNames = cats
        chart_obj.bars[0].fillColor = colors.HexColor(_PALETTE[0])
    else:  # grouped_bar / stacked_bar (ReportLab has no native stack -> grouped)
        chart_obj = VerticalBarChart()
        chart_obj.data = [[_num(r.get(s.value_field)) or 0 for r in data] for s in series]
        chart_obj.categoryAxis.categoryNames = cats
        for i in range(len(series)):
            chart_obj.bars[i].fillColor = colors.HexColor(_PALETTE[i % len(_PALETTE)])

    chart_obj.x = 8
    chart_obj.y = 26 if len(series) > 1 else 14
    chart_obj.width = width - 16
    chart_obj.height = height - chart_obj.y - 12
    chart_obj.valueAxis.labelTextFormat = _axis_formatter(chart.unit)
    chart_obj.valueAxis.labels.fontName = font
    chart_obj.valueAxis.labels.fontSize = 7
    chart_obj.categoryAxis.labels.fontName = font
    chart_obj.categoryAxis.labels.fontSize = 7
    chart_obj.categoryAxis.labels.boxAnchor = "n"
    chart_obj.categoryAxis.labels.dy = -2
    if chart.type != "horizontal_bar" and len(cats) > 6:
        chart_obj.categoryAxis.labels.angle = 30
        chart_obj.categoryAxis.labels.boxAnchor = "ne"
    dr.add(chart_obj)

    if len(series) > 1:
        lg = _legend([s.name or s.value_field for s in series], font)
        lg.x = 8
        lg.y = 8
        dr.add(lg)
    return dr


def _chart_block(chart: ChartSpec, st: dict) -> list:
    title = chart.title or chart.chart_id or "Biểu đồ"
    out: list = [Paragraph(_inline(title), st["caption"])]
    try:
        dr = _chart_drawing(chart, st)
    except Exception as exc:  # noqa: BLE001 - a bad chart spec degrades to a note
        dr = None
        out.append(Paragraph(_inline(f"(Không dựng được biểu đồ: {exc.__class__.__name__})"), st["sub"]))
    if dr is not None:
        out.append(dr)
    elif chart.type != "none":
        out.append(Paragraph(_inline("Không có dữ liệu để vẽ biểu đồ."), st["sub"]))
    out.append(Spacer(1, 8))
    return out


# ---- document assembly -----------------------------------------------------
_STATUS_VI = {"complete": "hoàn tất", "degraded": "một phần", "failed": "thiếu dữ liệu"}


def _heading(text: str, st: dict) -> Paragraph:
    return Paragraph(_inline(text), st["h2"])


def build_story(record: ReviewRecord, st: dict) -> list:
    """The ordered flowable list for one review (analytic → charts → evidence → sources)."""
    story: list = []
    title = (record.plan.analysis_title if record.plan and record.plan.analysis_title
             else record.question or "Báo cáo phân tích")
    story.append(Paragraph(_inline(title), st["title"]))
    status_vi = _STATUS_VI.get(record.status, record.status)
    meta_bits = [f"Phân tích chuyên sâu · {status_vi}"]
    if record.review_id:
        meta_bits.append(record.review_id)
    if record.created_at:
        meta_bits.append(record.created_at)
    story.append(Paragraph(_inline(" · ".join(meta_bits)), st["sub"]))
    if record.question and record.question != title:
        story.append(Paragraph(_inline(f"Câu hỏi: {record.question}"), st["sub"]))
    story.append(Spacer(1, 4))
    story.append(_hr())

    # 1) the written report (markdown)
    story += _markdown_flowables(record.report_markdown, st)

    # 2) charts
    charts = [c for c in (record.charts or []) if c and c.type != "none"]
    if charts:
        story.append(_heading("Biểu đồ", st))
        for c in charts:
            story.append(KeepTogether(_chart_block(c, st)))

    # 3) SQL evidence tables (web evidence is provenance -> shown under Sources)
    sql_ev = [e for e in (record.evidence or []) if e.source_type != "web"]
    if sql_ev:
        story.append(_heading("Bằng chứng", st))
        for ev in sql_ev:
            story += _evidence_table(ev, st)

    # 4) web sources
    story.append(_heading("Nguồn", st))
    sources = record.sources or []
    if sources:
        for i, s in enumerate(sources):
            n = s.get("n", i + 1)
            label = s.get("title") or s.get("url") or "Nguồn"
            url = s.get("url") or ""
            body = (f'[{n}] <link href="{html.escape(url, quote=True)}" color="#2563eb">'
                    f"<u>{_inline(label)}</u></link>") if url else f"[{n}] {_inline(label)}"
            if s.get("retrieved_at"):
                body += f" · {_inline(str(s['retrieved_at']))}"
            story.append(Paragraph(body, st["source"]))
    else:
        story.append(Paragraph(_inline("Chưa có nguồn web cho báo cáo này."), st["sub"]))

    # 5) caveats
    if record.caveats:
        story.append(_heading("Lưu ý", st))
        for c in record.caveats:
            story.append(Paragraph("• " + _inline(c), st["bullet"]))

    # 6) follow-up questions
    if record.follow_up_suggestions:
        story.append(_heading("Phân tích tiếp theo", st))
        for f in record.follow_up_suggestions:
            story.append(Paragraph("• " + _inline(f), st["bullet"]))

    return story


def _hr():
    tbl = Table([[""]], colWidths=[17 * cm], rowHeights=[1])
    tbl.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.6, _RULE)]))
    return tbl


def _footer(canvas, doc):
    canvas.saveState()
    font, _ = _ensure_fonts()
    canvas.setFont(font, 8)
    canvas.setFillColor(_MUTED)
    canvas.drawString(2 * cm, 1.1 * cm, "Trợ lý dữ liệu bán hàng")
    canvas.drawRightString(A4[0] - 2 * cm, 1.1 * cm, f"Trang {doc.page}")
    canvas.restoreState()


def render_review_pdf(record: ReviewRecord) -> bytes:
    """Render a persisted review to PDF bytes. Never raises on report content."""
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title=(record.plan.analysis_title if record.plan and record.plan.analysis_title
               else record.question or "Báo cáo phân tích"),
        author="Trợ lý dữ liệu bán hàng",
    )
    story = build_story(record, st)
    try:
        doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    except Exception:  # noqa: BLE001 - last-resort: emit a minimal valid PDF, never 500
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4)
        doc.build([Paragraph(_inline(record.question or "Báo cáo phân tích"), st["title"]),
                   Paragraph(_inline("Không dựng được báo cáo đầy đủ."), st["body"])])
    return buf.getvalue()


def pdf_filename(record: ReviewRecord) -> str:
    """An ASCII-safe download filename for a review (Content-Disposition friendly)."""
    stem = record.review_id or "review"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "review"
    return f"bao-cao-{stem}.pdf"
