from backend.analysis import advisor
from backend.analysis.models import AnalyticContext, EvidenceItem, PlannedTask, ReviewPlan


def test_advisor_uses_profiles_and_kb_rules():
    ctx = AnalyticContext(
        playbooks=[{
            "playbook": "revenue_drop",
            "interpretation_rules": [
                "Nếu top-3 concentration > 50% thì tập trung điều tra nhóm đó trước.",
            ],
            "improvement_rules": [
                "Một ngành hàng giảm mạnh: kiểm tra tồn kho, giá bán và khuyến mãi.",
            ],
        }],
        metric_analysis=[{
            "metric": "doanh_thu",
            "interpretation_down": "doanh thu giảm thường do mất khách hoặc giảm giá trị đơn.",
        }],
        dimensions=[{"dimension": "category", "aliases": ["ngành hàng"], "drill_down_to": ["product"]},
                    {"dimension": "product", "aliases": ["sản phẩm"], "drill_down_to": []}],
    )
    plan = ReviewPlan(tasks=[PlannedTask(metric="doanh_thu", dimension="category")])
    evidence = [
        EvidenceItem(
            evidence_id="ev1", title="Doanh thu kỳ này so với kỳ trước",
            metric="doanh_thu", status="success",
            profile={"shape": "kpi", "trend": "down", "absolute_change": -120, "pct_change": -12.5},
            rows=[{"ky": "ky_nay", "gia_tri": 880}, {"ky": "ky_truoc", "gia_tri": 1000}],
        ),
        EvidenceItem(
            evidence_id="ev2", title="Doanh thu theo ngành hàng",
            metric="doanh_thu", status="success",
            profile={
                "shape": "by_dimension",
                "biggest_mover": {"label": "Sữa", "change": -90},
                "top3_concentration": 0.72,
            },
            rows=[{"nhom": "Sữa", "ky_nay": 100, "ky_truoc": 190}],
        ),
    ]

    out = advisor.build_advice(ctx, plan, evidence)

    assert "KPI" in out.driver_summary
    assert any("Sữa" in b for b in out.interpretation_bullets)
    assert any("top-3" in b or "tập trung" in b for b in out.interpretation_bullets)
    assert any("kiểm tra" in b.lower() for b in out.improvement_bullets)
    assert any("sản phẩm" in q for q in out.next_questions)

