"""Seed the knowledge store (initial Phase-1 content).

Builds one entry per table, column, metric, join-path, value, and rule from
`common/schema_def.py` + `knowledge/business_meta.py` + the live `sales.db`
snapshot, stages them into `knowledge.db`, then batch-embeds the embeddable ones.

Run:
  python -m backend.knowledge.seed                # seed + embed on the RTX 2050
  python -m backend.knowledge.seed --no-embed     # stage only (no torch needed)
  python -m backend.knowledge.seed --reset        # clear the store first
"""
from __future__ import annotations

import argparse

from backend import config
from backend.common import schema_def
from backend.common.logging import get_logger
from backend.common.vn_text import normalize_vietnamese_text
from backend.ingestion import schema_loader
from backend.knowledge import analysis_meta as am
from backend.knowledge import business_meta as bm
from backend.knowledge.service import KnowledgeService

log = get_logger(__name__)

# Columns whose distinct/common values are worth showing in skill.md (names, enums).
_DISPLAY_COLUMNS = {
    "trang_thai", "ket_qua", "nganh_hang", "don_vi_tinh", "quoc_gia", "ma_tuyen",
    "tinh_thanh", "quan_huyen", "phuong_xa", "mo_ta", "ly_do",
}


def _joins_for_table(name: str) -> list[str]:
    out: list[str] = []
    for fk in schema_def.all_foreign_keys():
        if fk["from_table"] == name or fk["to_table"] == name:
            out.append(
                f"{fk['from_table']}.{fk['from_column']} = {fk['to_table']}.{fk['to_column']}"
            )
    return out


def _table_common_values(snapshot: dict, name: str) -> dict[str, list[str]]:
    tbl = snapshot["tables"].get(name, {})
    out: dict[str, list[str]] = {}
    for col in tbl.get("columns", []):
        cname = col["name"]
        if (cname.startswith("ten_") or cname in _DISPLAY_COLUMNS) and col.get("common_values"):
            out[cname] = col["common_values"]
    return out


def _col_data_type(snapshot: dict, table: str, column: str) -> str:
    for col in snapshot["tables"].get(table, {}).get("columns", []):
        if col["name"] == column:
            return col.get("data_type", "")
    return ""


def build_entries(snapshot: dict) -> list[dict]:
    """Return a flat list of {type, body, name?, id?, enabled} to stage."""
    entries: list[dict] = []

    for name in schema_def.all_table_names():
        t = schema_def.get_table(name)
        enrich = bm.TABLE_ENRICH.get(name, {})
        joins = _joins_for_table(name)
        meaning_en = enrich.get("meaning_en", "")
        aliases = list(t.get("aliases", []))
        retrieval = (
            f"{name}: {meaning_en or t.get('description','')} "
            f"Aliases: {', '.join(aliases)}."
        )
        body = {
            "table": name,
            "meaning": t.get("description", ""),
            "meaning_en": meaning_en,
            "use_when": enrich.get("use_when", []) or aliases,
            "dont_use_when": enrich.get("dont_use_when", []),
            "primary_key": schema_def.primary_key(name),
            "columns": schema_def.columns_of(name),
            "allowed_joins": joins,
            "aliases": aliases,
            "retrieval_text": retrieval,
            "common_values": _table_common_values(snapshot, name),
        }
        entries.append({"type": "table", "body": body})

        for col in t["columns"]:
            cname = col["name"]
            key = f"{name}.{cname}"
            cenrich = bm.COLUMN_ENRICH.get(key, {})
            entries.append({"type": "column", "body": {
                "table": name,
                "column": cname,
                "data_type": _col_data_type(snapshot, name, cname) or col.get("type", ""),
                "meaning": cenrich.get("meaning") or col.get("desc", ""),
                "aliases": cenrich.get("aliases", []),
                "use_when": cenrich.get("use_when", []),
            }})

    for m in bm.METRICS:
        body = dict(m)
        # Phase 11: merge analytic extensions (direction, decomposition, ...) onto the
        # metric body so the advisor can reason about it (plan §10.2, §18).
        ext = am.METRIC_EXTENSIONS.get(m["metric"])
        if ext:
            body.update(ext)
        entries.append({"type": "metric", "body": body})

    for jp in bm.JOIN_PATHS:
        entries.append({"type": "join_path", "body": dict(jp)})

    # value docs from sampled distinct values
    for row in schema_loader.collect_value_rows(bm.VALUE_SOURCES):
        val = row["value"]
        norm = normalize_vietnamese_text(val)
        aliases = [norm] if norm and norm != val.lower() else []
        entries.append({"type": "value", "body": {
            "table": row["table"],
            "column": row["column"],
            "id_column": row["id_column"],
            "id_value": row["id_value"],
            "value": val,
            "aliases": aliases,
            "use_when": f"user mentions {val}",
        }})

    # curated enum value docs (the value IS the code)
    for ev in bm.ENUM_VALUES:
        code = ev["value"]
        aliases = list(ev.get("aliases", [])) + [code.lower()]
        entries.append({"type": "value", "body": {
            "table": ev["table"],
            "column": ev["column"],
            "id_column": "",
            "id_value": code,
            "value": code,
            "aliases": aliases,
            "use_when": ev.get("use_when", f"user mentions {code}"),
        }})

    for r in bm.rules(snapshot["data_min_date"], snapshot["data_max_date"]):
        entries.append({"type": "rule", "body": dict(r)})

    # Phase 11: analytic knowledge (playbooks, dimensions, caveats, chart_rules).
    entries.extend(seed_analysis(snapshot["data_min_date"], snapshot["data_max_date"]))

    return entries


def seed_analysis(data_min: str, data_max: str) -> list[dict]:
    """The analytic knowledge entries (plan §10.4): 4 playbooks, 8 dimensions, 6 caveats,
    5 chart_rules. Ordinary entries — staged + embedded through the normal pipeline, and
    editable in the UI. Metric extensions are applied in ``build_entries``."""
    return am.build_analysis_entries(data_min, data_max)


def run(embed: bool = True, reset: bool = False, service: "KnowledgeService | None" = None) -> dict:
    snapshot = schema_loader.load_snapshot()
    schema_loader.save_snapshot(snapshot)

    svc = service or KnowledgeService.build(load_embedder=embed)
    if reset:
        svc.repo.clear()
        # also clear the vector index so it does not keep stale rows
        for eid in list(svc.index.ids):
            svc.index.delete(eid)
        svc.index.save()

    entries = build_entries(snapshot)
    for e in entries:
        svc.stage(e["type"], e["body"], name=e.get("name"), entry_id=e.get("id"),
                  enabled=e.get("enabled", True))

    result = {"staged": len(entries), "by_type": svc.repo.counts_by_type()}
    if embed:
        result["embed"] = svc.embed_pending()
    # Seeding bulk-replaces the knowledge base; bump the version so any running
    # RetrievalService rebuilds its derived caches on the next question (Phase 10).
    try:
        svc.repo.bump_kb_version()
    except Exception:  # noqa: BLE001 - never let a version bump break the seed
        log.exception("kb_version bump after seed failed")
    log.info("staged %d entries: %s", result["staged"], result["by_type"])
    if embed:
        log.info("embedded: %s", result["embed"])
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed the SQLNEW knowledge store.")
    ap.add_argument("--no-embed", action="store_true", help="stage entries without embedding")
    ap.add_argument("--reset", action="store_true", help="clear the store and index first")
    args = ap.parse_args()
    run(embed=not args.no_embed, reset=args.reset)
    print(f"[seed] knowledge.db -> {config.KNOWLEDGE_DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
