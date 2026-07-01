"""ConversationStore: CRUD over conversations.db (dict/Turn in, Turn out).

Mirrors store/repository.py conventions: a new connection per op, closed in a
finally block. List/dict turn fields are JSON-serialized on write and parsed back
into a ``Turn`` on read.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.memory import db
from backend.memory.models import ResultEntity, Turn

_JSON_LIST_COLS = ("selected_tables", "selected_columns", "selected_metrics",
                   "selected_filters", "result_columns", "result_preview", "result_entities")

_COLUMNS = (
    "turn_id", "conversation_id", "turn_index", "user_question", "normalized_question",
    "standalone_question", "intent", "needs_sql", "selected_tables", "selected_columns",
    "selected_metrics", "selected_filters", "generated_sql", "result_columns",
    "result_preview", "result_entities", "result_summary", "answer_from_memory", "created_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _load(text: str):
    try:
        return json.loads(text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


def _row_to_turn(row) -> Turn:
    d = dict(row)
    entities = [ResultEntity(**e) if isinstance(e, dict) else ResultEntity()
                for e in _load(d.get("result_entities"))]
    return Turn(
        turn_id=d["turn_id"],
        conversation_id=d["conversation_id"],
        turn_index=int(d.get("turn_index", 0)),
        user_question=d.get("user_question", ""),
        normalized_question=d.get("normalized_question", ""),
        standalone_question=d.get("standalone_question", ""),
        intent=d.get("intent", ""),
        needs_sql=bool(d.get("needs_sql", 0)),
        selected_tables=_load(d.get("selected_tables")),
        selected_columns=_load(d.get("selected_columns")),
        selected_metrics=_load(d.get("selected_metrics")),
        selected_filters=_load(d.get("selected_filters")),
        generated_sql=d.get("generated_sql", ""),
        result_columns=_load(d.get("result_columns")),
        result_preview=_load(d.get("result_preview")),
        result_entities=entities,
        result_summary=d.get("result_summary", ""),
        answer_from_memory=d.get("answer_from_memory", ""),
        created_at=d.get("created_at", ""),
    )


class ConversationStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        db.init_db(self.path)

    # ---- conversations ----
    def create(self, title: str = "") -> str:
        cid = uuid.uuid4().hex[:16]
        now = _now()
        con = db.get_connection(self.path)
        try:
            con.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (cid, title, now, now))
            con.commit()
        finally:
            con.close()
        return cid

    def _ensure_conversation(self, con, cid: str) -> None:
        now = _now()
        con.execute(
            "INSERT OR IGNORE INTO conversations (id, title, created_at, updated_at) "
            "VALUES (?, '', ?, ?)", (cid, now, now))
        con.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, cid))

    def _next_index(self, con, cid: str) -> int:
        row = con.execute(
            "SELECT COALESCE(MAX(turn_index), -1) AS m FROM turns WHERE conversation_id = ?",
            (cid,)).fetchone()
        return int(row["m"]) + 1

    # ---- turns ----
    def _save(self, turn: Turn) -> Turn:
        con = db.get_connection(self.path)
        try:
            self._ensure_conversation(con, turn.conversation_id)
            idx = self._next_index(con, turn.conversation_id)
            record = {
                "turn_id": turn.turn_id,
                "conversation_id": turn.conversation_id,
                "turn_index": idx,
                "user_question": turn.user_question,
                "normalized_question": turn.normalized_question,
                "standalone_question": turn.standalone_question,
                "intent": turn.intent,
                "needs_sql": 1 if turn.needs_sql else 0,
                "selected_tables": _dump(turn.selected_tables),
                "selected_columns": _dump(turn.selected_columns),
                "selected_metrics": _dump(turn.selected_metrics),
                "selected_filters": _dump(turn.selected_filters),
                "generated_sql": turn.generated_sql,
                "result_columns": _dump(turn.result_columns),
                "result_preview": _dump(turn.result_preview),
                "result_entities": _dump([e.model_dump() for e in turn.result_entities]),
                "result_summary": turn.result_summary,
                "answer_from_memory": turn.answer_from_memory,
                "created_at": turn.created_at or _now(),
            }
            placeholders = ", ".join("?" for _ in _COLUMNS)
            con.execute(
                f"INSERT OR REPLACE INTO turns ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
                [record[c] for c in _COLUMNS])
            con.commit()
        finally:
            con.close()
        return self.get(turn.turn_id)  # type: ignore[return-value]

    def save_sql_turn(self, conversation_id: str, user_question: str, *,
                      normalized_question: str = "", standalone_question: str = "",
                      intent: str = "NEW_QUERY", selected_tables=None, selected_columns=None,
                      selected_metrics=None, selected_filters=None, generated_sql: str = "",
                      result_columns=None, result_preview=None, result_entities=None,
                      result_summary: str = "") -> Turn:
        turn = Turn(
            turn_id=uuid.uuid4().hex, conversation_id=conversation_id,
            user_question=user_question, normalized_question=normalized_question,
            standalone_question=standalone_question, intent=intent, needs_sql=True,
            selected_tables=selected_tables or [], selected_columns=selected_columns or [],
            selected_metrics=selected_metrics or [], selected_filters=selected_filters or [],
            generated_sql=generated_sql, result_columns=result_columns or [],
            result_preview=result_preview or [], result_entities=result_entities or [],
            result_summary=result_summary)
        return self._save(turn)

    def save_non_sql_turn(self, conversation_id: str, user_question: str, *,
                          normalized_question: str = "", standalone_question: str = "",
                          intent: str = "", answer_from_memory: str = "") -> Turn:
        turn = Turn(
            turn_id=uuid.uuid4().hex, conversation_id=conversation_id,
            user_question=user_question, normalized_question=normalized_question,
            standalone_question=standalone_question, intent=intent, needs_sql=False,
            answer_from_memory=answer_from_memory)
        return self._save(turn)

    def get(self, turn_id: str) -> Optional[Turn]:
        con = db.get_connection(self.path)
        try:
            row = con.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            return _row_to_turn(row) if row else None
        finally:
            con.close()

    def load_recent(self, conversation_id: str, limit: int | None = None) -> list[Turn]:
        from backend import config
        limit = limit or config.MEMORY_RECENT_TURNS
        con = db.get_connection(self.path)
        try:
            rows = con.execute(
                "SELECT * FROM turns WHERE conversation_id = ? ORDER BY turn_index DESC LIMIT ?",
                (conversation_id, limit)).fetchall()
        finally:
            con.close()
        return [_row_to_turn(r) for r in reversed(rows)]  # chronological
