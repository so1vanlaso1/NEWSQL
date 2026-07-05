"""Phase 13/14: the review controller end-to-end, LLM disabled (fallback path).

Drives ``controller.run_review`` with ``client=None`` so the deterministic fallback pack is
used, then asserts the full stage sequence: tasks run, evidence is profiled and streamed,
charts are built, and the review persists and is retrievable. This is the offline
"analytic answers ship even with a garbage/absent LLM" guarantee.
"""
from backend.analysis import controller
from backend.analysis.models import ReviewSeed, TargetEntity
from backend.analysis.review_store import ReviewStore
from backend.knowledge import analysis_meta
from backend.memory.store import ConversationStore
from backend.retrieval.context_builder import RetrievalService


def _seed_kb(kb):
    # Stage like the production seeder (seed.seed_analysis) so cross-entry save-validation
    # ordering isn't an issue, then embed so retrieval can surface the playbook.
    kb.stage("metric", {
        "metric": "doanh_thu", "formula": "SUM(chi_tiet_don_hang_ban.thanh_tien)",
        "required_tables": ["chi_tiet_don_hang_ban"], "aliases": ["doanh thu", "revenue"],
        "direction": "higher_is_better", "decomposition": ["so_don_hang"],
        "interpretation_down": "giảm do mất khách"})
    for e in analysis_meta.build_analysis_entries("2024-01-01", "2025-06-24"):
        kb.stage(e["type"], e["body"])
    kb.embed_pending()


def _run(kb, tmp_path, message, mode="ANALYTIC_MODE", seed=None):
    _seed_kb(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    store = ConversationStore(path=tmp_path / "conversations.db")
    review_store = ReviewStore(path=tmp_path / "conversations.db")
    cid = store.create()
    events = list(controller.run_review(
        message=message, conversation_id=cid, turns=[], rsvc=rsvc, mode=mode,
        seed=seed, store=store, review_store=review_store, client=None))
    return events, store, review_store, cid


def _final(events):
    finals = [e for e in events if e.get("type") == "final"]
    assert finals, "controller must emit a final event"
    return finals[0]["response"]


def test_fresh_review_runs_and_persists(kb, tmp_path):
    events, store, review_store, cid = _run(
        kb, tmp_path, "Phân tích vì sao doanh thu giảm tháng 5 2025?")

    assert not any(e.get("type") == "downgrade" for e in events)
    resp = _final(events)
    assert resp["mode"] == "ANALYTIC_MODE"
    assert resp["review_id"].startswith("rv_")
    assert len(resp["evidence"]) >= 2
    assert len(resp["charts"]) >= 1
    assert resp["analytic_status"] in ("complete", "degraded")
    assert resp["report_markdown"].startswith("## ")

    # evidence events stream one-per-task
    ev_events = [e for e in events if e.get("type") == "evidence"]
    assert len(ev_events) == len(resp["evidence"])

    # persisted + retrievable
    got = review_store.get_review(resp["review_id"])
    assert got is not None and len(got.evidence) == len(resp["evidence"])

    # a turn was saved and linked to the review
    turns = store.load_all(cid)
    assert turns and turns[-1].review_id == resp["review_id"]
    assert turns[-1].intent == "ANALYTIC_MODE"


def test_sse_step_sequence(kb, tmp_path):
    events, *_ = _run(kb, tmp_path, "Phân tích doanh thu tháng 5 2025")
    steps = [e["step"] for e in events if e.get("type") == "step"]
    for expected in ("retrieve", "plan", "task", "profile", "charts", "write", "save"):
        assert expected in steps, f"missing SSE step {expected}"


def test_previous_result_seed_scopes_every_task(kb, tmp_path):
    seed = ReviewSeed(
        ok=True, source_question="Top 10 khách hàng", base_metrics=["doanh_thu"],
        base_filters=["2025-05"],
        target_entity=TargetEntity(type="khach_hang", id_column="khach_hang_id",
                                   id_value="KH_030", name_column="ten_khach_hang",
                                   name_value="Cua hang 30"))
    events, _, review_store, _ = _run(
        kb, tmp_path, "Phân tích sâu khách hàng top 1",
        mode="ANALYTIC_FROM_PREVIOUS_RESULT", seed=seed)
    resp = _final(events)
    assert resp["evidence"], "seeded review should still produce evidence"
    for ev in resp["evidence"]:
        if ev["sql"]:
            assert "khach_hang_id = 'KH_030'" in ev["sql"]


def test_review_persist_failure_leaves_no_dangling_turn(kb, tmp_path):
    """If the review fails to persist, the turn must not keep a review_id pointing at a
    review that does not exist (no 404-on-reopen), and the stream must still finish."""
    _seed_kb(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    store = ConversationStore(path=tmp_path / "conversations.db")
    cid = store.create()

    class _BoomReviewStore:
        def save_review(self, record):
            raise RuntimeError("disk full")

    events = list(controller.run_review(
        message="Phân tích doanh thu tháng 5 2025", conversation_id=cid, turns=[],
        rsvc=rsvc, mode="ANALYTIC_MODE", seed=None, store=store,
        review_store=_BoomReviewStore(), client=None))

    # the stream still completes with a final response (never dropped)
    resp = _final(events)
    assert resp["review_id"].startswith("rv_")   # live response still renders from inline data
    # but the persisted turn must NOT reference the unsaved review
    turns = store.load_all(cid)
    assert turns and turns[-1].review_id == ""
    assert turns[-1].intent == "ANALYTIC_MODE"
