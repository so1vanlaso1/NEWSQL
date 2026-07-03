"""Save-time validation of knowledge entries (Phase 10, plan §12.3).

Pydantic (``store/models.validate_body``) already enforces each type's *shape*. This
module adds *semantic* checks against the real schema (``common/schema_def``) and the SQL
dialect (sqlglot): a metric whose formula references a non-existent column, or does not
parse, is rejected at save time with a clear Vietnamese message the UI shows inline —
instead of silently producing SQL that fails at query time.

``validate_entry(entry_type, body, repo=None)`` returns a list of error strings (empty =
valid). The caller applies the ``KB_VALIDATE_ON_SAVE`` policy (strict rejects, warn
attaches, off skips). The optional ``repo`` enables cross-entry checks (a playbook's
referenced metric/dimension entries exist; a dimension's join_requirement names a real
join_path); without it those checks are skipped so pure-body unit tests still pass.
"""
from __future__ import annotations

import re

from backend.common import schema_def

# table.column reference inside a formula / join condition (khong-dau snake_case).
_COLREF = re.compile(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b")

# sql_hint placeholders -> parse-safe fillers (plan §13.4). Substituted only to check the
# template PARSES; real values are injected by the planner / fallback pack at run time.
_HINT_FILLERS = {
    "{date_from}": "2024-01-01", "{date_to}": "2024-01-31",
    "{compare_from}": "2023-12-01", "{compare_to}": "2023-12-31",
    "{dimension_column}": "x", "{entity_filter}": "",
}


def _fill_placeholders(sql: str) -> str:
    for k, v in _HINT_FILLERS.items():
        sql = sql.replace(k, v)
    return sql


def _tables() -> set[str]:
    return set(schema_def.all_table_names())


def _columns(table: str) -> set[str]:
    try:
        return set(schema_def.columns_of(table))
    except KeyError:
        return set()


def _parses(sql_fragment: str) -> bool:
    """True if the fragment parses under the configured dialect (sqlglot)."""
    if not (sql_fragment or "").strip():
        return False
    try:
        import sqlglot
    except Exception:  # sqlglot missing -> can't check; treat as parseable
        return True
    from backend import config
    try:
        return sqlglot.parse_one(sql_fragment, read=config.SQL_DIALECT) is not None
    except Exception:  # noqa: BLE001 - any parse error means "does not parse"
        return False


def _check_colrefs(text: str, field: str, errors: list[str]) -> None:
    """Every ``table.column`` mentioned in ``text`` must exist in the schema."""
    known = _tables()
    for tbl, col in _COLREF.findall(text or ""):
        if tbl not in known:
            errors.append(f"{field}: bảng không tồn tại trong schema: {tbl}")
        elif col not in _columns(tbl):
            errors.append(f"{field}: cột không tồn tại trong schema: {tbl}.{col}")


def _check_metric(body: dict, errors: list[str], repo=None) -> None:
    formula = body.get("formula", "")
    if not formula.strip():
        errors.append("formula: công thức không được để trống")
    elif not _parses(formula):
        errors.append(f"formula: công thức không phân tích được (SQL không hợp lệ): {formula}")
    _check_colrefs(formula, "formula", errors)
    known = _tables()
    for t in body.get("required_tables", []):
        if t and t not in known:
            errors.append(f"required_tables: bảng không tồn tại: {t}")


def _check_join_path(body: dict, errors: list[str], repo=None) -> None:
    known = _tables()
    for t in body.get("tables", []):
        if t and t not in known:
            errors.append(f"tables: bảng không tồn tại: {t}")
    for cond in body.get("joins", []):
        if not _parses(f"SELECT 1 WHERE {cond}"):
            errors.append(f"joins: điều kiện join không hợp lệ: {cond}")
        _check_colrefs(cond, "joins", errors)


def _check_table_column_pair(table: str, column: str, errors: list[str],
                             *, table_field: str = "table", col_field: str = "column") -> None:
    if not table:
        errors.append(f"{table_field}: thiếu tên bảng")
        return
    if table not in _tables():
        errors.append(f"{table_field}: bảng không tồn tại trong schema: {table}")
        return
    if column and column not in _columns(table):
        errors.append(f"{col_field}: cột không tồn tại trong bảng {table}: {column}")


def _check_value(body: dict, errors: list[str], repo=None) -> None:
    _check_table_column_pair(body.get("table", ""), body.get("column", ""), errors)
    id_col = body.get("id_column", "")
    if id_col:
        _check_table_column_pair(body.get("table", ""), id_col, errors, col_field="id_column")


def _check_column(body: dict, errors: list[str], repo=None) -> None:
    _check_table_column_pair(body.get("table", ""), body.get("column", ""), errors)


def _check_table(body: dict, errors: list[str], repo=None) -> None:
    name = body.get("table", "")
    if not name:
        errors.append("table: thiếu tên bảng")
    elif name not in _tables():
        errors.append(f"table: bảng không tồn tại trong schema: {name}")


def _entry_exists(repo, entry_id: str) -> bool:
    """True when repo has an entry with this id (used for cross-entry reference checks)."""
    if repo is None:
        return True  # no repo -> skip the cross-entry check (treat as satisfied)
    try:
        return repo.get(entry_id) is not None
    except Exception:  # noqa: BLE001 - a repo hiccup must not block the save
        return True


def _check_dimension(body: dict, errors: list[str], repo=None) -> None:
    _check_table_column_pair(body.get("table", ""), body.get("column", ""), errors)
    id_col = body.get("id_column", "")
    if id_col:
        _check_table_column_pair(body.get("table", ""), id_col, errors, col_field="id_column")
    # join_requirement (if set) must name an existing join_path entry.
    jr = body.get("join_requirement", "")
    if jr and not _entry_exists(repo, f"join_path:{jr}"):
        errors.append(f"join_requirement: không tìm thấy join_path: {jr}")


def _check_playbook(body: dict, errors: list[str], repo=None) -> None:
    steps = body.get("diagnostic_steps") or []
    if not steps:
        errors.append("diagnostic_steps: playbook cần ít nhất một bước chẩn đoán")
    for i, step in enumerate(steps, 1):
        hint = (step or {}).get("sql_hint", "")
        if hint.strip() and not _parses(_fill_placeholders(hint)):
            errors.append(f"diagnostic_steps[{i}].sql_hint: không phân tích được sau khi thay thế placeholder")
        dim = (step or {}).get("dimension", "")
        if dim and not _entry_exists(repo, f"dimension:{dim}"):
            errors.append(f"diagnostic_steps[{i}].dimension: không tìm thấy dimension: {dim}")
        met = (step or {}).get("metric", "")
        if met and not _entry_exists(repo, f"metric:{met}"):
            errors.append(f"diagnostic_steps[{i}].metric: không tìm thấy metric: {met}")
    for m in body.get("main_metrics", []):
        if m and not _entry_exists(repo, f"metric:{m}"):
            errors.append(f"main_metrics: không tìm thấy metric: {m}")


def _check_chart_rule(body: dict, errors: list[str], repo=None) -> None:
    # shape/chart_type are enum-validated by pydantic; only sanity-check the caps here.
    if int(body.get("max_categories", 0) or 0) < 0:
        errors.append("max_categories: phải >= 0")
    if int(body.get("min_rows", 0) or 0) < 0:
        errors.append("min_rows: phải >= 0")


_CHECKERS = {
    "metric": _check_metric,
    "join_path": _check_join_path,
    "value": _check_value,
    "column": _check_column,
    "table": _check_table,
    "dimension": _check_dimension,
    "playbook": _check_playbook,
    "chart_rule": _check_chart_rule,
    # 'rule', 'caveat' have no schema-level checks here.
}


def validate_entry(entry_type: str, body: dict, repo=None) -> list[str]:
    """Return a list of human-readable validation errors ([] means valid).

    ``repo`` (optional) enables cross-entry reference checks; without it those are skipped.
    """
    checker = _CHECKERS.get(entry_type)
    if checker is None:
        return []
    errors: list[str] = []
    try:
        checker(body or {}, errors, repo)
    except Exception as exc:  # noqa: BLE001 - a validator bug must not block saves
        return [f"lỗi kiểm tra nội bộ: {exc.__class__.__name__}: {exc}"]
    return errors
