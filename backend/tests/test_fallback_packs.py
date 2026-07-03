"""Phase 13: deterministic fallback packs (plan §13.4).

With the LLM disabled, every seeded playbook must instantiate into a complete, validated,
executable task pack. SQL is validated by the real 6-layer validator and executed against
the bundled sales.db, so these are genuine end-to-end checks of the fallback path.
"""
from backend.analysis import date_window, fallback_packs
from backend.analysis.models import AnalyticContext, DateWindow, ReviewSeed, TargetEntity
from backend.execution.query_runner import run_query
from backend.knowledge import analysis_meta
from backend.validation.sql_validator import validate

_WINDOW = date_window.resolve_window(
    "phân tích doanh thu tháng 5 2025", None, "2024-01-01", "2025-06-24")


def _ctx(playbook: dict) -> AnalyticContext:
    return AnalyticContext(
        question="Phân tích", playbooks=[playbook],
        dimensions=analysis_meta.DIMENSIONS,
        data_window={"min": "2024-01-01", "max": "2025-06-24"})


def _assert_pack_runs(plan, min_tasks=2):
    assert len(plan.tasks) >= min_tasks, plan.dropped
    for t in plan.tasks:
        vr = validate(t.sql, resolved_tables=None)
        assert vr.ok, f"{t.title}: {vr.errors}"
        qr = run_query(t.sql)
        assert qr.error is None, f"{t.title}: {qr.error}\n{t.sql}"


def test_every_seeded_playbook_builds_a_valid_pack():
    for pb in analysis_meta.PLAYBOOKS:
        plan = fallback_packs.build_fallback_pack(_ctx(pb), _WINDOW, None)
        _assert_pack_runs(plan)
        assert plan.source == "fallback"
        assert plan.playbook_used == f"playbook:{pb['playbook']}"


def test_revenue_drop_pack_has_kpi_and_dimension_shapes():
    pb = next(p for p in analysis_meta.PLAYBOOKS if p["playbook"] == "revenue_drop")
    plan = fallback_packs.build_fallback_pack(_ctx(pb), _WINDOW, None)
    shapes = {t.expected_shape for t in plan.tasks}
    assert "kpi" in shapes
    assert "by_dimension" in shapes


def test_entity_filter_is_applied_for_previous_result_seed():
    pb = next(p for p in analysis_meta.PLAYBOOKS if p["playbook"] == "top_customer_analysis")
    seed = ReviewSeed(ok=True, target_entity=TargetEntity(
        type="khach_hang", id_column="khach_hang_id", id_value="KH_030",
        name_column="ten_khach_hang", name_value="Cua hang 30"))
    plan = fallback_packs.build_fallback_pack(_ctx(pb), _WINDOW, seed)
    _assert_pack_runs(plan)
    assert all("khach_hang_id = 'KH_030'" in t.sql for t in plan.tasks), \
        "every task must be scoped to the seed entity"


def test_no_playbook_falls_back_to_default_pack():
    ctx = AnalyticContext(question="Phân tích doanh thu", playbooks=[],
                          dimensions=analysis_meta.DIMENSIONS,
                          data_window={"min": "2024-01-01", "max": "2025-06-24"})
    plan = fallback_packs.build_fallback_pack(ctx, _WINDOW, None)
    _assert_pack_runs(plan)
    assert {t.expected_shape for t in plan.tasks} >= {"kpi", "trend"}


def test_broken_sql_hint_step_is_skipped_not_fatal():
    pb = {
        "playbook": "custom", "diagnostic_steps": [
            {"title": "hỏng", "expected_shape": "kpi",
             "sql_hint": "SELECT * FROM khong_ton_tai WHERE x = {date_from}"},
            {"title": "ok", "expected_shape": "kpi", "metric": "doanh_thu",
             "sql_hint": ("SELECT 'ky_nay' AS ky, SUM(ct.thanh_tien) AS gia_tri "
                          "FROM don_hang_ban dh JOIN chi_tiet_don_hang_ban ct "
                          "ON dh.don_hang_id = ct.don_hang_id WHERE dh.trang_thai='NORMAL' "
                          "AND dh.ngay_dat_hang BETWEEN '{date_from}' AND '{date_to}' {entity_filter}")},
        ],
    }
    plan = fallback_packs.build_fallback_pack(_ctx(pb), _WINDOW, None)
    # The broken step is dropped; the pack is topped up to >= 2 valid tasks.
    _assert_pack_runs(plan)
    assert any("hỏng" in d for d in plan.dropped)


def test_substitute_fills_dimension_column_and_leaves_no_braces():
    win = DateWindow(date_from="2025-05-01", date_to="2025-05-31",
                     compare_from="2025-04-01", compare_to="2025-04-30")
    out = fallback_packs._substitute(
        "SELECT {dimension_column} AS nhom FROM t "
        "WHERE d BETWEEN '{date_from}' AND '{date_to}' {entity_filter} "
        "GROUP BY {dimension_column}",
        win, "AND kh.khach_hang_id = 'KH_1'", "danh_muc_san_pham.ten_danh_muc")
    assert out.count("danh_muc_san_pham.ten_danh_muc") == 2
    assert "{" not in out and "}" not in out


def test_unknown_placeholder_step_is_dropped_not_executed():
    # A hint carrying an unknown {placeholder} (e.g. survived save-time validation) must be
    # dropped, never run with a literal '{...}' in it. The pack still tops up to >= 2 tasks.
    pb = {
        "playbook": "custom", "diagnostic_steps": [
            {"title": "unknown ph", "expected_shape": "kpi",
             "sql_hint": ("SELECT 'ky_nay' AS ky, SUM(ct.thanh_tien) AS gia_tri "
                          "FROM don_hang_ban dh JOIN chi_tiet_don_hang_ban ct "
                          "ON dh.don_hang_id = ct.don_hang_id WHERE dh.trang_thai='NORMAL' "
                          "AND dh.ngay_dat_hang BETWEEN '{date_from}' AND '{date_to}' "
                          "AND dh.khach_hang_id = '{unknown_ph}'")},
        ],
    }
    plan = fallback_packs.build_fallback_pack(_ctx(pb), _WINDOW, None)
    assert any("chưa thay thế" in d for d in plan.dropped)
    assert all("{" not in t.sql for t in plan.tasks)
    _assert_pack_runs(plan)
