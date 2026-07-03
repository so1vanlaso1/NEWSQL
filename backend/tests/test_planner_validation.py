"""Phase 13: planner validation ladder (plan §13.3).

A fake LLM feeds canned planner responses so we can assert the ladder deterministically:
parse -> structure -> per-task 6-layer validation -> dedupe -> retry -> fallback pack.
"""
import json

from backend.analysis import date_window, planner
from backend.analysis.models import AnalyticContext
from backend.knowledge import analysis_meta
from backend.llm.client import LlmResult

_WINDOW = date_window.resolve_window("tháng 5 2025", None, "2024-01-01", "2025-06-24")

_KPI = ("SELECT 'ky_nay' AS ky, SUM(ct.thanh_tien) AS gia_tri "
        "FROM don_hang_ban dh JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id "
        "WHERE dh.trang_thai='NORMAL' AND dh.ngay_dat_hang BETWEEN '2025-05-01' AND '2025-05-31' "
        "UNION ALL SELECT 'ky_truoc' AS ky, SUM(ct.thanh_tien) AS gia_tri "
        "FROM don_hang_ban dh JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id "
        "WHERE dh.trang_thai='NORMAL' AND dh.ngay_dat_hang BETWEEN '2025-04-01' AND '2025-04-30'")
_TOP = ("SELECT kh.ten_khach_hang AS ten, SUM(ct.thanh_tien) AS gia_tri "
        "FROM don_hang_ban dh JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id "
        "JOIN khach_hang kh ON dh.khach_hang_id = kh.khach_hang_id "
        "WHERE dh.trang_thai='NORMAL' AND dh.ngay_dat_hang BETWEEN '2025-05-01' AND '2025-05-31' "
        "GROUP BY kh.ten_khach_hang ORDER BY gia_tri DESC LIMIT 10")
_BAD = "SELECT * FROM khong_ton_tai WHERE x = 1"


def _plan_json(tasks, downgrade=None):
    return json.dumps({
        "analysis_title": "Phân tích thử",
        "mode_downgrade": downgrade,
        "playbook_used": "playbook:revenue_drop",
        "date_range": {"from": "2025-05-01", "to": "2025-05-31",
                       "compare_from": "2025-04-01", "compare_to": "2025-04-30"},
        "tasks": tasks,
    }, ensure_ascii=False)


class FakeClient:
    """Returns queued responses (str content, or {'error': msg}) in order."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def resolve_model(self):
        return "fake-model"

    def chat(self, system, user, **kw):
        r = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(r, dict) and "error" in r:
            return LlmResult(error=r["error"], model="fake-model")
        return LlmResult(content=r, model="fake-model")


def _ctx():
    pb = next(p for p in analysis_meta.PLAYBOOKS if p["playbook"] == "revenue_drop")
    return AnalyticContext(question="Vì sao doanh thu giảm?", playbooks=[pb],
                           dimensions=analysis_meta.DIMENSIONS,
                           data_window={"min": "2024-01-01", "max": "2025-06-24"})


def test_valid_plan_is_accepted():
    tasks = [
        {"task_id": "t1", "title": "KPI", "purpose": "", "expected_shape": "kpi", "sql": _KPI},
        {"task_id": "t2", "title": "Top", "purpose": "", "expected_shape": "top_n", "sql": _TOP},
    ]
    plan = planner.plan_review(_ctx(), _WINDOW, FakeClient([_plan_json(tasks)]))
    assert plan.source == "llm"
    assert [t.task_id for t in plan.tasks] == ["t1", "t2"]
    assert plan.date_window.date_from == "2025-05-01"


def test_invalid_task_is_dropped_valid_ones_kept():
    tasks = [
        {"task_id": "t1", "title": "KPI", "expected_shape": "kpi", "sql": _KPI},
        {"task_id": "t2", "title": "Top", "expected_shape": "top_n", "sql": _TOP},
        {"task_id": "t3", "title": "Bad", "expected_shape": "kpi", "sql": _BAD},
    ]
    plan = planner.plan_review(_ctx(), _WINDOW, FakeClient([_plan_json(tasks)]))
    assert {t.task_id for t in plan.tasks} == {"t1", "t2"}
    assert any("Bad" in d for d in plan.dropped)


def test_duplicate_sql_is_deduped():
    tasks = [
        {"task_id": "t1", "title": "KPI", "expected_shape": "kpi", "sql": _KPI},
        {"task_id": "t2", "title": "KPI dup", "expected_shape": "kpi", "sql": _KPI},
        {"task_id": "t3", "title": "Top", "expected_shape": "top_n", "sql": _TOP},
    ]
    plan = planner.plan_review(_ctx(), _WINDOW, FakeClient([_plan_json(tasks)]))
    assert len(plan.tasks) == 2
    assert any("trùng" in d for d in plan.dropped)


def test_mode_downgrade_is_returned():
    plan = planner.plan_review(_ctx(), _WINDOW, FakeClient([_plan_json([], downgrade="NORMAL_SQL")]))
    assert plan.is_downgrade


def test_malformed_json_retries_then_falls_back():
    # First and retry both garbage -> deterministic fallback pack.
    client = FakeClient(["not json at all", "still not json"])
    plan = planner.plan_review(_ctx(), _WINDOW, client)
    assert plan.source == "fallback"
    assert len(plan.tasks) >= 2
    assert client.calls == 2  # one planner call + one retry


def test_one_valid_task_triggers_retry_then_repair_succeeds():
    one = [{"task_id": "t1", "title": "KPI", "expected_shape": "kpi", "sql": _KPI}]
    two = [
        {"task_id": "t1", "title": "KPI", "expected_shape": "kpi", "sql": _KPI},
        {"task_id": "t2", "title": "Top", "expected_shape": "top_n", "sql": _TOP},
    ]
    client = FakeClient([_plan_json(one), _plan_json(two)])
    plan = planner.plan_review(_ctx(), _WINDOW, client)
    assert plan.source == "llm_repair"
    assert len(plan.tasks) == 2


def test_llm_error_falls_back_immediately():
    plan = planner.plan_review(_ctx(), _WINDOW, FakeClient([{"error": "HTTP 500"}]))
    assert plan.source == "fallback"
    assert len(plan.tasks) >= 2


def test_client_none_uses_fallback():
    plan = planner.plan_review(_ctx(), _WINDOW, None)
    assert plan.source == "fallback"
    assert len(plan.tasks) >= 2
