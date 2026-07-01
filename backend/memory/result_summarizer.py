"""Deterministic result summarization + entity extraction (NO LLM).

Keeps the one-LLM-per-turn budget: a mechanical summary string plus entity
extraction by column-name convention (``*_id`` paired with ``ten_*``). The
extracted top entity feeds drill-down retrieval ("what products did they buy?").
"""
from __future__ import annotations

from backend.common import schema_def
from backend.memory.models import ResultEntity

_ID_SUFFIX = "_id"
_NAME_PREFIX = "ten_"


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _entity_type(id_column: str) -> str:
    base = id_column[:-len(_ID_SUFFIX)] if id_column.endswith(_ID_SUFFIX) else id_column
    if base in set(schema_def.all_table_names()):
        return base
    owners = [t for t in schema_def.all_table_names() if id_column in schema_def.columns_of(t)]
    return owners[0] if len(owners) == 1 else base


def extract_entities(columns: list[str], rows: list[dict]) -> list[ResultEntity]:
    """Entities for the top row (most relevant for drill-down)."""
    if not rows:
        return []
    first = rows[0]
    id_cols = [c for c in columns if c.endswith(_ID_SUFFIX)]
    name_cols = [c for c in columns if c.startswith(_NAME_PREFIX)]
    out: list[ResultEntity] = []
    for idc in id_cols:
        base = idc[:-len(_ID_SUFFIX)]
        name_col = next((n for n in name_cols if n == f"{_NAME_PREFIX}{base}"), None)
        if name_col is None and len(name_cols) == 1:
            name_col = name_cols[0]
        out.append(ResultEntity(
            type=_entity_type(idc),
            id_column=idc,
            id_value=str(first.get(idc, "")),
            name_column=name_col or "",
            name_value=str(first.get(name_col, "")) if name_col else "",
        ))
    return out


def summarize(columns: list[str], rows: list[dict]) -> str:
    n = len(rows)
    if n == 0:
        return "The query returned no rows."
    first = rows[0]
    name_col = next((c for c in columns if c.startswith(_NAME_PREFIX)), None)
    metric_col = None
    for c in reversed(columns):
        if c.endswith(_ID_SUFFIX):
            continue
        if _is_number(first.get(c)):
            metric_col = c
            break
    parts = [f"Returned {n} row{'s' if n != 1 else ''}."]
    if name_col and metric_col:
        parts.append(f"Top: {first.get(name_col)} = {first.get(metric_col)}.")
    elif name_col:
        parts.append(f"Top: {first.get(name_col)}.")
    parts.append(f"Columns: {', '.join(columns)}.")
    return " ".join(parts)
