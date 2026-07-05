"""Evidence-item construction (plan §15.2-15.3).

Converts a profiled ``TaskResult`` into an ``EvidenceItem`` with a HARD ``source_type``
column (``sql`` here; ``web`` in Phase 17) so the writer and frontend distinguish database
facts from web claims structurally, never by parsing text. Rows are capped at
``ANALYTIC_EVIDENCE_MAX_ROWS`` — the full result is never persisted.

``profile_sentence`` renders a deterministic Vietnamese one-liner from a profile; it feeds
both the interim report here and the Phase 15 skeleton fallback.
"""
from __future__ import annotations

import re

from backend import config
from backend.analysis.models import EvidenceItem, SHAPE_TO_CHART_SHAPE, TaskResult

# Count-like names never carry a currency unit (so_don_hang, so_khach_hang, so_luong, ...).
_COUNT_RE = re.compile(r"(^|_)(so|count|number|dem|luot|so_luong)(_|$)", re.I)
# Clearly money-named metrics/fields. NOTE: the generic KPI column alias "gia_tri" is
# deliberately NOT here — it is used for both revenue and count KPIs, so money-ness is
# decided by the task's metric, not the alias.
_MONEY_RE = re.compile(
    r"(doanh_thu|thanh_tien|tong_tien|gia_ban|don_gia|revenue|amount|_tien|_gia)", re.I)


def is_money(metric: str = "", value_field: str = "") -> bool:
    """Whether values should carry a ₫/VND unit — decided by the metric first, then the
    field name. Count metrics (so_*) are never money even though the KPI alias is 'gia_tri'."""
    for token in (metric or "", value_field or ""):
        if not token:
            continue
        if _COUNT_RE.search(token):
            return False
        if _MONEY_RE.search(token):
            return True
    return False


def _fmt_number(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f):
        return f"{int(f):,}".replace(",", ".")
    return f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_money(v, metric: str = "", value_field: str = "") -> str:
    s = _fmt_number(v)
    return f"{s} ₫" if is_money(metric, value_field) else s


def _fmt_pct(p) -> str:
    if p is None:
        return ""
    return f"{p:.1f}%".replace(".", ",")


def build_evidence(evidence_id: str, review_id: str, task: TaskResult, prof: dict,
                   *, created_at: str = "") -> EvidenceItem:
    """Build one evidence item from an executed, profiled task."""
    kind = SHAPE_TO_CHART_SHAPE.get(task.expected_shape, "raw")
    max_rows = config.ANALYTIC_EVIDENCE_MAX_ROWS
    status = task.status
    if status == "success" and task.row_count == 0:
        # A ran-but-empty task is still valid evidence (an absence is a finding).
        status = "success"
    return EvidenceItem(
        evidence_id=evidence_id, review_id=review_id, task_id=task.task_id,
        kind=kind if status == "success" else "raw",
        source_type="sql", metric=task.metric, title=task.title, purpose=task.purpose,
        sql=task.sql, columns=list(task.columns), rows=list(task.rows[:max_rows]),
        profile=prof or {}, status=status, created_at=created_at)


def build_web_evidence(evidence_id: str, review_id: str, *, n: int, query: str,
                       source: dict, retrieved_at: str, created_at: str = "") -> EvidenceItem:
    """Build one ``source_type="web"`` evidence item from a structured SearxNG result.

    Web evidence carries no SQL/rows — its payload is the ``web`` dict (the citation the
    writer references as ``[n]`` and the frontend renders in SourcesList). Provenance is a
    hard column (``source_type="web"``), never inferred from text (plan §15.2 web variant).
    """
    title = (source.get("title") or source.get("url") or "").strip()
    return EvidenceItem(
        evidence_id=evidence_id, review_id=review_id, task_id="",
        kind="web", source_type="web", metric="",
        title=title, purpose=query, sql="",
        columns=[], rows=[], profile={},
        web={
            "n": n,
            "query": query,
            "url": source.get("url") or "",
            "source_title": title,
            "snippet": source.get("snippet") or "",
            "published": source.get("published"),
            "retrieved_at": retrieved_at,
        },
        status="success", created_at=created_at)


