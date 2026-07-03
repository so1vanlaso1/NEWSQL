"""Review planner: LLM call 1 + the validation ladder (plan §13.1-13.4).

The planner proposes SQL tasks; the backend validates every one through the same 6-layer
validator the normal pipeline uses. A malformed plan degrades — not fails — via one retry
with the validation errors appended, then the deterministic fallback pack. So a review is
always plannable even when the 9B model returns garbage.
"""
from __future__ import annotations

from typing import Optional

from backend import config
from backend.analysis import fallback_packs
from backend.analysis.models import (
    AnalyticContext,
    DateWindow,
    PlannedTask,
    ReviewPlan,
    ReviewSeed,
)
from backend.common.logging import get_logger
from backend.llm import review_prompts
from backend.llm.client import LlmClient
from backend.llm.response_parser import clean_sql, extract_json_object
from backend.validation.sql_validator import validate

log = get_logger(__name__)

_VALID_SHAPES = {"kpi", "by_dimension", "trend", "top_n"}


def _norm_sql_key(sql: str) -> str:
    return " ".join((sql or "").split()).lower()


def _parse_date_window(data: dict, default: DateWindow) -> DateWindow:
    dr = data.get("date_range") if isinstance(data, dict) else None
    if not isinstance(dr, dict):
        return default
    win = DateWindow(
        date_from=str(dr.get("from") or default.date_from),
        date_to=str(dr.get("to") or default.date_to),
        compare_from=str(dr.get("compare_from") or default.compare_from),
        compare_to=str(dr.get("compare_to") or default.compare_to),
        label=default.label, compare_label=default.compare_label)
    # Reject a window that isn't full ISO dates (keep the deterministic default).
    for v in (win.date_from, win.date_to, win.compare_from, win.compare_to):
        if len(v) < 8:
            return default
    return win


def _validate_tasks(raw_tasks: object) -> tuple[list[PlannedTask], list[str]]:
    """Structural check + per-task 6-layer validation + dedupe (plan §13.3 steps 2-4)."""
    tasks: list[PlannedTask] = []
    dropped: list[str] = []
    seen_sql: set[str] = set()
    if not isinstance(raw_tasks, list):
        return tasks, ["tasks không phải danh sách"]
    for i, t in enumerate(raw_tasks, 1):
        if len(tasks) >= config.ANALYTIC_MAX_TASKS:
            break
        if not isinstance(t, dict):
            dropped.append(f"task #{i}: không phải object")
            continue
        sql = clean_sql(t.get("sql"))
        title = str(t.get("title") or f"Task {i}").strip()
        if not sql:
            dropped.append(f"{title}: thiếu SQL")
            continue
        shape = str(t.get("expected_shape") or "kpi").strip().lower()
        if shape not in _VALID_SHAPES:
            shape = "kpi"
        vr = validate(sql, resolved_tables=None)
        if not vr.ok:
            dropped.append(f"{title}: {'; '.join(vr.errors)}")
            continue
        key = _norm_sql_key(vr.normalized_sql)
        if key in seen_sql:
            dropped.append(f"{title}: trùng SQL")
            continue
        seen_sql.add(key)
        tasks.append(PlannedTask(
            task_id=str(t.get("task_id") or f"t{i}"),
            title=title, purpose=str(t.get("purpose") or "").strip(),
            expected_shape=shape, metric=str(t.get("metric") or "").strip(),
            dimension=str(t.get("dimension") or "").strip(),
            sql=vr.normalized_sql))
    return tasks, dropped


def _plan_from_response(content: str, default_window: DateWindow) -> tuple[Optional[ReviewPlan], list[str]]:
    """Parse one planner response into a (partial) plan + the dropped-task notes."""
    data = extract_json_object(content)
    if data is None:
        return None, ["không parse được JSON từ planner"]
    if str(data.get("mode_downgrade") or "").upper() == "NORMAL_SQL":
        return ReviewPlan(mode_downgrade="NORMAL_SQL", source="llm"), []
    tasks, dropped = _validate_tasks(data.get("tasks"))
    plan = ReviewPlan(
        analysis_title=str(data.get("analysis_title") or "").strip(),
        playbook_used=str(data.get("playbook_used") or "").strip(),
        date_window=_parse_date_window(data, default_window),
        tasks=tasks, dropped=dropped, source="llm")
    return plan, dropped


def plan_review(ctx: AnalyticContext, window: DateWindow, client: LlmClient,
                seed: Optional[ReviewSeed] = None) -> ReviewPlan:
    """Run the planner LLM call and the full validation ladder (plan §13.3).

    Never raises: an LLM/parse/validation failure degrades to a retry and then to the
    deterministic fallback pack. Set ``client=None`` to force the fallback path (tests).
    """
    system = review_prompts.build_planner_system_prompt()
    user = review_prompts.build_planner_user_prompt(ctx, window, seed)

    if client is None:
        pack = fallback_packs.build_fallback_pack(ctx, window, seed)
        pack.analysis_title = pack.analysis_title or (ctx.question or "Phân tích")
        return pack

    res = client.chat(system, user, temperature=config.LLM_TEMPERATURE_SQL,
                      max_tokens=config.LLM_MAX_TOKENS_SQL)
    if res.error:
        log.warning("planner LLM error: %s -> fallback pack", res.error)
        return _finalize_fallback(ctx, window, seed, note=f"planner offline: {res.error}")

    plan, dropped = _plan_from_response(res.content, window)
    if plan is not None and plan.is_downgrade:
        return plan
    if plan is not None and len(plan.tasks) >= 2:
        return plan

    # Retry once with the validation errors appended (plan §13.3 step 5).
    errors = (plan.dropped if plan else dropped)
    retry_user = review_prompts.build_planner_retry_user_prompt(user, errors)
    res2 = client.chat(system, retry_user, temperature=config.LLM_TEMPERATURE_SQL,
                       max_tokens=config.LLM_MAX_TOKENS_SQL)
    if not res2.error:
        plan2, _ = _plan_from_response(res2.content, window)
        if plan2 is not None and plan2.is_downgrade:
            return plan2
        if plan2 is not None and len(plan2.tasks) >= 2:
            plan2.source = "llm_repair"
            return plan2

    # Still short — deterministic fallback pack (plan §13.3 step 6).
    return _finalize_fallback(ctx, window, seed,
                              note="planner returned < 2 valid tasks")


def _finalize_fallback(ctx: AnalyticContext, window: DateWindow,
                       seed: Optional[ReviewSeed], note: str) -> ReviewPlan:
    pack = fallback_packs.build_fallback_pack(ctx, window, seed)
    pack.analysis_title = pack.analysis_title or (ctx.question or "Phân tích")
    pack.notes = note
    log.info("using fallback pack (%s): %d task(s)", note, len(pack.tasks))
    return pack
