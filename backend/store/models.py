


"""Pydantic schemas: per-type `body` validation, id derivation, API request/response.

Entry types
-----------
- ``table``       one FMCG table (meaning, use-when, columns, allowed joins)
- ``column``      one column (meaning, aliases, use-when)
- ``metric``      a business metric (formula + required tables/joins)
- ``join_path``   a named multi-table join path
- ``value``       a user-nameable entity value (company/customer/product/... name)
- ``rule``        a global SQL/normalization rule (NOT embedded; renders into skill.md)

Only ``table|column|metric|join_path|value`` are embedded (see EMBEDDABLE_TYPES).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

ENTRY_TYPES = ("table", "column", "metric", "join_path", "value", "rule")
EMBEDDABLE_TYPES = frozenset({"table", "column", "metric", "join_path", "value"})


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "item"


# ---- per-type body models ---------------------------------------------------
class TableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str
    meaning: str = ""
    meaning_en: str = ""
    use_when: list[str] = Field(default_factory=list)
    dont_use_when: list[str] = Field(default_factory=list)
    primary_key: str = ""
    columns: list[str] = Field(default_factory=list)
    allowed_joins: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    retrieval_text: str = ""
    common_values: dict[str, list[str]] = Field(default_factory=dict)


class ColumnBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str
    column: str
    data_type: str = ""
    meaning: str = ""
    aliases: list[str] = Field(default_factory=list)
    use_when: list[str] = Field(default_factory=list)


class MetricBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    aliases: list[str] = Field(default_factory=list)
    formula: str
    required_tables: list[str] = Field(default_factory=list)
    required_joins: list[str] = Field(default_factory=list)
    use_when: str = ""
    notes: str = ""


class JoinPathBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    tables: list[str] = Field(default_factory=list)
    joins: list[str] = Field(default_factory=list)
    use_when: str = ""


class ValueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str
    column: str
    value: str
    id_column: str = ""
    id_value: str = ""
    aliases: list[str] = Field(default_factory=list)
    use_when: str = ""


class RuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: str = "global"       # global | normalization | dialect | data_window | metric_policy
    title: str = ""
    content: str = ""
    items: list[str] = Field(default_factory=list)


_BODY_MODEL = {
    "table": TableBody,
    "column": ColumnBody,
    "metric": MetricBody,
    "join_path": JoinPathBody,
    "value": ValueBody,
    "rule": RuleBody,
}


def validate_body(entry_type: str, body: dict) -> dict:
    """Validate + normalize a body dict for the given type. Raises ValueError."""
    if entry_type not in _BODY_MODEL:
        raise ValueError(f"unknown entry type: {entry_type!r} (expected one of {ENTRY_TYPES})")
    try:
        model = _BODY_MODEL[entry_type](**(body or {}))
    except Exception as exc:  # pydantic ValidationError -> ValueError for the API
        raise ValueError(f"invalid {entry_type} body: {exc}") from exc
    return model.model_dump()


def derive_id(entry_type: str, body: dict) -> str:
    """Deterministic id from the body's natural key."""
    b = body or {}
    if entry_type == "table":
        return f"table:{b.get('table','')}"
    if entry_type == "column":
        return f"column:{b.get('table','')}.{b.get('column','')}"
    if entry_type == "metric":
        return f"metric:{b.get('metric','')}"
    if entry_type == "join_path":
        return f"join_path:{b.get('name','')}"
    if entry_type == "value":
        key = b.get("id_value") or b.get("value", "")
        return f"value:{b.get('table','')}.{b.get('column','')}:{key}"
    if entry_type == "rule":
        return f"rule:{b.get('section','global')}:{_slug(b.get('title',''))}"
    raise ValueError(f"unknown entry type: {entry_type!r}")


def default_name(entry_type: str, body: dict) -> str:
    b = body or {}
    if entry_type == "table":
        return str(b.get("table", ""))
    if entry_type == "column":
        return f"{b.get('table','')}.{b.get('column','')}"
    if entry_type == "metric":
        return str(b.get("metric", ""))
    if entry_type == "join_path":
        return str(b.get("name", ""))
    if entry_type == "value":
        return str(b.get("value", ""))
    if entry_type == "rule":
        return str(b.get("title", "")) or str(b.get("section", "rule"))
    return ""


# ---- API request / response models -----------------------------------------
class EntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Optional[str] = None          # derived from body when omitted
    type: str
    name: Optional[str] = None        # defaulted from body when omitted
    body: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class EntryOut(BaseModel):
    id: str
    type: str
    name: str
    body: dict[str, Any]
    enabled: bool
    embed_status: str
    embed_error: str
    content_hash: str
    created_at: str
    updated_at: str


class SaveResult(BaseModel):
    entry: EntryOut
    embedded: bool
    embed_status: str
    embed_error: str = ""
