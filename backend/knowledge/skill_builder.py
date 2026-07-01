"""Render the consolidated `skill.md` (Phase 1) from the knowledge store.

skill.md is a *view* of `knowledge.db`: editing an entry in the UI and
regenerating skill.md keeps the two in sync. Sections: global rules, metrics,
join paths, and one block per table (with per-column meanings pulled from the
column entries).
"""
from __future__ import annotations

from pathlib import Path

from backend import config
from backend.common import schema_def
from backend.store.repository import Repository

_RULE_ORDER = ["dialect", "global", "normalization", "data_window", "metric_policy"]


def _by_id(entries: list[dict]) -> dict[str, dict]:
    return {e["id"]: e for e in entries}


def _render_rule(e: dict) -> list[str]:
    b = e["body"]
    out = [f"## {b.get('title') or b.get('section','Rule')}", ""]
    if b.get("content"):
        out += [b["content"], ""]
    for item in b.get("items", []):
        out.append(f"- {item}")
    if b.get("items"):
        out.append("")
    return out


def _render_metric(e: dict) -> list[str]:
    b = e["body"]
    out = [f"## Metric: {b.get('metric','')}", ""]
    if b.get("aliases"):
        out.append(f"Aliases: {', '.join(b['aliases'])}")
    out += ["", "Formula:", "```sql", b.get("formula", ""), "```"]
    if b.get("required_tables"):
        out.append(f"Required tables: {', '.join(b['required_tables'])}")
    if b.get("required_joins"):
        out.append("Required join: " + "; ".join(b["required_joins"]))
    if b.get("use_when"):
        out.append(f"Use when: {b['use_when']}")
    if b.get("notes"):
        out.append(f"Notes: {b['notes']}")
    out.append("")
    return out


def _render_join_path(e: dict) -> list[str]:
    b = e["body"]
    out = [f"## Join Path: {b.get('name','')}", ""]
    if b.get("use_when"):
        out.append(f"Use when: {b['use_when']}")
    if b.get("tables"):
        out.append(f"Required tables: {', '.join(b['tables'])}")
    if b.get("joins"):
        out.append("Joins:")
        out += [f"- {j}" for j in b["joins"]]
    out.append("")
    return out


def _render_table(e: dict, columns_by_key: dict[str, dict]) -> list[str]:
    b = e["body"]
    name = b.get("table", "")
    out = [f"## Table: {name}", ""]
    out += ["### Business meaning", b.get("meaning", "")]
    if b.get("meaning_en"):
        out.append(b["meaning_en"])
    out.append("")
    if b.get("use_when"):
        out += ["### Use this table when"] + [f"- {x}" for x in b["use_when"]] + [""]
    if b.get("dont_use_when"):
        out += ["### Do not use this table alone when"] + [f"- {x}" for x in b["dont_use_when"]] + [""]
    out += ["### Primary key", b.get("primary_key", "") or "None", ""]

    out.append("### Columns")
    for col in b.get("columns", []):
        centry = columns_by_key.get(f"column:{name}.{col}")
        cb = centry["body"] if centry else {}
        dtype = cb.get("data_type", "")
        meaning = cb.get("meaning", "")
        suffix = f": {meaning}" if meaning else ""
        out.append(f"- {col} ({dtype}){suffix}" if dtype else f"- {col}{suffix}")
    out.append("")

    if b.get("allowed_joins"):
        out += ["### Allowed joins"] + [f"- {j}" for j in b["allowed_joins"]] + [""]

    cv = b.get("common_values") or {}
    if cv:
        out.append("### Common values")
        for col, vals in cv.items():
            out.append(f"- {col}: {', '.join(str(v) for v in vals)}")
        out.append("")

    if b.get("retrieval_text"):
        out += ["### Retrieval text", b["retrieval_text"], ""]
    return out


def render_skill_md(repo: Repository | None = None) -> str:
    repo = repo or Repository()
    entries = repo.all()
    by_id = _by_id(entries)
    rules = [e for e in entries if e["type"] == "rule"]
    metrics = [e for e in entries if e["type"] == "metric"]
    join_paths = [e for e in entries if e["type"] == "join_path"]
    tables = {e["body"].get("table"): e for e in entries if e["type"] == "table"}

    lines: list[str] = ["# Database Skill: FMCG Sales Database (SQLNEW)", ""]

    # Rules in canonical order, then any extras.
    rules_by_section: dict[str, list[dict]] = {}
    for r in rules:
        rules_by_section.setdefault(r["body"].get("section", "global"), []).append(r)
    ordered_sections = _RULE_ORDER + [s for s in rules_by_section if s not in _RULE_ORDER]
    for section in ordered_sections:
        for r in rules_by_section.get(section, []):
            lines += _render_rule(r)

    if metrics:
        lines += ["---", "", "# Metrics", ""]
        for m in sorted(metrics, key=lambda e: e["body"].get("metric", "")):
            lines += _render_metric(m)

    if join_paths:
        lines += ["---", "", "# Join Paths", ""]
        for jp in sorted(join_paths, key=lambda e: e["body"].get("name", "")):
            lines += _render_join_path(jp)

    lines += ["---", "", "# Tables", ""]
    for name in schema_def.all_table_names():  # stable, meaningful order
        if name in tables:
            lines += _render_table(tables[name], by_id)

    return "\n".join(lines).rstrip() + "\n"


def write_skill_md(path: Path | None = None, repo: Repository | None = None) -> Path:
    path = Path(path or config.SKILL_MD_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_skill_md(repo), encoding="utf-8")
    return path


if __name__ == "__main__":
    out = write_skill_md()
    print(f"[skill] wrote {out}")
