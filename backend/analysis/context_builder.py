"""Assemble an AnalyticContext (plan §11.2).

Reuses the shared ``RetrievalService`` (same embedder + index + hot-reloaded caches as the
normal pipeline) for schema retrieval, then layers the analytic buckets (playbooks,
dimensions, caveats), the analytic extensions of the retrieved metrics, the chart-rule
policy (loaded fresh, non-embedded), the review seed, and a compact memory window.

Pure assembly — no LLM. The planner (Phase 13) consumes this; the ``/api/analysis/plan``
tester renders it directly.
"""
from __future__ import annotations

from typing import Optional

from backend import config
from backend.analysis.models import AnalyticContext, ReviewSeed
from backend.memory.models import Turn
from backend.retrieval import vector_retriever
from backend.retrieval.context_builder import RetrievalService
from backend.retrieval.query_expander import expand_query

# Metric analytic-extension fields worth surfacing to the planner/advisor (plan §10.2).
_METRIC_EXT_FIELDS = (
    "direction", "decomposition", "default_comparisons", "default_dimensions",
    "interpretation_up", "interpretation_down",
)
_MEMORY_SUMMARY_TURNS = 3


def _bodies_by_id(rsvc: RetrievalService, type_: str) -> dict[str, dict]:
    # Honor the enabled flag (repo.list returns disabled rows too). A disabled entry is
    # already removed from the index so it won't be a hit, but filtering here keeps the
    # convention explicit and safe if that ever drifts.
    return {e["id"]: e["body"] for e in rsvc.repo.list(type_=type_) if e.get("enabled", True)}


def _hit_bodies(hits, bodies_by_id: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    for h in hits or []:
        body = bodies_by_id.get(h.metadata.get("entry_id", ""))
        if body is not None:
            out.append(body)
    return out


def _metric_analysis(rsvc: RetrievalService, schema_context) -> list[dict]:
    """Analytic extensions for the retrieved metrics (only metrics that have any set)."""
    out: list[dict] = []
    for m in getattr(schema_context, "metrics", []) or []:
        body = rsvc.metric_defs.get(m.metric)
        if not body:
            continue
        ext = {f: body.get(f) for f in _METRIC_EXT_FIELDS if body.get(f)}
        if ext:
            out.append({"metric": m.metric, **ext})
    return out


def _recent_summaries(recent_turns: Optional[list[Turn]]) -> list[str]:
    out: list[str] = []
    for t in (recent_turns or [])[-_MEMORY_SUMMARY_TURNS:]:
        q = (t.user_question or "").strip()
        if not q:
            continue
        summary = (t.result_summary or t.answer or "").strip()
        out.append(f"Hỏi: {q}" + (f" | {summary}" if summary else ""))
    return out


def build_analytic_context(
    rsvc: RetrievalService,
    question: str,
    *,
    mode: str = "",
    retrieval_query: Optional[str] = None,
    pinned_tables: Optional[list[str]] = None,
    review_seed: Optional[ReviewSeed] = None,
    recent_turns: Optional[list[Turn]] = None,
) -> AnalyticContext:
    """Build the analytic context for a review. ``retrieval_query`` defaults to ``question``
    but a seeded review passes a richer query (question + source question + entity name)."""
    rsvc.ensure_fresh()  # apply any KB edits before this review (no restart)
    query = retrieval_query or question

    schema_context = rsvc.retrieve(query, pinned_tables or [])

    # Analytic buckets over the same shared index (playbook/caveat/dimension only).
    exp = expand_query(query, rsvc.norm_map)
    buckets = vector_retriever.retrieve_buckets(
        rsvc.embedder, rsvc.index, exp.text, topk=vector_retriever.analytic_topk())

    playbooks = _hit_bodies(buckets.get("playbook"), _bodies_by_id(rsvc, "playbook"))
    dimensions = _hit_bodies(buckets.get("dimension"), _bodies_by_id(rsvc, "dimension"))
    caveats = _hit_bodies(buckets.get("caveat"), _bodies_by_id(rsvc, "caveat"))

    # chart_rules are policy (non-embedded): load all enabled ones fresh.
    chart_rules = [e["body"] for e in rsvc.repo.list(type_="chart_rule") if e.get("enabled", True)]

    return AnalyticContext(
        question=question,
        mode=mode,
        schema_context=schema_context,
        playbooks=playbooks,
        dimensions=dimensions,
        caveats=caveats,
        metric_analysis=_metric_analysis(rsvc, schema_context),
        chart_rules=chart_rules,
        review_seed=review_seed,
        recent_turn_summaries=_recent_summaries(recent_turns),
        data_window={"min": config.DATA_MIN_DATE, "max": config.DATA_MAX_DATE},
    )


def build_retrieval_query(question: str, seed: Optional[ReviewSeed]) -> str:
    """Retrieval query for a review: the question, enriched with the seed's source question
    and target-entity name when scoping to a previous result (so the entity's schema +
    metrics are retrieved alongside the question's)."""
    parts = [question]
    if seed and seed.ok:
        parts.append(seed.source_question)
        if seed.target_entity:
            parts.append(seed.target_entity.name_value)
    return " ".join(p for p in parts if p).strip()
