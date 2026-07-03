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
import threading

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
        # ensure_fresh() swaps self._caches as ONE reference; retrieve() snapshots it once
        # per call, so a concurrent rebuild can't hand a turn a mix of two kb_versions.
        # (FastAPI runs sync endpoints in a threadpool -> genuine reader/writer concurrency.)
        self._fresh_lock = threading.Lock()
        self._kb_version = -1
        self._caches = self._build_caches()
        try:
            self._kb_version = self.repo.get_kb_version()
        except Exception:  # pre-Phase-10 repo without meta table
            self._kb_version = 0

    def _build_caches(self) -> dict:
        """Build every derived cache from the repository as ONE immutable bundle.

        Assigned to self._caches as a single reference so readers always see a
        self-consistent snapshot (never one dict from the old version + another from the
        new). Called at construction and by ensure_fresh() when kb_version changes.
        """
        repo = self.repo
        return {
            "norm_map": load_normalization_map(repo),
            "value_index": build_value_alias_index(repo),
            "global_rules": load_global_rules(repo),
            "table_defs": {e["body"].get("table", ""): e["body"]
                           for e in repo.list(type_="table")},
            "metric_defs": {e["body"].get("metric", ""): e["body"]
                            for e in repo.list(type_="metric")},
            "column_defs": {f"{e['body'].get('table','')}.{e['body'].get('column','')}": e["body"]
                            for e in repo.list(type_="column")},
            "join_path_defs": {e["body"].get("name", ""): e["body"]
                               for e in repo.list(type_="join_path")},
        }

    # Backward-compatible read accessors. Each is a single atomic read of the current
    # bundle; a caller needing cross-field consistency within one turn snapshots
    # self._caches once (retrieve() does this).
    @property
    def norm_map(self) -> dict:
        return self._caches["norm_map"]

    @property
    def value_index(self):
        return self._caches["value_index"]

    @property
    def global_rules(self):
        return self._caches["global_rules"]

    @property
    def table_defs(self) -> dict:
        return self._caches["table_defs"]

    @property
    def metric_defs(self) -> dict:
        return self._caches["metric_defs"]

    @property
    def column_defs(self) -> dict:
        return self._caches["column_defs"]

    @property
    def join_path_defs(self) -> dict:
        return self._caches["join_path_defs"]

    def ensure_fresh(self) -> bool:
        """Rebuild derived caches if the KB was edited since we last looked (plan §12.2).

        One cheap SQLite read of meta.kb_version per call; a full rebuild only on change.
        The numpy index is shared in-memory with KnowledgeService, so vectors are never
        stale — only these derived dicts are. Returns True if a rebuild happened.
        """
        try:
            current = self.repo.get_kb_version()
        except Exception:  # noqa: BLE001 - never let a freshness check break a turn
            return False
        if current == self._kb_version:
            return False
        with self._fresh_lock:
            if current == self._kb_version:  # another thread rebuilt while we waited
                return False
            self._caches = self._build_caches()  # atomic single-reference swap
            self._kb_version = current
        return True

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

    def _resolved_column(self, table: str, column: str, column_defs: dict) -> ResolvedColumn:
        sc = self._schema_col(table, column)
        data_type = sc["type"] if sc else ""
        meaning = ""
        cdef = column_defs.get(f"{table}.{column}")
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

    def _build_table(self, table: str, reason: list[str], caches: dict) -> ResolvedTable:
        body = caches["table_defs"].get(table, {})
        try:
            cols = [self._resolved_column(table, c, caches["column_defs"])
                    for c in schema_def.columns_of(table)]
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
        self.ensure_fresh()  # pick up any KB edits since the last turn (no restart)
        c = self._caches     # ONE consistent snapshot for the whole turn (see _build_caches)
        pinned_tables = pinned_tables or []
        exp = expand_query(retrieval_query, c["norm_map"])
        buckets = vector_retriever.retrieve_buckets(self.embedder, self.index, exp.text)
        matched_values = match_values(retrieval_query, c["value_index"])

        final_tables, reasons = table_resolver.resolve_tables(
            buckets, matched_values, pinned_tables, max_tables=config.RETRIEVAL_MAX_TABLES)

        curated = [c["join_path_defs"][h.metadata.get("name", "")]
                   for h in buckets.get("join_path", [])
                   if h.metadata.get("name", "") in c["join_path_defs"]]
        joins, used_tables, unreachable = join_expander.expand_joins(final_tables, curated)

        # Metrics: map each retrieved hit to its full body.
        metrics: list[ResolvedMetric] = []
        for h in buckets.get("metric", []):
            name = h.metadata.get("metric", "")
            b = c["metric_defs"].get(name, {})
            metrics.append(ResolvedMetric(
                metric=name, formula=b.get("formula", ""), aliases=list(b.get("aliases", [])),
                required_tables=list(b.get("required_tables", [])),
                required_joins=list(b.get("required_joins", [])),
                use_when=b.get("use_when", ""), notes=b.get("notes", ""), score=round(h.score, 4)))

        tables = [self._build_table(t, reasons.get(t, []), c) for t in used_tables]
        columns = self._focus_columns(used_tables, buckets.get("column", []),
                                       metrics, joins, matched_values, c["column_defs"])

        # Temporal-cue guard: surface the order date so Phase 7 anchors to MAX(ngay_dat_hang).
        temporal = self._has_temporal_cue(exp.normalized)
        if temporal and "don_hang_ban" in used_tables:
            self._ensure_column(columns, "don_hang_ban", "ngay_dat_hang", c["column_defs"])

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
            rules=c["global_rules"],
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
    def _focus_columns(self, used_tables, column_hits, metrics, joins, matched_values, column_defs):
        final = set(used_tables)
        out: list[ResolvedColumn] = []
        seen: set[tuple[str, str]] = set()

        def add(table, column):
            if table in final and column and (table, column) not in seen:
                if self._schema_col(table, column) is not None:
                    seen.add((table, column))
                    out.append(self._resolved_column(table, column, column_defs))

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

    def _ensure_column(self, columns, table, column, column_defs):
        if any(c.table == table and c.column == column for c in columns):
            return
        if self._schema_col(table, column) is not None:
            columns.append(self._resolved_column(table, column, column_defs))

    @staticmethod
    def _has_temporal_cue(normalized: str) -> bool:
        if set(normalized.split()) & _TEMPORAL_TOKENS:
            return True
        return any(p in normalized for p in _TEMPORAL_PHRASES)
