"""Pydantic shapes for conversation memory (one ``Turn`` per user turn)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ResultEntity(BaseModel):
    type: str = ""          # owning table, e.g. "khach_hang"
    id_column: str = ""
    id_value: str = ""
    name_column: str = ""
    name_value: str = ""


class Turn(BaseModel):
    turn_id: str
    conversation_id: str
    turn_index: int = 0
    user_question: str = ""
    normalized_question: str = ""
    standalone_question: str = ""
    intent: str = ""
    needs_sql: bool = False
    selected_tables: list[str] = Field(default_factory=list)
    selected_columns: list[str] = Field(default_factory=list)
    selected_metrics: list[str] = Field(default_factory=list)
    selected_filters: list[str] = Field(default_factory=list)
    generated_sql: str = ""
    result_columns: list[str] = Field(default_factory=list)
    result_preview: list[dict] = Field(default_factory=list)
    result_entities: list[ResultEntity] = Field(default_factory=list)
    result_summary: str = ""
    answer_from_memory: str = ""
    created_at: str = ""

    def is_sql_turn(self) -> bool:
        return bool(self.needs_sql and self.generated_sql)
