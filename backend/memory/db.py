"""SQLite connection + schema for conversation memory (``conversations.db``).

A SEPARATE database from knowledge.db and from the read-only sales.db. One row per
conversation and one row per turn; list/dict turn fields are stored as JSON text.
Mirrors the store/db.py conventions (row_factory=Row, new connection per op).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from backend import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS turns (
    turn_id             TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL,
    turn_index          INTEGER NOT NULL DEFAULT 0,
    user_question       TEXT NOT NULL DEFAULT '',
    normalized_question TEXT NOT NULL DEFAULT '',
    standalone_question TEXT NOT NULL DEFAULT '',
    intent              TEXT NOT NULL DEFAULT '',
    needs_sql           INTEGER NOT NULL DEFAULT 0,
    selected_tables     TEXT NOT NULL DEFAULT '[]',
    selected_columns    TEXT NOT NULL DEFAULT '[]',
    selected_metrics    TEXT NOT NULL DEFAULT '[]',
    selected_filters    TEXT NOT NULL DEFAULT '[]',
    generated_sql       TEXT NOT NULL DEFAULT '',
    result_columns      TEXT NOT NULL DEFAULT '[]',
    result_preview      TEXT NOT NULL DEFAULT '[]',
    result_entities     TEXT NOT NULL DEFAULT '[]',
    result_summary      TEXT NOT NULL DEFAULT '',
    review_id           TEXT NOT NULL DEFAULT '',
    answer_from_memory  TEXT NOT NULL DEFAULT '',
    -- Re-display + model-input log (added for persistent chat sessions).
    answer              TEXT NOT NULL DEFAULT '',
    display_rows        TEXT NOT NULL DEFAULT '[]',
    row_count           INTEGER NOT NULL DEFAULT 0,
    truncated           INTEGER NOT NULL DEFAULT 0,
    error               TEXT NOT NULL DEFAULT '',
    llm_model           TEXT NOT NULL DEFAULT '',
    llm_skill_context   TEXT NOT NULL DEFAULT '',
    llm_system_prompt   TEXT NOT NULL DEFAULT '',
    llm_user_prompt     TEXT NOT NULL DEFAULT '',
    llm_raw_response    TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_turns_conv ON turns(conversation_id, turn_index);
"""

# Columns added after the first release; each is applied to a pre-existing turns table
# via ALTER TABLE ... ADD COLUMN so an already-populated conversations.db keeps working.
_MIGRATION_COLUMNS = {
    "review_id": "TEXT NOT NULL DEFAULT ''",
    "answer": "TEXT NOT NULL DEFAULT ''",
    "display_rows": "TEXT NOT NULL DEFAULT '[]'",
    "row_count": "INTEGER NOT NULL DEFAULT 0",
    "truncated": "INTEGER NOT NULL DEFAULT 0",
    "error": "TEXT NOT NULL DEFAULT ''",
    "llm_model": "TEXT NOT NULL DEFAULT ''",
    "llm_skill_context": "TEXT NOT NULL DEFAULT ''",
    "llm_system_prompt": "TEXT NOT NULL DEFAULT ''",
    "llm_user_prompt": "TEXT NOT NULL DEFAULT ''",
    "llm_raw_response": "TEXT NOT NULL DEFAULT ''",
}


def get_connection(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or config.CONV_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # timeout: wait (rather than immediately raising "database is locked") when a concurrent
    # writer holds the lock — conversations.db is written from FastAPI's threadpool.
    con = sqlite3.connect(path, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _migrate(con: sqlite3.Connection) -> None:
    existing = {r["name"] for r in con.execute("PRAGMA table_info(turns)").fetchall()}
    for col, decl in _MIGRATION_COLUMNS.items():
        if col not in existing:
            con.execute(f"ALTER TABLE turns ADD COLUMN {col} {decl}")


def init_db(path: Path | None = None) -> None:
    con = get_connection(path)
    try:
        con.executescript(SCHEMA)
        _migrate(con)
        con.commit()
    finally:
        con.close()
