


"""Pydantic schemas: per-type `body` validation, id derivation, API request/response.

Entry types
-----------
- ``table``       one FMCG table (meaning, use-when, columns, allowed joins)
- ``column``      one column (meaning, aliases, use-when)
- ``metric``      a business metric (formula + required tables/joins)
- ``join_path``   a named multi-table join path
- ``value``       a user-nameable entity value (company/customer/product/... name)
- ``rule``        a global SQL/normalization rule (NOT embedded; renders into skill.md)
- ``playbook``    an analytic diagnostic playbook (Phase 11; embedded via ``use_when``)
- ``caveat``      an analysis caveat / data-limitation note (Phase 11; embedded)
- ``dimension``   an analysis grouping dimension (Phase 11; embedded)
- ``chart_rule``  a shape->chart mapping policy (Phase 11; NOT embedded, loaded fresh)

Only ``table|column|metric|join_path|value|playbook|caveat|dimension`` are embedded
(see EMBEDDABLE_TYPES). ``rule`` and ``chart_rule`` are policy: pulled wholesale via
kb_version rather than retrieved semantically.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.common.vn_text import normalize_vietnamese_text

# Phase 11 added the analytic types. playbook/caveat/dimension are embeddable (retrieved
# semantically); chart_rule is policy (loaded fresh via kb_version, like rule).
ENTRY_TYPES = (
    "table", "column", "metric", "join_path", "value", "rule",
    "playbook", "caveat", "dimension", "chart_rule",
)
EMBEDDABLE_TYPES = frozenset({
    "table", "column", "metric", "join_path", "value",
    "playbook", "caveat", "dimension",
})


def _slug(text: str) -> str:
    # Strip Vietnamese diacritics first so an accented title yields a clean ascii slug
    # ("Phạm vi dữ liệu" -> "pham_vi_du_lieu") instead of a mangled one. Deterministic.
    ascii_text = normalize_vietnamese_text(text) or str(text).lower()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_") or "item"


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
    # ---- Phase 11 analytic extensions (all optional so existing entries stay valid) ----
    # Consumed by the advisor (plan §18): which direction is "good", how the metric
    # decomposes, its default comparison/dimensions, and canned interpretation phrasing.
    direction: Literal["higher_is_better", "lower_is_better", "neutral"] = "higher_is_better"
    decomposition: list[str] = Field(default_factory=list)
    default_comparisons: list[str] = Field(default_factory=list)
    default_dimensions: list[str] = Field(default_factory=list)
    interpretation_down: str = ""
    interpretation_up: str = ""


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


# ---- Phase 11 analytic body models (plan §10.2) -----------------------------
class DiagnosticStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str                      # "So sánh doanh thu kỳ này với kỳ trước"
    purpose: str = ""
    metric: str = ""                # metric entry name, e.g. "doanh_thu"
    dimension: str = ""             # dimension entry slug, e.g. "category"
    expected_shape: Literal["kpi", "by_dimension", "trend", "top_n"] = "kpi"
    # optional SQL template with {date_from} {date_to} {compare_from} {compare_to}
    # {dimension_column} {entity_filter} placeholders (see fallback_packs, plan §13.4).
    sql_hint: str = ""


class PlaybookBody(BaseModel):      # id: playbook:{playbook}
    model_config = ConfigDict(extra="forbid")
    playbook: str                   # slug, e.g. "revenue_drop"
    kind: Literal["diagnostic", "comparison", "ranking", "overview"] = "diagnostic"
    aliases: list[str] = Field(default_factory=list)
    use_when: str = ""              # embedded — drives retrieval
    main_metrics: list[str] = Field(default_factory=list)
    required_comparison: Literal["previous_period", "same_period_last_year", "none"] = "previous_period"
    diagnostic_steps: list[DiagnosticStep] = Field(default_factory=list)
    interpretation_rules: list[str] = Field(default_factory=list)
    improvement_rules: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    notes: str = ""


class CaveatBody(BaseModel):        # id: caveat:{slug(title)}
    model_config = ConfigDict(extra="forbid")
    title: str
    content: str = ""               # "Dữ liệu chỉ có đến 2025-06-24 ..."
    applies_to_metrics: list[str] = Field(default_factory=list)
    applies_to_tables: list[str] = Field(default_factory=list)
    severity: Literal["info", "warning"] = "info"
    aliases: list[str] = Field(default_factory=list)


class DimensionBody(BaseModel):     # id: dimension:{dimension}
    model_config = ConfigDict(extra="forbid")
    dimension: str                  # slug, e.g. "category"
    aliases: list[str] = Field(default_factory=list)
    table: str                      # dimension table
    column: str                     # label column
    id_column: str = ""
    join_requirement: str = ""      # join path name needed to reach fact tables
    drill_down_to: list[str] = Field(default_factory=list)
    use_when: str = ""


class ChartRuleBody(BaseModel):     # id: chart_rule:{shape}
    model_config = ConfigDict(extra="forbid")
    shape: Literal["kpi_comparison", "trend", "top_n", "composition", "raw"]
    chart_type: Literal["grouped_bar", "line", "horizontal_bar", "stacked_bar", "none"]
    max_categories: int = 12
    min_rows: int = 2
    notes: str = ""


_BODY_MODEL = {
    "table": TableBody,
    "column": ColumnBody,
    "metric": MetricBody,
    "join_path": JoinPathBody,
    "value": ValueBody,
    "rule": RuleBody,
    "playbook": PlaybookBody,
    "caveat": CaveatBody,
    "dimension": DimensionBody,
    "chart_rule": ChartRuleBody,
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
    if entry_type == "playbook":
        return f"playbook:{b.get('playbook','')}"
    if entry_type == "caveat":
        return f"caveat:{_slug(b.get('title',''))}"
    if entry_type == "dimension":
        return f"dimension:{b.get('dimension','')}"
    if entry_type == "chart_rule":
        return f"chart_rule:{b.get('shape','')}"
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
    if entry_type == "playbook":
        return str(b.get("playbook", ""))
    if entry_type == "caveat":
        return str(b.get("title", ""))
    if entry_type == "dimension":
        return str(b.get("dimension", ""))
    if entry_type == "chart_rule":
        return str(b.get("shape", ""))
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
