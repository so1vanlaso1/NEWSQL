"""Always-on global rules for the LLM context.

Rule entries are NOT embedded (they render into skill.md), so they can't be
retrieved semantically -- they are pulled wholesale and always attached to the
resolved context: the SQL dialect, global SELECT-only rules, the data-coverage
window (so recent-period questions anchor to MAX(ngay_dat_hang)), the revenue/
metric policy, and the Vietnamese normalization rules.
"""
from __future__ import annotations

from backend.retrieval.models import GlobalRule

GLOBAL_SECTIONS = ("dialect", "global", "data_window", "metric_policy", "normalization")


def load_global_rules(repo, sections: tuple[str, ...] = GLOBAL_SECTIONS) -> list[GlobalRule]:
    order = {s: i for i, s in enumerate(sections)}
    out: list[GlobalRule] = []
    for e in repo.list(type_="rule"):
        body = e.get("body", {}) or {}
        section = body.get("section", "global")
        if sections and section not in sections:
            continue
        out.append(GlobalRule(
            section=section,
            title=body.get("title", ""),
            content=body.get("content", ""),
            items=list(body.get("items", [])),
        ))
    out.sort(key=lambda r: order.get(r.section, 99))
    return out
