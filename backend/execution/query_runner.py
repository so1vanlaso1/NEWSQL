"""Read-only SQL execution (Phase 8).

Runs a *validated* SELECT against ``sales.db`` over a ``mode=ro`` connection (a second
line of defense on top of the validator - even a mistaken write cannot mutate data). A
progress handler enforces a wall-clock budget, and one extra row is fetched to detect
truncation. Rows are returned as JSON-safe dicts. Never raises: errors surface on
``QueryResult.error``.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Optional

from backend import config


@dataclass
class QueryResult:
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: int = 0
    error: Optional[str] = None


def _json_safe(value):
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _make_deadline_handler(deadline: float):
    def _handler():
        # Returning non-zero from a progress handler aborts the current query.
        return 1 if time.monotonic() > deadline else 0

    return _handler


def run_query(
    sql: str,
    *,
    db_path: Optional[Path] = None,
    max_rows: Optional[int] = None,
    timeout_sec: Optional[float] = None,
) -> QueryResult:
    db_path = Path(db_path or config.DB_PATH)
    max_rows = max_rows or config.MAX_RESULT_ROWS
    timeout_sec = timeout_sec if timeout_sec is not None else config.QUERY_TIMEOUT_SEC

    if not db_path.exists():
        return QueryResult(error=f"database not found: {db_path}")

    started = time.time()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    try:
        con.row_factory = sqlite3.Row
        # ~every 1000 VM steps, check the deadline (cheap, coarse-grained timeout).
        con.set_progress_handler(_make_deadline_handler(time.monotonic() + timeout_sec), 1000)
        cur = con.execute(sql)
        fetched = cur.fetchmany(max_rows + 1)
        columns = [d[0] for d in cur.description] if cur.description else []
        truncated = len(fetched) > max_rows
        kept = fetched[:max_rows]
        rows = [{c: _json_safe(r[c]) for c in columns} for r in kept]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=int((time.time() - started) * 1000),
        )
    except sqlite3.OperationalError as exc:
        msg = str(exc)
        if "interrupted" in msg.lower():
            msg = f"query exceeded the {timeout_sec:g}s time limit"
        return QueryResult(error=msg, elapsed_ms=int((time.time() - started) * 1000))
    except sqlite3.Error as exc:
        return QueryResult(error=str(exc), elapsed_ms=int((time.time() - started) * 1000))
    finally:
        con.close()
