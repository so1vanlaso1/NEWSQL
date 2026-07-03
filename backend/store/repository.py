"""Pure CRUD over `knowledge.db` (dict in / dict out, no pydantic, no embedding).

Embedding orchestration lives in `backend.knowledge.service`; this layer only
persists rows. Each entry is a dict with keys: id, type, name, body(dict),
embedding_text, enabled(bool), embed_status, embed_error, content_hash,
created_at, updated_at.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.store import db

_COLUMNS = [
    "id", "type", "name", "body", "embedding_text", "enabled",
    "embed_status", "embed_error", "content_hash", "created_at", "updated_at",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_entry(row) -> dict:
    d = dict(row)
    try:
        d["body"] = json.loads(d.get("body") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["body"] = {}
    d["enabled"] = bool(d.get("enabled", 1))
    return d


class Repository:
    def __init__(self, path: Path | None = None):
        self.path = path
        db.init_db(self.path)

    # ---- reads ----
    def get(self, entry_id: str) -> Optional[dict]:
        con = db.get_connection(self.path)
        try:
            row = con.execute(
                "SELECT * FROM knowledge_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            return _row_to_entry(row) if row else None
        finally:
            con.close()

    def list(
        self,
        type_: Optional[str] = None,
        query: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        clauses, params = [], []
        if type_:
            clauses.append("type = ?")
            params.append(type_)
        if status:
            clauses.append("embed_status = ?")
            params.append(status)
        if query:
            clauses.append("(LOWER(id) LIKE ? OR LOWER(name) LIKE ? OR LOWER(embedding_text) LIKE ?)")
            like = f"%{query.lower()}%"
            params += [like, like, like]
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        con = db.get_connection(self.path)
        try:
            rows = con.execute(
                f"SELECT * FROM knowledge_entries{where} ORDER BY type, id", params
            ).fetchall()
            return [_row_to_entry(r) for r in rows]
        finally:
            con.close()

    def all(self) -> list[dict]:
        return self.list()

    def counts_by_type(self) -> dict[str, int]:
        con = db.get_connection(self.path)
        try:
            rows = con.execute(
                "SELECT type, COUNT(*) AS n FROM knowledge_entries GROUP BY type"
            ).fetchall()
            return {str(r["type"]): int(r["n"]) for r in rows}
        finally:
            con.close()

    def counts_by_status(self) -> dict[str, int]:
        con = db.get_connection(self.path)
        try:
            rows = con.execute(
                "SELECT embed_status, COUNT(*) AS n FROM knowledge_entries GROUP BY embed_status"
            ).fetchall()
            return {str(r["embed_status"]): int(r["n"]) for r in rows}
        finally:
            con.close()

    # ---- writes ----
    def upsert(self, entry: dict) -> dict:
        """Insert or replace an entry. Preserves created_at on updates."""
        existing = self.get(entry["id"])
        now = _now()
        record = {
            "id": entry["id"],
            "type": entry["type"],
            "name": entry.get("name", ""),
            "body": json.dumps(entry.get("body", {}), ensure_ascii=False),
            "embedding_text": entry.get("embedding_text", ""),
            "enabled": 1 if entry.get("enabled", True) else 0,
            "embed_status": entry.get("embed_status", "pending"),
            "embed_error": entry.get("embed_error", "") or "",
            "content_hash": entry.get("content_hash", ""),
            "created_at": (existing or {}).get("created_at") or entry.get("created_at") or now,
            "updated_at": now,
        }
        con = db.get_connection(self.path)
        try:
            placeholders = ", ".join("?" for _ in _COLUMNS)
            con.execute(
                f"INSERT OR REPLACE INTO knowledge_entries ({', '.join(_COLUMNS)}) "
                f"VALUES ({placeholders})",
                [record[c] for c in _COLUMNS],
            )
            con.commit()
        finally:
            con.close()
        return self.get(entry["id"])  # type: ignore[return-value]

    def set_status(self, entry_id: str, status: str, error: str = "") -> None:
        con = db.get_connection(self.path)
        try:
            con.execute(
                "UPDATE knowledge_entries SET embed_status = ?, embed_error = ?, updated_at = ? "
                "WHERE id = ?",
                (status, error or "", _now(), entry_id),
            )
            con.commit()
        finally:
            con.close()

    def delete(self, entry_id: str) -> bool:
        con = db.get_connection(self.path)
        try:
            cur = con.execute("DELETE FROM knowledge_entries WHERE id = ?", (entry_id,))
            con.commit()
            return cur.rowcount > 0
        finally:
            con.close()

    def clear(self) -> None:
        con = db.get_connection(self.path)
        try:
            con.execute("DELETE FROM knowledge_entries")
            con.commit()
        finally:
            con.close()

    # ---- kb_version (Phase 10 hot-reload) ----
    def get_kb_version(self) -> int:
        con = db.get_connection(self.path)
        try:
            row = con.execute("SELECT value FROM meta WHERE key = 'kb_version'").fetchone()
            if row is None:
                return 0
            try:
                return int(row["value"])
            except (TypeError, ValueError):
                return 0
        finally:
            con.close()

    def bump_kb_version(self) -> int:
        """Atomically increment kb_version and return the new value."""
        con = db.get_connection(self.path)
        try:
            con.execute(
                "INSERT INTO meta (key, value) VALUES ('kb_version', '1') "
                "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)"
            )
            con.commit()
            row = con.execute("SELECT value FROM meta WHERE key = 'kb_version'").fetchone()
            return int(row["value"]) if row else 0
        finally:
            con.close()

    # ---- entry history (Phase 10 audit + restore) ----
    def record_history(self, entry_id: str, action: str,
                       old_body: Optional[dict] = None, new_body: Optional[dict] = None) -> None:
        con = db.get_connection(self.path)
        try:
            con.execute(
                "INSERT INTO entry_history (entry_id, action, old_body, new_body, changed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    entry_id, action,
                    json.dumps(old_body, ensure_ascii=False) if old_body is not None else None,
                    json.dumps(new_body, ensure_ascii=False) if new_body is not None else None,
                    _now(),
                ),
            )
            con.commit()
        finally:
            con.close()

    def list_history(self, entry_id: str) -> list[dict]:
        """History rows for an entry, newest first, with bodies parsed back to dicts."""
        con = db.get_connection(self.path)
        try:
            rows = con.execute(
                "SELECT * FROM entry_history WHERE entry_id = ? ORDER BY history_id DESC",
                (entry_id,),
            ).fetchall()
        finally:
            con.close()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            for key in ("old_body", "new_body"):
                if d.get(key):
                    try:
                        d[key] = json.loads(d[key])
                    except (json.JSONDecodeError, TypeError):
                        d[key] = None
                else:
                    d[key] = None
            out.append(d)
        return out

    def get_history_row(self, history_id: int) -> Optional[dict]:
        con = db.get_connection(self.path)
        try:
            row = con.execute(
                "SELECT * FROM entry_history WHERE history_id = ?", (history_id,)
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        d = dict(row)
        for key in ("old_body", "new_body"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = None
            else:
                d[key] = None
        return d
