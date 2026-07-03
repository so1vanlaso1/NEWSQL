"""Render a knowledge entry into (a) the exact text that gets embedded and (b) the
vector-index metadata. Formats follow the SQLNEW plan sections 8-13.

Pure functions of a single entry dict -> so a save re-embeds deterministically.
Only EMBEDDABLE_TYPES produce embedding text; ``rule`` entries return "".
"""
from __future__ import annotations

from typing import Optional

from backend.store.models import EMBEDDABLE_TYPES


def is_embeddable(entry_type: str) -> bool:
    return entry_type in EMBEDDABLE_TYPES


def _join_summary(joins: list[str]) -> list[str]:
    """"cong_ty.cong_ty_id = don_hang_ban.cong_ty_id" -> "cong_ty to don_hang_ban"."""
    pairs: list[str] = []
    for j in joins or []:
        left, _, right = str(j).partition("=")
        lt = left.strip().split(".")[0]
        rt = right.strip().split(".")[0]
        if lt and rt:
            pairs.append(f"{lt} to {rt}")
    return pairs


def _csv(items) -> str:
    return ", ".join(str(x) for x in items if str(x).strip())


def build_embedding_text(entry: dict) -> str:
    t = entry["type"]
    b = entry.get("body", {}) or {}
    if t == "table":
        lines = [
            "TYPE: table",
            f"TABLE: {b.get('table','')}",
            f"MEANING: {b.get('meaning','')}".rstrip(),
        ]
        if b.get("meaning_en"):
            lines.append(f"MEANING_EN: {b['meaning_en']}")
        if b.get("aliases"):
            lines.append(f"VIETNAMESE_ALIASES: {_csv(b['aliases'])}")
        if b.get("use_when"):
            lines.append(f"USE_WHEN: {_csv(b['use_when'])}")
        if b.get("dont_use_when"):
            lines.append(f"DO_NOT_USE_ALONE_WHEN: {_csv(b['dont_use_when'])}")
        if b.get("columns"):
            lines.append(f"COLUMNS: {_csv(b['columns'])}")
        joins = _join_summary(b.get("allowed_joins", []))
        if joins:
            lines.append(f"JOINS: {_csv(joins)}")
        if b.get("retrieval_text"):
            lines.append(f"RETRIEVAL: {b['retrieval_text']}")
        return "\n".join(lines)

    if t == "column":
        lines = [
            "TYPE: column",
            f"TABLE: {b.get('table','')}",
            f"COLUMN: {b.get('column','')}",
        ]
        if b.get("data_type"):
            lines.append(f"DATA_TYPE: {b['data_type']}")
        if b.get("meaning"):
            lines.append(f"MEANING: {b['meaning']}")
        if b.get("aliases"):
            lines.append(f"VIETNAMESE_ALIASES: {_csv(b['aliases'])}")
        if b.get("use_when"):
            lines.append(f"USE_WHEN: {_csv(b['use_when'])}")
        return "\n".join(lines)

    if t == "metric":
        lines = [
            "TYPE: metric",
            f"METRIC: {b.get('metric','')}",
        ]
        if b.get("aliases"):
            lines.append(f"ALIASES: {_csv(b['aliases'])}")
        lines.append(f"FORMULA: {b.get('formula','')}")
        if b.get("required_tables"):
            lines.append(f"REQUIRED_TABLES: {_csv(b['required_tables'])}")
        if b.get("required_joins"):
            lines.append("REQUIRED_JOIN: " + "; ".join(b["required_joins"]))
        if b.get("use_when"):
            lines.append(f"USE_WHEN: {b['use_when']}")
        if b.get("notes"):
            lines.append(f"NOTES: {b['notes']}")
        return "\n".join(lines)

    if t == "join_path":
        lines = [
            "TYPE: join_path",
            f"NAME: {b.get('name','')}",
        ]
        if b.get("use_when"):
            lines.append(f"USE_WHEN: {b['use_when']}")
        if b.get("tables"):
            lines.append(f"REQUIRED_TABLES: {_csv(b['tables'])}")
        if b.get("joins"):
            lines.append("JOINS:")
            lines.extend(str(j) for j in b["joins"])
        return "\n".join(lines)

    if t == "value":
        lines = [
            "TYPE: value",
            f"VALUE: {b.get('value','')}",
            f"TABLE: {b.get('table','')}",
            f"COLUMN: {b.get('column','')}",
        ]
        if b.get("id_column"):
            lines.append(f"ID_COLUMN: {b['id_column']}")
        if b.get("id_value"):
            lines.append(f"ID_VALUE: {b['id_value']}")
        if b.get("aliases"):
            lines.append(f"ALIASES: {_csv(b['aliases'])}")
        if b.get("use_when"):
            lines.append(f"USE_WHEN: {b['use_when']}")
        return "\n".join(lines)

    # ---- Phase 11 analytic types (plan §10.3) -------------------------------
    if t == "playbook":
        # Embed use_when + aliases + step titles + main_metrics (drives retrieval).
        lines = [
            "TYPE: playbook",
            f"PLAYBOOK: {b.get('playbook','')}",
            f"KIND: {b.get('kind','')}",
        ]
        if b.get("use_when"):
            lines.append(f"USE_WHEN: {b['use_when']}")
        if b.get("aliases"):
            lines.append(f"ALIASES: {_csv(b['aliases'])}")
        if b.get("main_metrics"):
            lines.append(f"MAIN_METRICS: {_csv(b['main_metrics'])}")
        step_titles = [str(s.get("title", "")) for s in (b.get("diagnostic_steps") or [])
                       if str(s.get("title", "")).strip()]
        if step_titles:
            lines.append(f"STEPS: {_csv(step_titles)}")
        return "\n".join(lines)

    if t == "dimension":
        # Embed aliases + table.column + use_when.
        lines = [
            "TYPE: dimension",
            f"DIMENSION: {b.get('dimension','')}",
        ]
        if b.get("aliases"):
            lines.append(f"ALIASES: {_csv(b['aliases'])}")
        lines.append(f"COLUMN: {b.get('table','')}.{b.get('column','')}")
        if b.get("use_when"):
            lines.append(f"USE_WHEN: {b['use_when']}")
        return "\n".join(lines)

    if t == "caveat":
        # Embed title + content + applies_to_*.
        lines = [
            "TYPE: caveat",
            f"CAVEAT: {b.get('title','')}",
        ]
        if b.get("content"):
            lines.append(f"CONTENT: {b['content']}")
        if b.get("applies_to_metrics"):
            lines.append(f"APPLIES_TO_METRICS: {_csv(b['applies_to_metrics'])}")
        if b.get("applies_to_tables"):
            lines.append(f"APPLIES_TO_TABLES: {_csv(b['applies_to_tables'])}")
        if b.get("aliases"):
            lines.append(f"ALIASES: {_csv(b['aliases'])}")
        return "\n".join(lines)

    # rule, chart_rule (and any non-embeddable): no embedding text
    return ""


