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
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()
