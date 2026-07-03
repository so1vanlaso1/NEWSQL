"""Sequential validated task execution (plan §14).

Reuses the normal pipeline's validator and read-only runner **unchanged** — every analytic
SQL goes through the exact same 6-layer validation and ``mode=ro`` connection, so analytic
mode can never write. Tasks run sequentially (single user, small DB). A failed task becomes
a failed-evidence record and the review continues; the wall-clock budget skips remaining
tasks with a caveat rather than hanging.
"""
from __future__ import annotations

from typing import Optional

from backend import config
from backend.analysis.models import PlannedTask, TaskResult
from backend.common.logging import get_logger
from backend.execution.query_runner import run_query
from backend.llm import review_prompts
from backend.llm.client import LlmClient
from backend.llm.response_parser import clean_sql, extract_json_object
from backend.validation.sql_validator import validate

log = get_logger(__name__)


def skipped_result(task: PlannedTask, reason: str) -> TaskResult:
    """A ``skipped`` TaskResult (used when the review's wall-clock budget is exceeded)."""
    return TaskResult(
        task_id=task.task_id, title=task.title, purpose=task.purpose,
        expected_shape=task.expected_shape, metric=task.metric, dimension=task.dimension,
        sql=task.sql, status="skipped", error=reason)


def _repair_sql(task: PlannedTask, error: str, client: LlmClient) -> Optional[str]:
    """One LLM repair round for a runtime SQL error (plan §14). Returns validated SQL or None."""
    system = review_prompts.build_task_repair_system_prompt()
    user = review_prompts.build_task_repair_user_prompt(task.title, task.sql, error)
    res = client.chat(system, user, temperature=config.LLM_TEMPERATURE_SQL,
                      max_tokens=config.LLM_MAX_TOKENS_SQL)
    if res.error:
        return None
    data = extract_json_object(res.content)
    candidate = clean_sql(data.get("sql")) if isinstance(data, dict) else clean_sql(res.content)
    if not candidate:
        return None
    vr = validate(candidate, resolved_tables=None)
    return vr.normalized_sql if vr.ok else None


def run_task(task: PlannedTask, client: Optional[LlmClient] = None) -> TaskResult:
    """Execute one task with an optional single self-repair round. Never raises."""
    tr = TaskResult(
        task_id=task.task_id, title=task.title, purpose=task.purpose,
        expected_shape=task.expected_shape, metric=task.metric, dimension=task.dimension,
        sql=task.sql)

    # Re-validate defensively (fallback-pack SQL is already validated, but this keeps the
    # runner self-contained and safe for any caller).
    vr = validate(task.sql, resolved_tables=None)
    if not vr.ok:
        tr.status = "failed"
        tr.error = "; ".join(vr.errors)
        return tr
    sql = vr.normalized_sql
    tr.sql = sql

    qr = run_query(sql)
    if qr.error and client is not None and config.ANALYTIC_MAX_REPAIRS_PER_TASK > 0:
        fixed = _repair_sql(task, qr.error, client)
        if fixed:
            qr2 = run_query(fixed)
            if not qr2.error:
                tr.sql, qr, tr.repaired = fixed, qr2, True

    if qr.error:
        tr.status = "failed"
        tr.error = qr.error
        return tr
    tr.status = "success"
    tr.columns = qr.columns
    tr.rows = qr.rows
    tr.row_count = qr.row_count
    tr.truncated = qr.truncated
    return tr
