"""Phase 12: AnalyticContext assembly + the /api/analysis/plan tester (plan §11, §22.1)."""
from backend.analysis import context_builder
from backend.analysis.models import ReviewSeed, TargetEntity
from backend.retrieval.context_builder import RetrievalService


def _seed_analytic(kb):
    kb.save("metric", {
        "metric": "doanh_thu", "formula": "SUM(chi_tiet_don_hang_ban.thanh_tien)",
        "required_tables": ["chi_tiet_don_hang_ban"], "aliases": ["doanh thu", "revenue"],
        "direction": "higher_is_better", "decomposition": ["so_don_hang"],
        "interpretation_down": "giảm do mất khách"})
    kb.save("playbook", {"playbook": "revenue_drop", "use_when": "vì sao doanh thu giảm",
                         "diagnostic_steps": [{"title": "so sánh kỳ"}]})
    kb.save("dimension", {"dimension": "category", "table": "danh_muc_san_pham",
                          "column": "ten_danh_muc", "aliases": ["ngành hàng"]})
    kb.save("caveat", {"title": "Phạm vi dữ liệu", "content": "chỉ đến 2025-06-24"})
    kb.save("chart_rule", {"shape": "trend", "chart_type": "line"})


def test_build_analytic_context_populated(kb):
    _seed_analytic(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    ctx = context_builder.build_analytic_context(
        rsvc, "phân tích vì sao doanh thu giảm theo ngành hàng",
        mode="ANALYTIC_MODE", recent_turns=[])

    assert ctx.schema_context is not None
    assert any(p.get("playbook") == "revenue_drop" for p in ctx.playbooks)
    assert any(d.get("dimension") == "category" for d in ctx.dimensions)
    assert ctx.caveats, "caveats should be retrieved"
    assert any(c.get("shape") == "trend" for c in ctx.chart_rules)  # policy, loaded fresh
    assert any(m.get("metric") == "doanh_thu" and m.get("direction") for m in ctx.metric_analysis)
    assert ctx.data_window.get("min") and ctx.data_window.get("max")
    assert ctx.mode == "ANALYTIC_MODE"


def test_chart_rules_are_live_on_edit(kb):
    _seed_analytic(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    ctx1 = context_builder.build_analytic_context(rsvc, "phân tích doanh thu", mode="ANALYTIC_MODE")
    assert any(c.get("chart_type") == "line" for c in ctx1.chart_rules)
    # Edit the chart_rule -> next context reflects it with no restart (hot-reload).
    kb.save("chart_rule", {"shape": "trend", "chart_type": "none"})
    ctx2 = context_builder.build_analytic_context(rsvc, "phân tích doanh thu", mode="ANALYTIC_MODE")
    trend = [c for c in ctx2.chart_rules if c.get("shape") == "trend"][0]
    assert trend["chart_type"] == "none"


def test_build_retrieval_query_with_and_without_seed():
    assert context_builder.build_retrieval_query("phân tích cái này", None) == "phân tích cái này"
    seed = ReviewSeed(ok=True, source_question="Top 10 khách hàng",
                      target_entity=TargetEntity(name_value="Cua hang 30"))
    q = context_builder.build_retrieval_query("phân tích sâu top 1", seed)
    assert "Top 10 khách hàng" in q and "Cua hang 30" in q


# ---- endpoint ---------------------------------------------------------------
def test_analysis_plan_endpoint_analytic_mode(kb, monkeypatch):
    from backend.api import analysis, state
    _seed_analytic(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    monkeypatch.setattr(state, "_retrieval", rsvc, raising=False)

    resp = analysis.analysis_plan(
        analysis.AnalysisPlanRequest(message="Vì sao doanh thu giảm?"), rsvc)
    assert resp.mode == "ANALYTIC_MODE"
    assert resp.analytic_context is not None
    assert resp.review_seed is None


def test_analysis_plan_endpoint_normal_mode(kb, monkeypatch):
    from backend.api import analysis, state
    _seed_analytic(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    monkeypatch.setattr(state, "_retrieval", rsvc, raising=False)

    resp = analysis.analysis_plan(
        analysis.AnalysisPlanRequest(message="Top 10 khách hàng theo doanh thu"), rsvc)
    assert resp.mode == "NORMAL_SQL"
    assert resp.analytic_context is None
    assert resp.note
