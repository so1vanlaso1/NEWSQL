"""RetrievalService: the public Phase 3 seam.

``retrieve(retrieval_query, pinned_tables)`` runs the full pipeline
(normalize/expand -> per-type vector buckets -> exact value pin -> table resolve
-> FK join expand -> focus columns) and returns a ``ResolvedContext``. Reuses the
already-loaded embedder + index from the KnowledgeService (no second model load);
the normalization map, value-alias index, global rules, and schema defs are cached
once at construction.
"""
from __future__ import annotations

import re

from backend import config
from backend.common import schema_def
from backend.retrieval import join_expander, table_resolver, vector_retriever
from backend.retrieval.models import (
    ResolvedColumn,
    ResolvedContext,
    ResolvedMetric,
    ResolvedTable,
)
from backend.retrieval.query_expander import expand_query, load_normalization_map
from backend.retrieval.rules_provider import load_global_rules
from backend.retrieval.value_matcher import build_value_alias_index, match_values

_COLREF = re.compile(r"([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)")
# Single-word không-dấu temporal cues (drop "nam" -> collides with "Việt Nam").
_TEMPORAL_TOKENS = {"thang", "quy", "tuan", "ngay"}
_TEMPORAL_PHRASES = ("hom nay", "gan day", "nam nay", "nam ngoai", "thang truoc", "thang nay")


