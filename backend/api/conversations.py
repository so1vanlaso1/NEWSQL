"""Persistent chat-session endpoints.

The conversation memory (``conversations.db``) already stores every turn; these
endpoints expose it so the chat UI can keep a durable session list that survives
reloads and "new chat":

``GET    /api/conversations``        -> newest-first session summaries (sidebar).
``GET    /api/conversations/{id}``   -> one session re-rendered as user/assistant turns,
                                        including each turn's persisted model input log.
``PATCH  /api/conversations/{id}``   -> rename a session.
``DELETE /api/conversations/{id}``   -> delete a session and all its turns.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.memory.models import Turn
from backend.memory.store import get_conversation_store

router = APIRouter(tags=["conversations"])


class ConversationSummary(BaseModel):
    id: str
    title: str = ""
    created_at: str = ""
    updated_at: str = ""
    turn_count: int = 0


class HistoryTurn(BaseModel):
    """One persisted turn, shaped so the UI can re-render it like a live turn."""
    turn_id: str
    user_question: str
    intent: str = ""
    needs_sql: bool = False
    answer: str = ""
    standalone_question: str = ""
    sql: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    tables_used: list[str] = Field(default_factory=list)
    metrics_used: list[str] = Field(default_factory=list)
    filters_used: list[str] = Field(default_factory=list)
    result_summary: str = ""
    error: str = ""
    llm_model: str = ""
    llm_skill_context: str = ""
    llm_system_prompt: str = ""
    llm_user_prompt: str = ""
    llm_raw_response: str = ""
    created_at: str = ""


class ConversationDetail(BaseModel):
    id: str
    title: str = ""
    created_at: str = ""
    updated_at: str = ""
    turns: list[HistoryTurn] = Field(default_factory=list)


class RenameRequest(BaseModel):
    title: str


def _to_history_turn(t: Turn) -> HistoryTurn:
    return HistoryTurn(
        turn_id=t.turn_id,
        user_question=t.user_question,
        intent=t.intent,
        needs_sql=t.needs_sql,
        answer=t.answer or t.answer_from_memory,
        standalone_question=t.standalone_question,
        sql=t.generated_sql,
        columns=t.result_columns,
        rows=t.display_rows,
        row_count=t.row_count,
        truncated=t.truncated,
        tables_used=t.selected_tables,
        metrics_used=t.selected_metrics,
        filters_used=t.selected_filters,
        result_summary=t.result_summary,
        error=t.error,
        llm_model=t.llm_model,
        llm_skill_context=t.llm_skill_context,
        llm_system_prompt=t.llm_system_prompt,
        llm_user_prompt=t.llm_user_prompt,
        llm_raw_response=t.llm_raw_response,
        created_at=t.created_at,
    )


@router.get("/conversations", response_model=list[ConversationSummary])
def list_conversations():
    return get_conversation_store().list_conversations()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str):
    store = get_conversation_store()
    meta = store.get_conversation(conversation_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    turns = store.load_all(conversation_id)
    title = meta.get("title") or (turns[0].user_question if turns else "")
    return ConversationDetail(
        id=meta["id"], title=title,
        created_at=meta.get("created_at", ""), updated_at=meta.get("updated_at", ""),
        turns=[_to_history_turn(t) for t in turns],
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(conversation_id: str, req: RenameRequest):
    store = get_conversation_store()
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="title must not be empty")
    if not store.rename(conversation_id, req.title):
        raise HTTPException(status_code=404, detail="conversation not found")
    meta = store.get_conversation(conversation_id) or {}
    return ConversationSummary(
        id=conversation_id, title=meta.get("title", req.title.strip()),
        created_at=meta.get("created_at", ""), updated_at=meta.get("updated_at", ""))


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    ok = get_conversation_store().delete_conversation(conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"deleted": True, "id": conversation_id}
