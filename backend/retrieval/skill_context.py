"""Phase 6: serialize a ``ResolvedContext`` into the compact LLM skill context.

``build_llm_skill_context`` renders the design §27 template -- the single block a
Phase-7 LLM call consumes instead of the full ``skill.md``: SQL dialect, global +
data-window + metric-policy + normalization rules, the compact conversation memory
window, the current message (+ standalone candidate), then the retrieved metric
rules, relevant tables/columns, allowed joins, and matched values.

Pure and embedder-free (consumes an already-built ``ResolvedContext`` + strings), so
it is unit-testable without the GPU. Context-size guardrails (design §42-43) live in
``ContextLimits``: tables are never dropped (join bridges are load-bearing), but
per-table columns, focus columns, metrics, aliases, and normalization lines are
capped, and an oversized table set is flagged (never silently trimmed).

NOTE: the dialect here is SQLite (see the seeded rules), NOT the plan doc's stale
"MySQL" example text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend import config
from backend.retrieval.models import GlobalRule, ResolvedContext, ResolvedTable

_COLREF = re.compile(r"([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)")
_NONE = "None"
_NONE_FROM_MEMORY = "None (answering from memory)"


@dataclass(frozen=True)
class ContextLimits:
    max_columns_per_table: int = 14
    max_focus_columns: int = 40
    max_matched_values: int = 5
    max_metrics: int = 5
    max_aliases_per_metric: int = 6
    max_normalization_items: int = 14
    table_count_warn_threshold: int = 8

    @classmethod
    def from_config(cls) -> "ContextLimits":
        return cls(
            max_columns_per_table=config.SKILL_CTX_MAX_COLUMNS_PER_TABLE,
            max_focus_columns=config.SKILL_CTX_MAX_FOCUS_COLUMNS,
            max_matched_values=config.RETRIEVAL_MAX_VALUE_MATCHES,
            max_metrics=config.SKILL_CTX_MAX_METRICS,
            max_aliases_per_metric=config.SKILL_CTX_MAX_ALIASES_PER_METRIC,
            max_normalization_items=config.SKILL_CTX_MAX_NORMALIZATION_ITEMS,
            table_count_warn_threshold=config.SKILL_CTX_TABLE_WARN,
        )


def approx_token_count(text: str) -> int:
    """Rough size heuristic (~4 chars/token) for logging/inspection only -- never a
    truncation gate (truncation happens structurally via ContextLimits)."""
    return (len(text) + 3) // 4


# ---- rule helpers -----------------------------------------------------------
def _rules_by_section(resolved: ResolvedContext | None,
                      rules: list[GlobalRule] | None) -> dict[str, GlobalRule]:
    src = (resolved.rules if resolved is not None and resolved.rules else rules) or []
    by_section: dict[str, GlobalRule] = {}
    for r in src:
        by_section.setdefault(r.section, r)  # first wins
    return by_section


def _dialect_line(by_section: dict[str, GlobalRule], resolved: ResolvedContext | None) -> str:
    r = by_section.get("dialect")
    if r and r.content:
        return r.content
    if r and r.items:
        return "; ".join(r.items)
    return (resolved.dialect if resolved else config.SQL_DIALECT) or "sqlite"


# ---- per-table column picker (design §43) -----------------------------------
def _pick_table_columns(t: ResolvedTable, resolved: ResolvedContext,
                        limits: ContextLimits) -> tuple[list[str], int]:
    """Priority union (pk, focus, join, metric, then schema order), deduped, capped."""
    known = {c.column for c in t.columns}
    picked: list[str] = []
    seen: set[str] = set()

    def add(col: str) -> None:
        if col and col in known and col not in seen:
            seen.add(col)
            picked.append(col)

    add(t.primary_key)                                             # 1. primary key
    for c in resolved.columns:                                     # 2. focus columns
        if c.table == t.table:
            add(c.column)
    for j in resolved.joins:                                       # 3. join columns
        if j.left_table == t.table:
            add(j.left_column)
        if j.right_table == t.table:
            add(j.right_column)
    for m in resolved.metrics:                                     # 4. metric columns
        for tt, cc in _COLREF.findall(m.formula or ""):
            if tt == t.table:
                add(cc)
        for cond in m.required_joins:
            for tt, cc in _COLREF.findall(cond):
                if tt == t.table:
                    add(cc)
    for c in t.columns:                                            # 5. remaining
        add(c.column)

    truncated = max(0, len(picked) - limits.max_columns_per_table)
    return picked[:limits.max_columns_per_table], truncated


def _render_table(t: ResolvedTable, resolved: ResolvedContext, limits: ContextLimits) -> list[str]:
    lines = [f"Table: {t.table}"]
    meaning = t.meaning_en or t.meaning
    if meaning:
        lines.append(f"Meaning: {meaning}")
    cols, truncated = _pick_table_columns(t, resolved, limits)
    by_name = {c.column: c for c in t.columns}
    lines.append("Columns:")
    for col in cols:
        c = by_name.get(col)
        label = (c.meaning if c else "") or ("primary key" if c and c.is_key else "")
        lines.append(f"- {col}: {label}" if label else f"- {col}")
    if truncated:
        lines.append(f"- (+{truncated} more columns)")
    return lines


# ---- section renderers ------------------------------------------------------
def _global_rules_section(by_section: dict[str, GlobalRule], limits: ContextLimits) -> list[str]:
    blocks: list[str] = []

    g = by_section.get("global")
    glines = ["GLOBAL RULES:"]
    if g and g.items:
        glines += [f"- {it}" for it in g.items]
    elif g and g.content:
        glines.append(g.content)
    else:
        glines.append("- Use only the provided tables, columns, and joins.")
    blocks.append("\n".join(glines))

    dw = by_section.get("data_window")
    if dw and (dw.content or dw.items):
        body = dw.content or "\n".join(f"- {it}" for it in dw.items)
        blocks.append(f"DATA WINDOW:\n{body}")

    mp = by_section.get("metric_policy")
    if mp and (mp.content or mp.items):
        body = mp.content or "\n".join(f"- {it}" for it in mp.items)
        blocks.append(f"REVENUE / METRIC POLICY:\n{body}")

    nz = by_section.get("normalization")
    if nz and nz.items:
        items = nz.items[:limits.max_normalization_items]
        nlines = ["NORMALIZATION:"] + [f"- {it}" for it in items]
        if len(nz.items) > limits.max_normalization_items:
            nlines.append(f"- (+{len(nz.items) - limits.max_normalization_items} more)")
        blocks.append("\n".join(nlines))

    return blocks


def _metrics_section(resolved: ResolvedContext | None, limits: ContextLimits) -> str:
    metrics = resolved.metrics if resolved else []
    if not metrics:
        return f"RETRIEVED METRIC RULES:\n{_NONE}"
    lines = ["RETRIEVED METRIC RULES:"]
    for m in metrics[:limits.max_metrics]:
        lines.append(f"Metric: {m.metric}")
        if m.aliases:
            shown = m.aliases[:limits.max_aliases_per_metric]
            lines.append(f"Aliases: {', '.join(shown)}")
        if m.formula:
            lines.append(f"Formula: {m.formula}")
        if m.use_when:
            lines.append(f"Use when: {m.use_when}")
        if m.notes:
            lines.append(f"Notes: {m.notes}")
    return "\n".join(lines)


def _tables_section(resolved: ResolvedContext | None, limits: ContextLimits) -> str:
    tables = resolved.tables if resolved else []
    if resolved is None or not tables:
        return f"RELEVANT TABLES:\n{_NONE_FROM_MEMORY}"
    lines = ["RELEVANT TABLES:"]
    if len(tables) > limits.table_count_warn_threshold:
        lines.append(f"# NOTE: {len(tables)} tables (> {limits.table_count_warn_threshold}); "
                     f"context is large")
    for t in tables:
        lines += _render_table(t, resolved, limits)
    return "\n".join(lines)


def _columns_section(resolved: ResolvedContext | None, limits: ContextLimits) -> str:
    columns = resolved.columns if resolved else []
    if not columns:
        return f"RELEVANT COLUMNS:\n{_NONE}"
    lines = ["RELEVANT COLUMNS:"]
    for c in columns[:limits.max_focus_columns]:
        lines.append(f"- {c.table}.{c.column}: {c.meaning}" if c.meaning
                     else f"- {c.table}.{c.column}")
    if len(columns) > limits.max_focus_columns:
        lines.append(f"- (+{len(columns) - limits.max_focus_columns} more)")
    return "\n".join(lines)


def _joins_section(resolved: ResolvedContext | None) -> str:
    joins = resolved.joins if resolved else []
    if not joins:
        return f"ALLOWED JOINS:\n{_NONE}"
    return "\n".join(["ALLOWED JOINS:"] + [f"- {j.condition}" for j in joins])


def _values_section(resolved: ResolvedContext | None, limits: ContextLimits) -> str:
    values = resolved.matched_values if resolved else []
    if not values:
        return f"MATCHED VALUES:\n{_NONE}"
    lines = ["MATCHED VALUES:"]
    for v in values[:limits.max_matched_values]:
        suffix = f" ({v.id_column}={v.id_value})" if v.id_value else ""
        lines.append(f"- {v.value} -> {v.table}.{v.column}{suffix}")
    return "\n".join(lines)


# ---- public entrypoint ------------------------------------------------------
def build_llm_skill_context(
    user_message: str,
    memory_window: str,
    resolved: ResolvedContext | None,
    standalone_question: str | None = None,
    rules: list[GlobalRule] | None = None,
    limits: ContextLimits | None = None,
) -> str:
    """Serialize the compact LLM skill context (design §27).

    ``rules`` is a fallback rule list used when ``resolved`` is None (ask-about-sql /
    ask-about-result turns) so the dialect + SELECT-only guardrails are still present.
    """
    limits = limits or ContextLimits.from_config()
    by_section = _rules_by_section(resolved, rules)

    sections: list[str] = ["DATABASE SKILL CONTEXT"]
    sections.append(f"SQL DIALECT:\n{_dialect_line(by_section, resolved)}")
    sections += _global_rules_section(by_section, limits)
    sections.append(f"CONVERSATION MEMORY:\n{memory_window.strip()}")
    sections.append(f"CURRENT USER MESSAGE:\n{str(user_message).strip()}")

    sq = (standalone_question or "").strip()
    if sq and sq != str(user_message).strip():
        sections.append(f"STANDALONE QUESTION CANDIDATE:\n{sq}")

    sections.append(_metrics_section(resolved, limits))
    sections.append(_tables_section(resolved, limits))
    sections.append(_columns_section(resolved, limits))
    sections.append(_joins_section(resolved))
    sections.append(_values_section(resolved, limits))

    return "\n\n".join(sections)