class RetrievalService:
    def __init__(self, repo, embedder, index):
        self.repo = repo
        self.embedder = embedder
        self.index = index
        self.norm_map = load_normalization_map(repo)
        self.value_index = build_value_alias_index(repo)
        self.global_rules = load_global_rules(repo)
        self.table_defs = {e["body"].get("table", ""): e["body"]
                           for e in repo.list(type_="table")}
        self.metric_defs = {e["body"].get("metric", ""): e["body"]
                            for e in repo.list(type_="metric")}
        self.column_defs = {f"{e['body'].get('table','')}.{e['body'].get('column','')}": e["body"]
                            for e in repo.list(type_="column")}
        self.join_path_defs = {e["body"].get("name", ""): e["body"]
                               for e in repo.list(type_="join_path")}

    @classmethod
    def from_knowledge_service(cls, svc) -> "RetrievalService":
        return cls(svc.repo, svc.embedder, svc.index)

    # ---- schema helpers ----
    def _schema_col(self, table: str, column: str) -> dict | None:
        try:
            for c in schema_def.get_table(table)["columns"]:
                if c["name"] == column:
                    return c
        except KeyError:
            pass
        return None

    def _resolved_column(self, table: str, column: str) -> ResolvedColumn:
        sc = self._schema_col(table, column)
        data_type = sc["type"] if sc else ""
        meaning = ""
        cdef = self.column_defs.get(f"{table}.{column}")
        if cdef and cdef.get("meaning"):
            meaning = cdef["meaning"]
        elif sc:
            meaning = sc.get("desc", "")
        try:
            is_key = column == schema_def.primary_key(table)
        except KeyError:
            is_key = False
        return ResolvedColumn(table=table, column=column, data_type=data_type,
                              meaning=meaning, is_key=is_key)

    def _build_table(self, table: str, reason: list[str]) -> ResolvedTable:
        body = self.table_defs.get(table, {})
        try:
            cols = [self._resolved_column(table, c) for c in schema_def.columns_of(table)]
            pk = schema_def.primary_key(table)
        except KeyError:
            cols, pk = [], ""
        meaning = body.get("meaning", "")
        if not meaning:
            try:
                meaning = schema_def.get_table(table).get("description", "")
            except KeyError:
                meaning = ""
        return ResolvedTable(table=table, meaning=meaning, meaning_en=body.get("meaning_en", ""),
                             primary_key=pk, columns=cols, reason=", ".join(reason))

    # ---- main ----
    def retrieve(self, retrieval_query: str, pinned_tables: list[str] | None = None) -> ResolvedContext:
        pinned_tables = pinned_tables or []
        exp = expand_query(retrieval_query, self.norm_map)
        buckets = vector_retriever.retrieve_buckets(self.embedder, self.index, exp.text)
        matched_values = match_values(retrieval_query, self.value_index)

        final_tables, reasons = table_resolver.resolve_tables(
            buckets, matched_values, pinned_tables, max_tables=config.RETRIEVAL_MAX_TABLES)

        curated = [self.join_path_defs[h.metadata.get("name", "")]
                   for h in buckets.get("join_path", [])
                   if h.metadata.get("name", "") in self.join_path_defs]
        joins, used_tables, unreachable = join_expander.expand_joins(final_tables, curated)

        # Metrics: map each retrieved hit to its full body.
        metrics: list[ResolvedMetric] = []
        for h in buckets.get("metric", []):
            name = h.metadata.get("metric", "")
            b = self.metric_defs.get(name, {})
            metrics.append(ResolvedMetric(
                metric=name, formula=b.get("formula", ""), aliases=list(b.get("aliases", [])),
                required_tables=list(b.get("required_tables", [])),
                required_joins=list(b.get("required_joins", [])),
                use_when=b.get("use_when", ""), notes=b.get("notes", ""), score=round(h.score, 4)))

        tables = [self._build_table(t, reasons.get(t, [])) for t in used_tables]
        columns = self._focus_columns(used_tables, buckets.get("column", []),
                                       metrics, joins, matched_values)

        # Temporal-cue guard: surface the order date so Phase 7 anchors to MAX(ngay_dat_hang).
        temporal = self._has_temporal_cue(exp.normalized)
        if temporal and "don_hang_ban" in used_tables:
            self._ensure_column(columns, "don_hang_ban", "ngay_dat_hang")

        return ResolvedContext(
            dialect=config.SQL_DIALECT,
            retrieval_query=retrieval_query,
            pinned_tables=pinned_tables,
            final_tables=used_tables,
            tables=tables,
            columns=columns,
            metrics=metrics,
            joins=joins,
            matched_values=matched_values,
            rules=self.global_rules,
            debug={
                "expanded_query": exp.text,
                "identifier_hints": exp.identifier_hints,
                "table_reasons": {t: reasons.get(t, []) for t in final_tables},
                "unreachable_tables": unreachable,
                "temporal_cue": temporal,
                "bucket_hits": {t: [{"id": h.doc_id, "score": round(h.score, 4)} for h in hs]
                                for t, hs in buckets.items()},
            },
        )

    # ---- focus-column assembly ----
    def _focus_columns(self, used_tables, column_hits, metrics, joins, matched_values):
        final = set(used_tables)
        out: list[ResolvedColumn] = []
        seen: set[tuple[str, str]] = set()

        def add(table, column):
            if table in final and column and (table, column) not in seen:
                if self._schema_col(table, column) is not None:
                    seen.add((table, column))
                    out.append(self._resolved_column(table, column))

        for h in column_hits:
            add(h.metadata.get("table", ""), h.metadata.get("column", ""))
        for j in joins:
            add(j.left_table, j.left_column)
            add(j.right_table, j.right_column)
        for mv in matched_values:
            add(mv.table, mv.column)
            if mv.id_column:
                add(mv.table, mv.id_column)
        for m in metrics:
            for t, c in _COLREF.findall(m.formula or ""):
                add(t, c)
            for cond in m.required_joins:
                for t, c in _COLREF.findall(cond):
                    add(t, c)
        return out

    def _ensure_column(self, columns, table, column):
        if any(c.table == table and c.column == column for c in columns):
            return
        if self._schema_col(table, column) is not None:
            columns.append(self._resolved_column(table, column))

    @staticmethod
    def _has_temporal_cue(normalized: str) -> bool:
        if set(normalized.split()) & _TEMPORAL_TOKENS:
            return True
        return any(p in normalized for p in _TEMPORAL_PHRASES)