def build_geo_evidence(evidence_id: str, review_id: str, *, title: str, label: str,
                       prospects: list[dict], penetration: dict, created_at: str = "") -> EvidenceItem:
    """Build one ``source_type="geo"`` evidence item: nearby prospect outlets + penetration.

    ``kind="raw"`` so the generic chart planner never auto-charts the prospect table (the
    by-category chart is built explicitly); the penetration profile is narrated by
    ``profile_sentence`` (shape="geo") so the writer/skeleton surface the market context.
    """
    max_rows = config.ANALYTIC_EVIDENCE_MAX_ROWS
    columns = ["Tên cửa hàng", "Ngành hàng", "Khoảng cách (m)", "Địa chỉ", "Đánh giá"]
    rows = [{
        "Tên cửa hàng": p.get("name", ""),
        "Ngành hàng": p.get("loai_label", ""),
        "Khoảng cách (m)": p.get("distance_m"),
        "Địa chỉ": p.get("address", ""),
        "Đánh giá": p.get("rating") if p.get("rating") is not None else "",
    } for p in (prospects or [])[:max_rows]]
    profile = {"shape": "geo", "label": label, **(penetration or {})}
    return EvidenceItem(
        evidence_id=evidence_id, review_id=review_id, task_id="",
        kind="raw", source_type="geo", metric="",
        title=title, purpose=f"Cửa hàng bán lẻ gần {label} và độ phủ khách hàng hiện có",
        sql="", columns=columns, rows=rows, profile=profile, status="success", created_at=created_at)


def profile_sentence(ev: EvidenceItem) -> str:
    """A deterministic Vietnamese finding sentence for one evidence item."""
    p = ev.profile or {}
    shape = p.get("shape", ev.kind)
    title = ev.title or "Chỉ số"
    if shape == "geo":
        return (f"{title}: có khoảng {p.get('nearby_total', 0)} cửa hàng bán lẻ trong bán kính, "
                f"{p.get('customers_in_area', 0)} đã là khách hàng, còn ~{p.get('prospects', 0)} "
                f"cửa hàng tiềm năng (độ phủ ~{p.get('penetration_pct', 0)}%).")
    if ev.status == "failed":
        return f"{title}: không chạy được (lỗi truy vấn)."
    if ev.status == "skipped":
        return f"{title}: bỏ qua (vượt ngân sách thời gian)."
    if not ev.rows and "empty_result" in (p.get("warnings") or []):
        return f"{title}: không có dữ liệu trong kỳ."

    mt = ev.metric
    if shape == "kpi":
        cur, prev = p.get("current"), p.get("previous")
        vf = p.get("value_field", "")
        if cur is None:
            return f"{title}: không xác định được giá trị."
        base = f"{title}: {_fmt_money(cur, mt, vf)}"
        if prev is not None:
            verb = {"down": "giảm", "up": "tăng", "flat": "gần như không đổi"}.get(p.get("trend"), "thay đổi")
            pct = _fmt_pct(p.get("pct_change"))
            base += f" so với {_fmt_money(prev, mt, vf)} kỳ trước ({verb}{' ' + pct if pct else ''})"
        return base + "."
    if shape == "by_dimension":
        mover = p.get("biggest_mover") or {}
        conc = p.get("top3_concentration")
        parts = [f"{title}: {p.get('n_groups', 0)} nhóm"]
        if mover:
            parts.append(f"biến động mạnh nhất là {mover.get('label')} "
                         f"({_fmt_money(mover.get('change'), mt, p.get('current_field',''))})")
        if conc is not None:
            parts.append(f"top-3 chiếm {_fmt_pct(conc * 100)} mức thay đổi")
        return "; ".join(parts) + "."
    if shape == "trend":
        vf = p.get("value_field", "")
        verb = {"down": "giảm", "up": "tăng", "flat": "đi ngang"}.get(p.get("direction"), "thay đổi")
        best, worst = p.get("best_period") or {}, p.get("worst_period") or {}
        s = f"{title}: {p.get('n_periods', 0)} kỳ, xu hướng {verb}"
        if best and worst:
            s += (f" (cao nhất {best.get('period')} = {_fmt_money(best.get('value'), mt, vf)}, "
                  f"thấp nhất {worst.get('period')} = {_fmt_money(worst.get('value'), mt, vf)})")
        return s + "."
    if shape == "top_n":
        vf = p.get("value_field", "")
        share = _fmt_pct((p.get("leader_share") or 0) * 100) if p.get("leader_share") is not None else ""
        s = f"{title}: dẫn đầu là {p.get('leader')} ({_fmt_money(p.get('leader_value'), mt, vf)}"
        s += f", chiếm {share}" if share else ""
        return s + ")."
    return f"{title}: {len(ev.rows)} dòng."
