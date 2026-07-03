"""SQLite connection + schema for the editable Knowledge Storage (`knowledge.db`).

One row per knowledge entry. `body` holds the type-specific fields as JSON;
`embedding_text` is the exact text that gets embedded; `content_hash` lets a save
skip re-embedding when nothing that affects the vector changed.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from backend import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_entries (
    id             TEXT PRIMARY KEY,
    type           TEXT NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    body           TEXT NOT NULL DEFAULT '{}',
    embedding_text TEXT NOT NULL DEFAULT '',
    enabled        INTEGER NOT NULL DEFAULT 1,
    embed_status   TEXT NOT NULL DEFAULT 'pending',
    embed_error    TEXT NOT NULL DEFAULT '',
    content_hash   TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_knowledge_type ON knowledge_entries(type);
CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge_entries(embed_status);

-- Phase 10: monotonic knowledge-base version. RetrievalService.ensure_fresh() reads
-- this once per turn and rebuilds its derived caches when it changes -> live edits.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
INSERT OR IGNORE INTO meta (key, value) VALUES ('kb_version', '0');

-- Phase 10: audit trail. Every create/update/delete/restore writes one row so nothing
-- is ever silently lost and any prior version can be restored.
CREATE TABLE IF NOT EXISTS entry_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id   TEXT NOT NULL,
    action     TEXT NOT NULL,          -- create | update | delete | restore
    old_body   TEXT,                   -- JSON, null on create
    new_body   TEXT,                   -- JSON, null on delete
    changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entry_history_entry ON entry_history(entry_id);
"""


def get_connection(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or config.KNOWLEDGE_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(path: Path | None = None) -> None:
    con = get_connection(path)
    try:
        # CREATE TABLE IF NOT EXISTS makes this safe on a pre-Phase-10 knowledge.db:
        # the meta/entry_history tables are simply added to the existing file.
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()
