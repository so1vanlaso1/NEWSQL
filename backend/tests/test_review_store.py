"""Phase 14: review persistence round-trip (plan §20.1) + Turn.review_id link."""
from backend.analysis.models import (
    ChartSeries,
    ChartSpec,
    DateWindow,
    EvidenceItem,
    PlannedTask,
    ReviewPlan,
    ReviewRecord,
    ReviewSeed,
    TargetEntity,
)
from backend.analysis.review_store import ReviewStore
from backend.memory.store import ConversationStore


def _record(review_id="rv_test1", conversation_id="c1") -> ReviewRecord:
    ev1 = EvidenceItem(
        evidence_id=f"{review_id}_ev1", review_id=review_id, task_id="t1",
        kind="kpi_comparison", source_type="sql", title="Doanh thu",
        sql="SELECT 1", columns=["ky", "gia_tri"],
        rows=[{"ky": "ky_nay", "gia_tri": 820}, {"ky": "ky_truoc", "gia_tri": 1040}],
        profile={"current": 820, "previous": 1040, "pct_change": -21.15}, status="success",
        chart_id="c1")
    ev2 = EvidenceItem(
        evidence_id=f"{review_id}_ev2", review_id=review_id, task_id="t2",
        kind="top_n", source_type="sql", title="Top KH", sql="SELECT 2",
        columns=["ten", "gia_tri"], rows=[{"ten": "X", "gia_tri": 5}], status="success")
    chart = ChartSpec(chart_id="c1", type="grouped_bar", title="Doanh thu", x_field="ky",
                      series=[ChartSeries(name="gia_tri", value_field="gia_tri")],
                      data=[{"ky": "ky_nay", "gia_tri": 820}], unit="VND",
                      evidence_id=ev1.evidence_id)
    plan = ReviewPlan(analysis_title="Phân tích", source="fallback",
                      date_window=DateWindow(date_from="2025-05-01", date_to="2025-05-31"),
                      tasks=[PlannedTask(task_id="t1", title="KPI", sql="SELECT 1")])
    return ReviewRecord(
        review_id=review_id, conversation_id=conversation_id, turn_id="turn1",
        mode="ANALYTIC_MODE", question="Vì sao doanh thu giảm?",
        review_seed=ReviewSeed(ok=True, target_entity=TargetEntity(id_value="KH_1")),
        plan=plan, findings_summary="Doanh thu giảm 21%.",
        report_markdown="## Phân tích\n...", evidence=[ev1, ev2], charts=[chart],
        sources=[], follow_up_suggestions=["Phân tích theo vùng"],
        caveats=["Chỉ tính đơn NORMAL."], status="complete")


def test_save_and_get_review_round_trip(tmp_path):
    store = ReviewStore(path=tmp_path / "conversations.db")
    store.save_review(_record())
    got = store.get_review("rv_test1")
    assert got is not None
    assert got.question == "Vì sao doanh thu giảm?"
    assert got.mode == "ANALYTIC_MODE"
    assert len(got.evidence) == 2
    assert got.evidence[0].profile["pct_change"] == -21.15
    assert got.evidence[0].source_type == "sql"
    assert len(got.charts) == 1
    assert got.charts[0].chart_id == "c1"
    assert got.evidence[0].chart_id == "c1"
    assert got.caveats == ["Chỉ tính đơn NORMAL."]
    assert got.follow_up_suggestions == ["Phân tích theo vùng"]
    assert got.plan.tasks[0].task_id == "t1"
    assert got.review_seed.target_entity.id_value == "KH_1"
    assert got.status == "complete"


def test_list_and_last_review(tmp_path):
    store = ReviewStore(path=tmp_path / "conversations.db")
    store.save_review(_record(review_id="rv_a", conversation_id="c9"))
    store.save_review(_record(review_id="rv_b", conversation_id="c9"))
    listed = store.list_reviews("c9")
    assert {r["review_id"] for r in listed} == {"rv_a", "rv_b"}
    last = store.last_review("c9")
    assert last is not None and last.review_id in {"rv_a", "rv_b"}


def test_missing_review_returns_none(tmp_path):
    store = ReviewStore(path=tmp_path / "conversations.db")
    assert store.get_review("nope") is None


def test_turn_review_id_round_trips(tmp_path):
    cs = ConversationStore(path=tmp_path / "conversations.db")
    cid = cs.create()
    saved = cs.save_non_sql_turn(cid, "phân tích", intent="ANALYTIC_MODE",
                                 answer="ok", review_id="rv_xyz")
    loaded = cs.get(saved.turn_id)
    assert loaded.review_id == "rv_xyz"
    assert loaded.intent == "ANALYTIC_MODE"
