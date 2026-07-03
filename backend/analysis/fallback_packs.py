"""Deterministic task packs from a playbook's diagnostic steps (plan §13.4).

When the planner LLM returns garbage (or is offline), the review must still ship. This
module instantiates the top retrieved playbook's ``diagnostic_steps`` into concrete,
validator-passing SQL tasks by substituting the resolved date window and entity filter into
each step's ``sql_hint``. Every generated task is put through the full 6-layer validator;
steps that fail are skipped. It is also the unit-test path — with the LLM disabled it must
produce a complete, correct review for the seeded playbooks.
"""
from __future__ import annotations

import re
from typing import Optional

from backend import config
from backend.analysis.models import (
    AnalyticContext,
    DateWindow,
    PlannedTask,
    ReviewPlan,
    ReviewSeed,
)
from backend.common import schema_def
from backend.validation.sql_validator import validate

# Runtime placeholders substituted into a step's sql_hint. Must stay in sync with
# entry_validator._HINT_FILLERS (save-time validation) — {dimension_column} is a documented
# placeholder there, so it must be substituted here too or a valid-at-save hint would emit
# leftover braces at run time.
_PLACEHOLDERS = ("date_from", "date_to", "compare_from", "compare_to", "entity_filter",
                 "dimension_column")
# A leftover {snake_case} token after substitution means an unknown placeholder slipped
# through save-time validation; such SQL is dropped rather than executed (it would be wrong).
_LEFTOVER_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _substitute(hint: str, window: DateWindow, entity_filter: str,
                dimension_column: str = "") -> str:
    values = {
        "date_from": window.date_from, "date_to": window.date_to,
        "compare_from": window.compare_from, "compare_to": window.compare_to,
        "entity_filter": entity_filter, "dimension_column": dimension_column,
    }
    out = hint
    for key in _PLACEHOLDERS:
        out = out.replace("{" + key + "}", values.get(key, ""))
    return out


def _dimension_column(dim_slug: str, dimensions: list[dict]) -> str:
    for d in dimensions:
        if d.get("dimension") == dim_slug and d.get("table") and d.get("column"):
            return f"{d['table']}.{d['column']}"
    return ""


def _synthesize_from_metric(step: dict, ctx: AnalyticContext, window: DateWindow,
                            entity_filter: str) -> str:
    """Best-effort SQL when a step has no sql_hint: aggregate the step's metric, optionally
    grouped by the step's dimension. Kept simple; the validator gates the result."""
    metric_name = step.get("metric", "")
    formula = ""
    for m in getattr(ctx.schema_context, "metrics", []) or []:
        if m.metric == metric_name and m.formula:
            formula = m.formula
            break
    if not formula:
        return ""
    dim_col = _dimension_column(step.get("dimension", ""), ctx.dimensions)
    base = (
        "FROM don_hang_ban dh\n"
        "JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id\n"
        f"WHERE dh.trang_thai = 'NORMAL' AND dh.ngay_dat_hang "
        f"BETWEEN '{window.date_from}' AND '{window.date_to}' {entity_filter}")
    if dim_col:
        return (f"SELECT {dim_col} AS nhom, {formula} AS gia_tri\n{base}\n"
                f"GROUP BY {dim_col}\nORDER BY gia_tri DESC")
    return (f"SELECT 'ky_nay' AS ky, {formula} AS gia_tri\n{base}")


def _pick_playbook(ctx: AnalyticContext) -> Optional[dict]:
    for pb in ctx.playbooks:
        if pb.get("diagnostic_steps"):
            return pb
    return None


