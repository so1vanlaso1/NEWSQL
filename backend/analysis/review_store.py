"""Persist reviews + evidence in conversations.db (plan §20.1).

A review is 1 turn -> N tasks -> N evidence items -> 1 report; flattening that into ``Turn``
columns would be lossy, so it lives in its own ``reviews`` + ``evidence`` tables (same
conversations.db, so a session and its analyses stay together). ``source_type`` is a hard
column on evidence — database facts vs web claims are distinguished structurally. The
``research_cache`` table is created here (Phase 14) and used by web research in Phase 17.

Follows the store conventions: a new connection per op, JSON text for list/dict fields.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.analysis.models import (
    ChartSpec,
    EvidenceItem,
    ReviewPlan,
    ReviewRecord,
    ReviewSeed,
)
from backend.memory import db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id        TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    turn_id          TEXT NOT NULL DEFAULT '',
    mode             TEXT NOT NULL DEFAULT '',
    question         TEXT NOT NULL DEFAULT '',
    review_seed_json TEXT,
    plan_json        TEXT NOT NULL DEFAULT '{}',
    findings_summary TEXT NOT NULL DEFAULT '',
    report_markdown  TEXT NOT NULL DEFAULT '',
    sources_json     TEXT NOT NULL DEFAULT '[]',
    caveats_json     TEXT NOT NULL DEFAULT '[]',
    follow_up_json   TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'complete',
    created_at       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id  TEXT PRIMARY KEY,
    review_id    TEXT NOT NULL,
    task_id      TEXT,
    kind         TEXT NOT NULL DEFAULT 'raw',
    source_type  TEXT NOT NULL DEFAULT 'sql',
    metric       TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    purpose      TEXT NOT NULL DEFAULT '',
    sql          TEXT,
    columns_json TEXT NOT NULL DEFAULT '[]',
    rows_json    TEXT NOT NULL DEFAULT '[]',
    profile_json TEXT NOT NULL DEFAULT '{}',
    web_json     TEXT,
    chart_json   TEXT,
    ordinal      INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'success',
    created_at   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_reviews_conv ON reviews(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_evidence_review ON evidence(review_id, ordinal);
CREATE TABLE IF NOT EXISTS research_cache (
    query_norm   TEXT PRIMARY KEY,
    results_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS places_cache (
    query_norm   TEXT PRIMARY KEY,
    results_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (json.JSONDecodeError, TypeError):
        return default


class ReviewStore:
    def __init__(self, path: Path | None = None):
        self.path = path
        con = db.get_connection(self.path)
        try:
            con.executescript(_SCHEMA)
            con.commit()
        finally:
            con.close()

    # ---- writes ----
    def save_review(self, record: ReviewRecord) -> ReviewRecord:
        """Persist a review + its evidence (charts stored on their evidence rows)."""
        created = record.created_at or _now()
        charts_by_ev = {c.evidence_id: c for c in record.charts}
        con = db.get_connection(self.path)
        try:
            con.execute(
                "INSERT OR REPLACE INTO reviews (review_id, conversation_id, turn_id, mode, "
                "question, review_seed_json, plan_json, findings_summary, report_markdown, "
                "sources_json, caveats_json, follow_up_json, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.review_id, record.conversation_id, record.turn_id, record.mode,
                    record.question,
                    _dumps(record.review_seed.model_dump()) if record.review_seed else None,
                    _dumps(record.plan.model_dump()) if record.plan else "{}",
                    record.findings_summary, record.report_markdown,
                    _dumps(record.sources), _dumps(record.caveats),
                    _dumps(record.follow_up_suggestions), record.status, created,
                ))
            con.execute("DELETE FROM evidence WHERE review_id = ?", (record.review_id,))
            for i, ev in enumerate(record.evidence):
                chart = charts_by_ev.get(ev.evidence_id)
                con.execute(
                    "INSERT INTO evidence (evidence_id, review_id, task_id, kind, source_type, "
                    "metric, title, purpose, sql, columns_json, rows_json, profile_json, web_json, "
                    "chart_json, ordinal, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ev.evidence_id, record.review_id, ev.task_id, ev.kind, ev.source_type,
                        ev.metric, ev.title, ev.purpose, ev.sql, _dumps(ev.columns), _dumps(ev.rows),
                        _dumps(ev.profile), _dumps(ev.web) if ev.web is not None else None,
                        _dumps(chart.model_dump()) if chart else None,
                        i, ev.status, ev.created_at or created,
                    ))
            con.commit()
        finally:
            con.close()
        return record

    # ---- reads ----
    def get_review(self, review_id: str) -> Optional[ReviewRecord]:
        con = db.get_connection(self.path)
        try:
            row = con.execute("SELECT * FROM reviews WHERE review_id = ?", (review_id,)).fetchone()
            if row is None:
                return None
            ev_rows = con.execute(
                "SELECT * FROM evidence WHERE review_id = ? ORDER BY ordinal ASC",
                (review_id,)).fetchall()
        finally:
            con.close()
        return self._row_to_record(row, ev_rows)

    def list_reviews(self, conversation_id: str, limit: int = 100) -> list[dict]:
        """Compact review summaries for a conversation (newest first)."""
        con = db.get_connection(self.path)
        try:
            rows = con.execute(
                "SELECT review_id, turn_id, mode, question, findings_summary, status, created_at "
                "FROM reviews WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?",
                (conversation_id, limit)).fetchall()
        finally:
            con.close()
        return [dict(r) for r in rows]

    def last_review(self, conversation_id: str) -> Optional[ReviewRecord]:
        con = db.get_connection(self.path)
        try:
            row = con.execute(
                "SELECT review_id FROM reviews WHERE conversation_id = ? "
                "ORDER BY created_at DESC LIMIT 1", (conversation_id,)).fetchone()
        finally:
            con.close()
        return self.get_review(row["review_id"]) if row else None

    # ---- research cache (Phase 17): normalized-query key, TTL enforced by the caller ----
    def get_research_cache(self, query_norm: str) -> Optional[dict]:
        con = db.get_connection(self.path)
        try:
            row = con.execute(
                "SELECT results_json, created_at FROM research_cache WHERE query_norm = ?",
                (query_norm,)).fetchone()
        finally:
            con.close()
        return dict(row) if row is not None else None

    def put_research_cache(self, query_norm: str, results_json: str, created_at: str) -> None:
        con = db.get_connection(self.path)
        try:
            con.execute(
                "INSERT OR REPLACE INTO research_cache (query_norm, results_json, created_at) "
                "VALUES (?,?,?)",
                (query_norm, results_json, created_at))
            con.commit()
        finally:
            con.close()

    # ---- places cache (Phase 19): same shape as research_cache ----
    def get_places_cache(self, query_norm: str) -> Optional[dict]:
        con = db.get_connection(self.path)
        try:
            row = con.execute(
                "SELECT results_json, created_at FROM places_cache WHERE query_norm = ?",
                (query_norm,)).fetchone()
        finally:
            con.close()
        return dict(row) if row is not None else None

    def put_places_cache(self, query_norm: str, results_json: str, created_at: str) -> None:
        con = db.get_connection(self.path)
        try:
            con.execute(
                "INSERT OR REPLACE INTO places_cache (query_norm, results_json, created_at) "
                "VALUES (?,?,?)",
                (query_norm, results_json, created_at))
            con.commit()
        finally:
            con.close()

    def _row_to_record(self, row, ev_rows) -> ReviewRecord:
        d = dict(row)
        evidence: list[EvidenceItem] = []
        charts: list[ChartSpec] = []
        for er in ev_rows:
            e = dict(er)
            chart_data = _loads(e.get("chart_json"), None)
            item = EvidenceItem(
                evidence_id=e["evidence_id"], review_id=e["review_id"],
                task_id=e.get("task_id") or "", kind=e.get("kind", "raw"),
                source_type=e.get("source_type", "sql"), metric=e.get("metric", "") or "",
                title=e.get("title", ""),
                purpose=e.get("purpose", ""), sql=e.get("sql") or "",
                columns=_loads(e.get("columns_json"), []), rows=_loads(e.get("rows_json"), []),
                profile=_loads(e.get("profile_json"), {}),
                web=_loads(e.get("web_json"), None),
                chart_id=(chart_data or {}).get("chart_id", "") if chart_data else "",
                status=e.get("status", "success"), created_at=e.get("created_at", ""))
            evidence.append(item)
            if chart_data:
                charts.append(ChartSpec(**chart_data))
        seed_data = _loads(d.get("review_seed_json"), None)
        plan_data = _loads(d.get("plan_json"), None)
        return ReviewRecord(
            review_id=d["review_id"], conversation_id=d.get("conversation_id", ""),
            turn_id=d.get("turn_id", ""), mode=d.get("mode", ""),
            question=d.get("question", ""),
            review_seed=ReviewSeed(**seed_data) if seed_data else None,
            plan=ReviewPlan(**plan_data) if plan_data else None,
            findings_summary=d.get("findings_summary", ""),
            report_markdown=d.get("report_markdown", ""),
            evidence=evidence, charts=charts,
            sources=_loads(d.get("sources_json"), []),
            follow_up_suggestions=_loads(d.get("follow_up_json"), []),
            caveats=_loads(d.get("caveats_json"), []),
            status=d.get("status", "complete"), created_at=d.get("created_at", ""))


# ---- shared singleton -------------------------------------------------------
_STORE: Optional[ReviewStore] = None
_STORE_LOCK = threading.Lock()


def get_review_store() -> ReviewStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:  # double-checked lock: one instance under threadpool concurrency
            if _STORE is None:
                _STORE = ReviewStore()
    return _STORE
