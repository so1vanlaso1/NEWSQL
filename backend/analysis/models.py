"""Pydantic shapes for the analytic pipeline (plan §8.3, §11.2).

Kept pydantic (not bare dataclasses) so the ``/api/analysis/plan`` tester serializes them
directly and they nest the existing ``ResolvedContext``. Every field defaults so a partial
context/seed is always constructible (fallback-everywhere, §2.2).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from backend.retrieval.models import ResolvedContext


class TargetEntity(BaseModel):
    """The entity a previous-result analysis is scoped to (plan §8.3)."""
    type: str = ""            # owning table, e.g. "khach_hang"
    rank: int = 0             # 1-based position in the previous result
    id_column: str = ""
    id_value: str = ""
    name_column: str = ""
    name_value: str = ""


class ReviewSeed(BaseModel):
    """Seed for ANALYTIC_FROM_PREVIOUS_RESULT — pins one entity from the last SQL turn.

    ``ok=False`` (+ ``reason``) means the reference could not be resolved safely; the
    controller then returns an insufficient-context answer instead of guessing (§8.2).
    """
    ok: bool = False
    reason: str = ""
    source_turn_id: str = ""
    source_question: str = ""
    source_sql: str = ""
    target_entity: Optional[TargetEntity] = None
    base_metrics: list[str] = Field(default_factory=list)
    base_filters: list[str] = Field(default_factory=list)
    base_tables: list[str] = Field(default_factory=list)
    base_fact: str = ""

    def entity_filter_sql(self) -> str:
        """A trailing WHERE fragment pinning the target entity (for {entity_filter}).

        Empty when there is no resolved entity/id — a fresh analysis leaves the filter out.
        """
        e = self.target_entity
        if not e or not e.id_column or not e.id_value:
            return ""
        safe = str(e.id_value).replace("'", "''")
        return f"AND {e.id_column} = '{safe}'"


class AnalyticContext(BaseModel):
    """Everything the planner needs, assembled deterministically (plan §11.2)."""
    question: str = ""
    mode: str = ""
    schema_context: Optional[ResolvedContext] = None
    playbooks: list[dict] = Field(default_factory=list)
    dimensions: list[dict] = Field(default_factory=list)
    caveats: list[dict] = Field(default_factory=list)
    metric_analysis: list[dict] = Field(default_factory=list)   # analytic ext. of retrieved metrics
    chart_rules: list[dict] = Field(default_factory=list)       # loaded fresh (non-embedded policy)
    review_seed: Optional[ReviewSeed] = None
    recent_turn_summaries: list[str] = Field(default_factory=list)
    data_window: dict = Field(default_factory=dict)


# ---- Phase 13: review planner + task runner (plan §13-14) -------------------
class DateWindow(BaseModel):
    """The current period vs its comparison period, resolved once per review.

    Serialized to the planner (as a suggestion) and consumed by the fallback pack for
    placeholder substitution ({date_from}/{date_to}/{compare_from}/{compare_to}).
    """
    date_from: str = ""
    date_to: str = ""
    compare_from: str = ""
    compare_to: str = ""
    label: str = ""            # human label, e.g. "2025-03"
    compare_label: str = ""    # e.g. "2025-02"

    def to_contract(self) -> dict:
        """The §13.2 ``date_range`` JSON shape (from/to/compare_from/compare_to)."""
        return {"from": self.date_from, "to": self.date_to,
                "compare_from": self.compare_from, "compare_to": self.compare_to}


# The four DiagnosticStep expected_shape values, and the chart shape each maps to
# (chart_rule entries are keyed by chart shape, plan §17.1).
EXPECTED_SHAPES = ("kpi", "by_dimension", "trend", "top_n")
SHAPE_TO_CHART_SHAPE = {
    "kpi": "kpi_comparison",
    "by_dimension": "composition",
    "trend": "trend",
    "top_n": "top_n",
}


class PlannedTask(BaseModel):
    """One validated diagnostic SQL task (plan §13.2 task object)."""
    task_id: str = ""
    title: str = ""
    purpose: str = ""
    expected_shape: str = "kpi"     # one of EXPECTED_SHAPES
    sql: str = ""                   # validator-normalized SQL
    metric: str = ""                # optional provenance (which metric it measures)
    dimension: str = ""             # optional provenance (which dimension it groups by)


class ReviewPlan(BaseModel):
    """The planner's output after the validation ladder (plan §13.3)."""
    analysis_title: str = ""
    playbook_used: str = ""
    mode_downgrade: Optional[str] = None      # "NORMAL_SQL" re-routes to the normal pipeline
    date_window: Optional[DateWindow] = None
    tasks: list[PlannedTask] = Field(default_factory=list)
    source: str = "llm"                       # llm | llm_repair | fallback
    dropped: list[str] = Field(default_factory=list)   # notes on invalid/duplicate tasks
    notes: str = ""

    @property
    def is_downgrade(self) -> bool:
        return (self.mode_downgrade or "").upper() == "NORMAL_SQL"


class TaskResult(BaseModel):
    """A planned task after execution (plan §14 output)."""
    task_id: str = ""
    title: str = ""
    purpose: str = ""
    expected_shape: str = "kpi"
    sql: str = ""
    metric: str = ""
    dimension: str = ""
    status: str = "success"         # success | failed | skipped
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    repaired: bool = False
    error: str = ""


# ---- Phase 14: profiled evidence + chart specs + persisted review -----------
class EvidenceItem(BaseModel):
    """A profiled, provenance-tagged piece of evidence (plan §15.2)."""
    evidence_id: str = ""
    review_id: str = ""
    task_id: str = ""
    kind: str = "raw"               # chart shape: kpi_comparison|composition|trend|top_n|raw
    source_type: str = "sql"        # sql | web  (hard column, never parsed from text)
    metric: str = ""                # the metric this evidence measures (drives money/unit)
    title: str = ""
    purpose: str = ""
    sql: str = ""                   # the task SQL (null for web evidence)
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)   # capped at ANALYTIC_EVIDENCE_MAX_ROWS
    profile: dict = Field(default_factory=dict)
    web: Optional[dict] = None      # {query,url,source_title,snippet,...} for source_type=web
    chart_id: str = ""
    status: str = "success"         # success | failed | skipped
    created_at: str = ""


class ChartSeries(BaseModel):
    name: str = ""
    value_field: str = ""


class ChartSpec(BaseModel):
    """A deterministic chart specification (plan §17.2)."""
    chart_id: str = ""
    type: str = "none"              # grouped_bar|line|horizontal_bar|stacked_bar|none
    title: str = ""
    x_field: str = ""
    series: list[ChartSeries] = Field(default_factory=list)
    data: list[dict] = Field(default_factory=list)
    unit: str = ""
    evidence_id: str = ""
    notes: str = ""


class ReviewRecord(BaseModel):
    """A complete persisted review (plan §20.1 reviews + evidence, joined)."""
    review_id: str = ""
    conversation_id: str = ""
    turn_id: str = ""
    mode: str = ""
    question: str = ""
    review_seed: Optional[ReviewSeed] = None
    plan: Optional[ReviewPlan] = None
    findings_summary: str = ""
    report_markdown: str = ""
    evidence: list[EvidenceItem] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    follow_up_suggestions: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    status: str = "complete"        # complete | degraded | failed
    created_at: str = ""
