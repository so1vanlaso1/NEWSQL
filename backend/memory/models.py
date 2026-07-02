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
    # Re-display + model-input log (persistent chat sessions).
    answer: str = ""                                     # final composed VN answer shown to the user
    display_rows: list[dict] = Field(default_factory=list)  # rows kept for re-rendering old sessions
    row_count: int = 0
    truncated: bool = False
    error: str = ""
    llm_model: str = ""
    llm_skill_context: str = ""                          # the §27 compact context (what Chat Plan shows)
    llm_system_prompt: str = ""
    llm_user_prompt: str = ""
    llm_raw_response: str = ""                           # raw JSON the model returned
    created_at: str = ""

    def is_sql_turn(self) -> bool:
        return bool(self.needs_sql and self.generated_sql)
