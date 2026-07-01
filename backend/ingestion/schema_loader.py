"""Introspect the live SQLite `sales.db`.

Produces `schema_snapshot.json` (real columns/types/PKs, row counts, common values)
and pulls distinct values for the value-source columns used to build ``value``
embedding docs. Modeled on the old pipeline's schema_catalog.py but self-contained
(no joined-table machinery).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from backend import config
from backend.common import schema_def


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(db_path or config.DB_PATH)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def common_values(con: sqlite3.Connection, table: str, column: str, limit: int = 5) -> list[Any]:
    try:
        rows = con.execute(
            f"SELECT {_quote(column)} AS v, COUNT(*) AS n FROM {_quote(table)} "
            f"WHERE {_quote(column)} IS NOT NULL GROUP BY {_quote(column)} "
            f"ORDER BY n DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [r["v"] for r in rows]


def distinct_values(
    con: sqlite3.Connection, table: str, column: str, limit: int, id_column: str | None = None
) -> list[dict]:
    """Distinct values (optionally with their id) for building value docs."""
    cols = _quote(column) + (f", {_quote(id_column)}" if id_column else "")
    try:
        rows = con.execute(
            f"SELECT DISTINCT {cols} FROM {_quote(table)} WHERE {_quote(column)} IS NOT NULL "
            f"ORDER BY {_quote(column)} LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        item = {"value": r[column]}
        if id_column:
            item["id_value"] = r[id_column]
        out.append(item)
    return out


def data_date_range(con: sqlite3.Connection) -> tuple[Optional[str], Optional[str]]:
    try:
        row = con.execute(
            "SELECT MIN(ngay_dat_hang) AS lo, MAX(ngay_dat_hang) AS hi FROM don_hang_ban"
        ).fetchone()
        return (row["lo"], row["hi"]) if row else (None, None)
    except sqlite3.Error:
        return (None, None)


def load_snapshot(db_path: Path | None = None, common_value_limit: int | None = None) -> dict:
    cv_limit = config.COMMON_VALUE_LIMIT if common_value_limit is None else common_value_limit
    con = _connect(db_path)
    try:
        tables: dict[str, dict] = {}
        for name in schema_def.all_table_names():
            info = con.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
            columns = []
            for col in info:
                cname = str(col["name"])
                columns.append(
                    {
                        "name": cname,
                        "data_type": str(col["type"] or ""),
                        "primary_key": bool(col["pk"]),
                        "nullable": not bool(col["notnull"]),
                        "common_values": [str(v) for v in common_values(con, name, cname, cv_limit)],
                    }
                )
            try:
                row_count = con.execute(f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0]
            except sqlite3.Error:
                row_count = 0
            tables[name] = {"name": name, "row_count": int(row_count), "columns": columns}
        lo, hi = data_date_range(con)
        return {
            "database": str(Path(db_path or config.DB_PATH)),
            "dialect": config.SQL_DIALECT,
            "data_min_date": lo or config.DATA_MIN_DATE,
            "data_max_date": hi or config.DATA_MAX_DATE,
            "tables": tables,
        }
    finally:
        con.close()


def save_snapshot(snapshot: dict, path: Path | None = None) -> Path:
    path = Path(path or config.SCHEMA_SNAPSHOT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def collect_value_rows(
    value_sources: list[dict], db_path: Path | None = None, limit: int | None = None
) -> list[dict]:
    """For each configured value source, pull distinct values + ids from the DB.

    ``value_sources`` items: {table, column, id_column?}. Returns flat list of dicts
    with table/column/id_column/id_value/value ready to become value entries.
    """
    lim = config.VALUE_SAMPLE_LIMIT if limit is None else limit
    con = _connect(db_path)
    out: list[dict] = []
    try:
        for src in value_sources:
            table, column = src["table"], src["column"]
            id_col = src.get("id_column")
            for item in distinct_values(con, table, column, lim, id_col):
                out.append(
                    {
                        "table": table,
                        "column": column,
                        "id_column": id_col or "",
                        "id_value": str(item.get("id_value", "") or ""),
                        "value": str(item["value"]),
                    }
                )
    finally:
        con.close()
    return out


if __name__ == "__main__":
    snap = load_snapshot()
    out = save_snapshot(snap)
    print(f"[snapshot] {len(snap['tables'])} tables, "
          f"data {snap['data_min_date']}..{snap['data_max_date']} -> {out}")