def build_metadata(entry: dict) -> dict:
    t = entry["type"]
    b = entry.get("body", {}) or {}
    meta: dict = {"type": t, "entry_id": entry["id"]}
    if t == "table":
        meta["table"] = b.get("table", "")
    elif t == "column":
        meta["table"] = b.get("table", "")
        meta["column"] = b.get("column", "")
    elif t == "metric":
        meta["metric"] = b.get("metric", "")
        meta["required_tables"] = list(b.get("required_tables", []))
    elif t == "join_path":
        meta["name"] = b.get("name", "")
        meta["tables"] = list(b.get("tables", []))
    elif t == "value":
        meta["table"] = b.get("table", "")
        meta["column"] = b.get("column", "")
        meta["id_column"] = b.get("id_column", "")
        meta["id_value"] = b.get("id_value", "")
        meta["value"] = b.get("value", "")
    elif t == "playbook":
        meta["playbook"] = b.get("playbook", "")
        meta["kind"] = b.get("kind", "")
    elif t == "dimension":
        meta["dimension"] = b.get("dimension", "")
        meta["table"] = b.get("table", "")
        meta["column"] = b.get("column", "")
    elif t == "caveat":
        meta["caveat"] = b.get("title", "")
        meta["severity"] = b.get("severity", "info")
    return meta
