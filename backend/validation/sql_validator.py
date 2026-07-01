"""SELECT-only SQL validation (Phase 8).

Adapted from the old pipeline's ``schema_rag/validator.py`` onto the new
``backend.common`` modules. The LLM proposes SQL; this module decides whether it is
safe to run. Layers:

1. Regex/structural gate: non-empty, no ``;`` chaining, must start SELECT/WITH, no
   dangerous keyword.
2. sqlglot parse (``read="sqlite"``): exactly one statement, a read node
   (Select/With/Union), and ``SELECT *`` requires a LIMIT.
3. Vietnamese guard: identifiers must be khong-dau (a diacritic in a table/column name
   is a generation error; string literals are untouched).
4. Allow-list vs the real DB schema (``schema_def``): unknown table / ``alias.col``.
5. LIMIT policy: enforce an explicit LIMIT <= ``MAX_RESULT_ROWS``; auto-inject a LIMIT
   for raw (non-aggregate) row SELECTs that omit one (warning, not a failure).
6. SQLite binding via ``EXPLAIN`` + a soft ``EXPLAIN QUERY PLAN`` scan-size warning.

A referenced table that is not in the retrieved context is a WARNING only (bridge
tables like ``chi_tiet_don_hang_ban`` are legitimately needed) - never a hard failure.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend import config
from backend.common import schema_def
from backend.common.vn_text import has_diacritics

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_DANGEROUS_RE = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM)\b",
    re.I,
)
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.I)

_ROW_COUNT_CACHE: dict[str, dict[str, int]] = {}


@dataclass
class ValidationResult:
    ok: bool
    normalized_sql: str = ""
    referenced_tables: list[str] = field(default_factory=list)
    unknown_tables: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    explain: list[str] = field(default_factory=list)


def _catalog_sets() -> tuple[set[str], dict[str, set[str]]]:
    tables = set(schema_def.all_table_names())
    cols = {t: set(schema_def.columns_of(t)) for t in tables}
    return tables, cols


def _strip_trailing_semicolon(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _has_statement_chaining(sql: str) -> bool:
    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1]
    return ";" in stripped


def _starts_readonly(sql: str) -> bool:
    return bool(re.match(r"^\s*(SELECT|WITH)\b", sql, flags=re.IGNORECASE))


def _looks_like_raw_select(sql: str) -> bool:
    text = sql.upper()
    if re.search(r"\bGROUP\s+BY\b", text) or re.search(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", text):
        return False
    return True


def _parse_with_sqlglot(sql: str, res: ValidationResult) -> None:
    try:
        import sqlglot
        from sqlglot import exp
    except Exception:
        res.warnings.append("sqlglot unavailable; using regex + SQLite binding checks only.")
        return
    try:
        parsed = sqlglot.parse(sql, read=config.SQL_DIALECT)
    except Exception as exc:  # noqa: BLE001
        res.errors.append(f"SQL parse error: {exc}")
        return
    if len(parsed) != 1 or parsed[0] is None:
        res.errors.append("Only one SQL statement is allowed.")
        return
    root = parsed[0]
    if not isinstance(root, (exp.Select, exp.With, exp.Union)):
        res.errors.append("Only SELECT/WITH/UNION read queries are allowed.")
    for select in root.find_all(exp.Select):
        has_star = any(isinstance(e, exp.Star) for e in select.expressions)
        if has_star and not select.args.get("limit") and not root.args.get("limit"):
            res.errors.append("SELECT * without LIMIT is not allowed.")


def _diacritic_identifier_check(sql: str, res: ValidationResult) -> None:
    try:
        import sqlglot
        from sqlglot import exp
    except Exception:
        return
    try:
        parsed = sqlglot.parse_one(sql, read=config.SQL_DIALECT)
    except Exception:
        return
    if parsed is None:
        return
    bad: list[str] = []
    for ident in parsed.find_all(exp.Identifier):
        name = ident.name or ""
        if has_diacritics(name) and name not in bad:
            bad.append(name)
    if bad:
        res.errors.append(
            "SQL identifiers must be khong dau snake_case (no Vietnamese diacritics): "
            + ", ".join(bad)
        )


_SQL_KEYWORDS = {
    "ON", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "CROSS", "FULL", "NATURAL",
    "GROUP", "ORDER", "LIMIT", "HAVING", "USING", "AS", "UNION", "WITH", "SELECT",
}
# The optional alias must NOT be a reserved word (else "JOIN"/"ON" gets eaten as an
# alias and the next table is skipped).
_FROM_JOIN_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+({_IDENT})(?:\s+(?:AS\s+)?(?!(?:{'|'.join(_SQL_KEYWORDS)})\b)({_IDENT}))?",
    flags=re.IGNORECASE,
)


def _alias_map_regex(sql: str) -> tuple[dict[str, str], list[str]]:
    aliases: dict[str, str] = {}
    referenced: list[str] = []
    for m in _FROM_JOIN_RE.finditer(sql):
        table = m.group(1)
        alias = m.group(2)
        referenced.append(table)
        if alias and alias.upper() not in _SQL_KEYWORDS:
            aliases[alias] = table
        aliases[table] = table
    return aliases, referenced


def _alias_map(sql: str) -> tuple[dict[str, str], list[str]]:
    """Prefer the sqlglot AST (handles aliases/subqueries correctly); fall back to regex."""
    try:
        import sqlglot
        from sqlglot import exp
        parsed = sqlglot.parse_one(sql, read=config.SQL_DIALECT)
        if parsed is None:
            return _alias_map_regex(sql)
        aliases: dict[str, str] = {}
        referenced: list[str] = []
        for tbl in parsed.find_all(exp.Table):
            name = tbl.name
            if not name:
                continue
            referenced.append(name)
            aliases[name] = name
            alias = tbl.alias
            if alias:
                aliases[alias] = name
        # de-dupe while preserving order
        referenced = list(dict.fromkeys(referenced))
        return aliases, referenced
    except Exception:  # noqa: BLE001
        return _alias_map_regex(sql)


def _static_identifier_check(sql: str, res: ValidationResult) -> None:
    known_tables, known_cols = _catalog_sets()
    aliases, referenced = _alias_map(sql)

    for table in referenced:
        if table in known_tables:
            if table not in res.referenced_tables:
                res.referenced_tables.append(table)
        elif table not in res.unknown_tables:
            res.unknown_tables.append(table)

    for prefix, col in re.findall(rf"\b({_IDENT})\.({_IDENT}|\*)\b", sql):
        if col == "*":
            continue
        table = aliases.get(prefix, prefix)
        if table in known_tables and col not in known_cols[table]:
            res.unknown_columns.append(f"{prefix}.{col}")

    res.unknown_tables = sorted(set(res.unknown_tables))
    res.unknown_columns = sorted(set(res.unknown_columns))
    if res.unknown_tables:
        res.errors.append("Unknown tables: " + ", ".join(res.unknown_tables))
    if res.unknown_columns:
        res.errors.append("Unknown columns: " + ", ".join(res.unknown_columns))


def _row_counts(db_path: Path) -> dict[str, int]:
    key = str(db_path)
    if key in _ROW_COUNT_CACHE:
        return _ROW_COUNT_CACHE[key]
    counts: dict[str, int] = {}
    if db_path.exists():
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for t in schema_def.all_table_names():
                try:
                    counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except sqlite3.Error:
                    pass
        finally:
            con.close()
    _ROW_COUNT_CACHE[key] = counts
    return counts


def _binding_check(sql: str, db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.execute("EXPLAIN " + sql)
        return []
    except sqlite3.Error as exc:
        return [str(exc)]
    finally:
        con.close()


def _explain_scan_check(sql: str, db_path: Path, aliases: dict[str, str],
                        res: ValidationResult) -> None:
    if not db_path.exists():
        return
    counts = _row_counts(db_path)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
    except sqlite3.Error:
        return
    finally:
        con.close()
    for row in rows:
        detail = str(row[-1])
        res.explain.append(detail)
        m = re.search(r"\bSCAN\s+(?:TABLE\s+)?(" + _IDENT + r")\b", detail, flags=re.IGNORECASE)
        if not m:
            continue
        token = m.group(1)
        table = aliases.get(token, token)
        n = counts.get(table)
        if n and n > config.EXPLAIN_MAX_SCAN_ROWS:
            res.warnings.append(
                f"Full scan of {table} ({n} rows) exceeds soft limit "
                f"{config.EXPLAIN_MAX_SCAN_ROWS}."
            )


def validate(
    sql: str,
    *,
    resolved_tables: Optional[set[str]] = None,
    db_path: Optional[Path] = None,
    require_limit_for_raw: bool = True,
) -> ValidationResult:
    db_path = Path(db_path or config.DB_PATH)
    res = ValidationResult(ok=True)
    sql = (sql or "").strip()

    if not sql:
        res.errors.append("SQL is empty.")
        res.ok = False
        return res
    if _has_statement_chaining(sql):
        res.errors.append("Semicolon statement chaining is not allowed.")
    if not _starts_readonly(sql):
        res.errors.append("Only SELECT/WITH queries are allowed.")
    if _DANGEROUS_RE.search(sql):
        res.errors.append("Dangerous SQL keyword detected.")

    normalized = _strip_trailing_semicolon(sql)
    _parse_with_sqlglot(normalized, res)
    _diacritic_identifier_check(normalized, res)
    _static_identifier_check(normalized, res)

    # LIMIT policy -----------------------------------------------------------
    limit_match = _LIMIT_RE.search(normalized)
    if limit_match and int(limit_match.group(1)) > config.MAX_RESULT_ROWS:
        res.errors.append(
            f"LIMIT {limit_match.group(1)} exceeds max result rows {config.MAX_RESULT_ROWS}."
        )
    elif not limit_match and require_limit_for_raw and _looks_like_raw_select(normalized):
        inject = min(config.AUTO_LIMIT, config.MAX_RESULT_ROWS)
        normalized = f"{normalized} LIMIT {inject}"
        res.warnings.append(f"No LIMIT on a row query; auto-added LIMIT {inject}.")

    res.normalized_sql = normalized

    # Soft context membership check (never fails) ----------------------------
    if resolved_tables:
        for t in res.referenced_tables:
            if t not in resolved_tables:
                res.warnings.append(f"Table '{t}' was not in the retrieved context.")

    # SQLite binding + scan plan (only worth running if still structurally ok)
    if not res.errors:
        res.errors.extend(f"bind error: {e}" for e in _binding_check(normalized, db_path))
        aliases, _ = _alias_map(normalized)
        _explain_scan_check(normalized, db_path, aliases, res)

    res.ok = not res.errors
    return res
