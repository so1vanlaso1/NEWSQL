"""Select the final set of tables from retrieved hits + entities + pinned tables.

Weighted candidate gather (design §23-24). Pinned tables (follow-ups) and
entity-pinned tables (a named company/product/... lives there) are never dropped;
everything else fills the remaining budget by score. Bridge tables needed only to
connect the set are added later by the join expander, not here.
"""
from __future__ import annotations

from collections import defaultdict

from backend.common import schema_def

# Source weights (higher = stronger claim to be in the final set).
W_PINNED = 100.0
W_ENTITY = 90.0
W_METRIC = 70.0
W_JOIN_PATH = 60.0
W_TABLE_BASE = 50.0
W_COLUMN_BASE = 30.0


def resolve_tables(buckets, matched_values, pinned_tables=None, max_tables=8):
    valid = set(schema_def.all_table_names())
    score: dict[str, float] = defaultdict(float)
    reason: dict[str, list[str]] = defaultdict(list)

    must: set[str] = set()  # unconditional keeps (pinned + entity)

    for t in (pinned_tables or []):
        if t in valid:
            score[t] += W_PINNED
            reason[t].append("pinned")
            must.add(t)

    for mv in matched_values:
        if mv.table in valid:
            score[mv.table] += W_ENTITY
            reason[mv.table].append(f"entity:{mv.value}")
            must.add(mv.table)

    for h in buckets.get("metric", []):
        metric = h.metadata.get("metric", "")
        for t in h.metadata.get("required_tables", []):
            if t in valid:
                score[t] += W_METRIC
                reason[t].append(f"metric:{metric}")

    for h in buckets.get("join_path", []):
        name = h.metadata.get("name", "")
        for t in h.metadata.get("tables", []):
            if t in valid:
                score[t] += W_JOIN_PATH
                reason[t].append(f"join_path:{name}")

    for rank, h in enumerate(buckets.get("table", [])):
        t = h.metadata.get("table", "")
        if t in valid:
            score[t] += max(1.0, W_TABLE_BASE - rank)
            reason[t].append(f"table_hit#{rank}")

    for rank, h in enumerate(buckets.get("column", [])):
        t = h.metadata.get("table", "")
        if t in valid:
            score[t] += max(0.0, W_COLUMN_BASE - rank)
            reason[t].append(f"column_hit#{rank}")

    ranked = sorted(score.keys(), key=lambda t: (-score[t], t))

    # A table backed only by a column hit is a weak candidate: one semantically
    # near column shouldn't pull its whole table into the budget. Fill the budget
    # with must + strong-signal tables first; only pad with weak (column-only)
    # tables if the strong set is thin (so simple/narrow questions still resolve).
    def _strong(t: str) -> bool:
        return any(not r.startswith("column_hit") for r in reason[t])

    keep = [t for t in ranked if t in must]
    for t in ranked:  # strong tables by score
        if len(keep) >= max_tables:
            break
        if t not in keep and _strong(t):
            keep.append(t)
    if len(keep) < 3:  # pad with weak candidates only when we have too few
        for t in ranked:
            if len(keep) >= max_tables:
                break
            if t not in keep:
                keep.append(t)
    return keep, {t: reason[t] for t in keep}
