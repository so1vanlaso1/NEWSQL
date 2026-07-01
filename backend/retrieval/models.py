"""Pydantic shapes for the query-time retrieval result (``ResolvedContext``).

These are the structured facts a later phase (Phase 6) will serialize into the
compact LLM skill context. Kept deliberately flat and JSON-friendly so the debug
``/api/retrieve`` endpoint and the frontend tester can render them directly.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ResolvedColumn(BaseModel):
    table: str
    column: str
    data_type: str = ""
    meaning: str = ""
    is_key: bool = False


class ResolvedTable(BaseModel):
    table: str
    meaning: str = ""
    meaning_en: str = ""
    primary_key: str = ""
    columns: list[ResolvedColumn] = Field(default_factory=list)
    reason: str = ""  # why this table was selected (explainability)


class ResolvedMetric(BaseModel):
    metric: str
    formula: str = ""
    aliases: list[str] = Field(default_factory=list)
    required_tables: list[str] = Field(default_factory=list)
    required_joins: list[str] = Field(default_factory=list)
    use_when: str = ""
    notes: str = ""
    score: float = 0.0


class ResolvedJoin(BaseModel):
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    condition: str
    source: str = "fk_graph"  # "fk_graph" | "join_path:<name>"


class MatchedValue(BaseModel):
    table: str
    column: str
    value: str
    id_column: str = ""
    id_value: str = ""
    matched_alias: str = ""
    match_kind: str = "exact"  # exact | enum


class GlobalRule(BaseModel):
    section: str
    title: str = ""
    content: str = ""
    items: list[str] = Field(default_factory=list)


class ResolvedContext(BaseModel):
    dialect: str
    retrieval_query: str = ""
    pinned_tables: list[str] = Field(default_factory=list)
    final_tables: list[str] = Field(default_factory=list)
    tables: list[ResolvedTable] = Field(default_factory=list)
    columns: list[ResolvedColumn] = Field(default_factory=list)  # focus columns (flat)
    metrics: list[ResolvedMetric] = Field(default_factory=list)
    joins: list[ResolvedJoin] = Field(default_factory=list)
    matched_values: list[MatchedValue] = Field(default_factory=list)
    rules: list[GlobalRule] = Field(default_factory=list)
    debug: dict = Field(default_factory=dict)