def build_fallback_pack(ctx: AnalyticContext, window: DateWindow,
                        seed: Optional[ReviewSeed] = None) -> ReviewPlan:
    """Instantiate the top playbook's steps into validated tasks (never raises)."""
    entity_filter = seed.entity_filter_sql() if (seed and seed.ok) else ""
    playbook = _pick_playbook(ctx)
    plan = ReviewPlan(
        source="fallback", date_window=window,
        analysis_title=(ctx.question or "Phân tích").strip()[:120])

    if playbook is None:
        # No playbook retrieved: emit a minimal, always-valid revenue KPI + monthly trend.
        return _default_pack(ctx, window, entity_filter, plan)

    plan.playbook_used = f"playbook:{playbook.get('playbook','')}"
    tasks: list[PlannedTask] = []
    for i, step in enumerate(playbook.get("diagnostic_steps", []), 1):
        if len(tasks) >= config.ANALYTIC_MAX_TASKS:
            break
        hint = step.get("sql_hint", "")
        dim_col = _dimension_column(step.get("dimension", ""), ctx.dimensions)
        sql = _substitute(hint, window, entity_filter, dim_col) if hint else \
            _synthesize_from_metric(step, ctx, window, entity_filter)
        if not sql.strip():
            plan.dropped.append(f"{step.get('title','')}: không dựng được SQL")
            continue
        leftover = _LEFTOVER_PLACEHOLDER.search(sql)
        if leftover:
            plan.dropped.append(
                f"{step.get('title','')}: placeholder chưa thay thế: {leftover.group()}")
            continue
        vr = validate(sql, resolved_tables=None)
        if not vr.ok:
            plan.dropped.append(f"{step.get('title','')}: {'; '.join(vr.errors)}")
            continue
        tasks.append(PlannedTask(
            task_id=f"t{i}", title=step.get("title", f"Bước {i}"),
            purpose=step.get("purpose", ""),
            expected_shape=step.get("expected_shape", "kpi"),
            metric=step.get("metric", ""), dimension=step.get("dimension", ""),
            sql=vr.normalized_sql))
    plan.tasks = tasks
    if len(tasks) < 2:
        # Playbook steps mostly failed — top up with the guaranteed default pack.
        _augment_default(ctx, window, entity_filter, plan)
    return plan


def _kpi_revenue_sql(window: DateWindow, entity_filter: str) -> str:
    return (
        "SELECT 'ky_nay' AS ky, SUM(ct.thanh_tien) AS gia_tri\n"
        "FROM don_hang_ban dh\n"
        "JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id\n"
        f"WHERE dh.trang_thai = 'NORMAL' AND dh.ngay_dat_hang "
        f"BETWEEN '{window.date_from}' AND '{window.date_to}' {entity_filter}\n"
        "UNION ALL\n"
        "SELECT 'ky_truoc' AS ky, SUM(ct.thanh_tien) AS gia_tri\n"
        "FROM don_hang_ban dh\n"
        "JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id\n"
        f"WHERE dh.trang_thai = 'NORMAL' AND dh.ngay_dat_hang "
        f"BETWEEN '{window.compare_from}' AND '{window.compare_to}' {entity_filter}")


def _trend_revenue_sql(window: DateWindow, entity_filter: str) -> str:
    return (
        "SELECT strftime('%Y-%m', dh.ngay_dat_hang) AS thang, SUM(ct.thanh_tien) AS gia_tri\n"
        "FROM don_hang_ban dh\n"
        "JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id\n"
        f"WHERE dh.trang_thai = 'NORMAL' AND dh.ngay_dat_hang "
        f"BETWEEN '{window.compare_from}' AND '{window.date_to}' {entity_filter}\n"
        "GROUP BY strftime('%Y-%m', dh.ngay_dat_hang)\nORDER BY thang")


def _default_pack(ctx: AnalyticContext, window: DateWindow, entity_filter: str,
                  plan: ReviewPlan) -> ReviewPlan:
    _augment_default(ctx, window, entity_filter, plan)
    return plan


def _augment_default(ctx: AnalyticContext, window: DateWindow, entity_filter: str,
                     plan: ReviewPlan) -> None:
    """Append a guaranteed revenue KPI + trend so a review always has >= 2 valid tasks."""
    have = {t.task_id for t in plan.tasks}
    candidates = [
        ("kpi", "Doanh thu kỳ này so với kỳ trước",
         "Xác nhận và định lượng mức thay đổi doanh thu.",
         _kpi_revenue_sql(window, entity_filter)),
        ("trend", "Xu hướng doanh thu theo tháng",
         "Nhìn xu hướng doanh thu theo thời gian.",
         _trend_revenue_sql(window, entity_filter)),
    ]
    idx = len(plan.tasks)
    for shape, title, purpose, sql in candidates:
        vr = validate(sql, resolved_tables=None)
        if not vr.ok:
            continue
        idx += 1
        tid = f"d{idx}"
        while tid in have:
            idx += 1
            tid = f"d{idx}"
        plan.tasks.append(PlannedTask(
            task_id=tid, title=title, purpose=purpose, expected_shape=shape,
            metric="doanh_thu", sql=vr.normalized_sql))
        have.add(tid)
