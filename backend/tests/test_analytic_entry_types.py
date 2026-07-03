"""Phase 11: analytic knowledge entry types (playbook, caveat, dimension, chart_rule).

Covers id derivation, body validation, metric extensions, embedding text, skill.md
sections, save-time validation (incl. cross-entry refs), the seed content, and that the
new embeddable types land in their retrieval buckets (chart_rule stays out of the index).
"""
import pytest

from backend.knowledge import analysis_meta as am
from backend.knowledge import embedding_text as et
from backend.knowledge import entry_validator as ev
from backend.knowledge import seed as seed_mod
from backend.knowledge import skill_builder
from backend.retrieval import vector_retriever
from backend.retrieval.context_builder import RetrievalService
from backend.store import models


# ---- type registry ----------------------------------------------------------
def test_new_types_registered():
    for t in ("playbook", "caveat", "dimension", "chart_rule"):
        assert t in models.ENTRY_TYPES
    # playbook/caveat/dimension embed; chart_rule is policy (loaded fresh, not embedded).
    assert {"playbook", "caveat", "dimension"} <= models.EMBEDDABLE_TYPES
    assert "chart_rule" not in models.EMBEDDABLE_TYPES
    assert "rule" not in models.EMBEDDABLE_TYPES


# ---- id derivation ----------------------------------------------------------
def test_id_derivation_per_type():
    assert models.derive_id("playbook", {"playbook": "revenue_drop"}) == "playbook:revenue_drop"
    assert models.derive_id("dimension", {"dimension": "category"}) == "dimension:category"
    assert models.derive_id("chart_rule", {"shape": "trend"}) == "chart_rule:trend"
    # caveat id slugs the title with Vietnamese diacritics stripped (clean, deterministic).
    assert models.derive_id("caveat", {"title": "Phạm vi dữ liệu"}) == "caveat:pham_vi_du_lieu"


def test_default_name_per_type():
    assert models.default_name("playbook", {"playbook": "revenue_drop"}) == "revenue_drop"
    assert models.default_name("caveat", {"title": "Phạm vi dữ liệu"}) == "Phạm vi dữ liệu"
    assert models.default_name("dimension", {"dimension": "category"}) == "category"
    assert models.default_name("chart_rule", {"shape": "trend"}) == "trend"


# ---- body validation --------------------------------------------------------
def test_playbook_body_valid_and_forbids_extra():
    body = models.validate_body("playbook", {
        "playbook": "p", "use_when": "x",
        "diagnostic_steps": [{"title": "s1", "expected_shape": "kpi"}]})
    assert body["playbook"] == "p"
    assert body["diagnostic_steps"][0]["title"] == "s1"
    with pytest.raises(ValueError):
        models.validate_body("playbook", {"playbook": "p", "bogus": 1})


def test_chart_rule_enum_enforced():
    assert models.validate_body("chart_rule",
                                {"shape": "trend", "chart_type": "line"})["shape"] == "trend"
    with pytest.raises(ValueError):
        models.validate_body("chart_rule", {"shape": "nope", "chart_type": "line"})


def test_metric_extensions_are_optional_and_roundtrip():
    # A plain metric (no extensions) still validates with the defaults.
    plain = models.validate_body("metric", {"metric": "m", "formula": "SUM(x.y)"})
    assert plain["direction"] == "higher_is_better"
    assert plain["decomposition"] == []
    # Extensions round-trip when provided.
    ext = models.validate_body("metric", {
        "metric": "doanh_thu", "formula": "SUM(chi_tiet_don_hang_ban.thanh_tien)",
        "decomposition": ["so_don_hang"], "interpretation_down": "giảm"})
    assert ext["decomposition"] == ["so_don_hang"]
    assert ext["interpretation_down"] == "giảm"


# ---- save-time validation (entry_validator) ---------------------------------
def test_playbook_bad_sql_hint_rejected():
    errs = ev.validate_entry("playbook", {
        "playbook": "p",
        "diagnostic_steps": [{"title": "s", "sql_hint": "SELECT ("}]})
    assert any("sql_hint" in e for e in errs)


def test_playbook_placeholder_hint_parses():
    hint = am.PLAYBOOKS[0]["diagnostic_steps"][0]["sql_hint"]
    assert "{date_from}" in hint  # it is a template
    assert ev.validate_entry("playbook", {
        "playbook": "p", "diagnostic_steps": [{"title": "s", "sql_hint": hint}]}) == []


def test_playbook_needs_a_step():
    errs = ev.validate_entry("playbook", {"playbook": "p", "diagnostic_steps": []})
    assert any("diagnostic_steps" in e for e in errs)


def test_dimension_bad_column_rejected():
    errs = ev.validate_entry("dimension", {
        "dimension": "d", "table": "khach_hang", "column": "khong_co_cot"})
    assert any("khong_co_cot" in e for e in errs)


def test_dimension_valid_without_repo_skips_cross_refs():
    # No repo -> join_requirement existence is skipped; a real table.column still checked.
    assert ev.validate_entry("dimension", {
        "dimension": "category", "table": "danh_muc_san_pham", "column": "ten_danh_muc",
        "join_requirement": "revenue_by_category"}) == []


def test_dimension_join_requirement_checked_with_repo(kb):
    # With a repo, an unknown join_path is rejected...
    errs = ev.validate_entry("dimension", {
        "dimension": "d", "table": "khach_hang", "column": "ten_khach_hang",
        "join_requirement": "khong_ton_tai"}, repo=kb.repo)
    assert any("khong_ton_tai" in e for e in errs)
    # ...but present once the join_path entry exists.
    kb.save("join_path", {"name": "jp1", "tables": ["khach_hang"],
                          "joins": ["khach_hang.khach_hang_id = don_hang_ban.khach_hang_id"]})
    assert ev.validate_entry("dimension", {
        "dimension": "d", "table": "khach_hang", "column": "ten_khach_hang",
        "join_requirement": "jp1"}, repo=kb.repo) == []


def test_playbook_metric_ref_checked_with_repo(kb):
    errs = ev.validate_entry("playbook", {
        "playbook": "p", "main_metrics": ["khong_co_metric"],
        "diagnostic_steps": [{"title": "s"}]}, repo=kb.repo)
    assert any("khong_co_metric" in e for e in errs)


# ---- embedding text ---------------------------------------------------------
def test_embedding_text_for_analytic_types():
    pb = et.build_embedding_text({"id": "playbook:p", "type": "playbook", "body": {
        "playbook": "revenue_drop", "use_when": "why revenue fell",
        "main_metrics": ["doanh_thu"],
        "diagnostic_steps": [{"title": "compare periods"}]}})
    assert "TYPE: playbook" in pb and "USE_WHEN" in pb and "compare periods" in pb

    dm = et.build_embedding_text({"id": "dimension:category", "type": "dimension", "body": {
        "dimension": "category", "table": "danh_muc_san_pham", "column": "ten_danh_muc",
        "aliases": ["ngành hàng"]}})
    assert "TYPE: dimension" in dm and "danh_muc_san_pham.ten_danh_muc" in dm

    cv = et.build_embedding_text({"id": "caveat:x", "type": "caveat", "body": {
        "title": "Phạm vi dữ liệu", "content": "chỉ đến 2025"}})
    assert "TYPE: caveat" in cv and "Phạm vi dữ liệu" in cv


def test_chart_rule_has_no_embedding_text():
    assert et.build_embedding_text({"id": "chart_rule:trend", "type": "chart_rule",
                                    "body": {"shape": "trend", "chart_type": "line"}}) == ""
    assert et.is_embeddable("chart_rule") is False


# ---- skill.md rendering -----------------------------------------------------
def test_skill_md_has_analytic_sections(kb):
    # Minimal playbook (no metric/dimension refs) so the empty-fixture save passes.
    kb.save("playbook", {"playbook": "revenue_drop", "use_when": "why revenue fell",
                         "diagnostic_steps": [{"title": "compare periods"}]})
    kb.save("dimension", {"dimension": "category", "table": "danh_muc_san_pham",
                          "column": "ten_danh_muc"})
    kb.save("caveat", am.caveats("2024-01-01", "2025-06-24")[0])
    kb.save("chart_rule", am.CHART_RULES[0])
    md = skill_builder.render_skill_md(kb.repo)
    for sec in ("# Analysis Playbooks", "# Dimensions", "# Analysis Caveats", "# Chart Rules"):
        assert sec in md, sec
    assert "Playbook: revenue_drop" in md


# ---- seed content -----------------------------------------------------------
def test_seed_analysis_entries_all_validate():
    entries = seed_mod.seed_analysis("2024-01-01", "2025-06-24")
    kinds = {e["type"] for e in entries}
    assert kinds == {"playbook", "dimension", "caveat", "chart_rule"}
    counts = {}
    for e in entries:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
        # pydantic shape + (parse-only, no repo) semantic checks both clean.
        body = models.validate_body(e["type"], e["body"])
        assert ev.validate_entry(e["type"], body) == [], (e["type"], e["body"])
    assert counts == {"playbook": 4, "dimension": 8, "caveat": 6, "chart_rule": 5}


# ---- retrieval buckets ------------------------------------------------------
def test_analytic_types_land_in_their_buckets(kb):
    # A minimal playbook (no metric/dimension refs) so cross-entry checks pass.
    kb.save("playbook", {"playbook": "revenue_drop", "use_when": "vì sao doanh thu giảm",
                         "diagnostic_steps": [{"title": "so sánh kỳ"}]})
    kb.save("dimension", {"dimension": "category", "table": "danh_muc_san_pham",
                          "column": "ten_danh_muc"})
    kb.save("caveat", {"title": "Phạm vi dữ liệu", "content": "chỉ đến 2025-06-24"})
    kb.save("chart_rule", {"shape": "trend", "chart_type": "line"})

    buckets = vector_retriever.retrieve_buckets(
        kb.embedder, kb.index, "phan tich doanh thu giam theo nganh hang",
        topk=vector_retriever.analytic_topk())
    assert "revenue_drop" in [h.metadata.get("playbook") for h in buckets["playbook"]]
    assert "category" in [h.metadata.get("dimension") for h in buckets["dimension"]]
    assert buckets["caveat"], "caveat bucket should not be empty"
    # chart_rule is policy: never embedded, so it is absent from the index.
    assert not kb.index.contains("chart_rule:trend")
    assert kb.index.contains("playbook:revenue_drop")


def test_editing_playbook_is_live_on_next_retrieval(kb):
    kb.save("playbook", {"playbook": "revenue_drop", "use_when": "old",
                         "diagnostic_steps": [{"title": "s"}]})
    rsvc = RetrievalService.from_knowledge_service(kb)
    v0 = kb.repo.get_kb_version()
    kb.save("playbook", {"playbook": "revenue_drop", "use_when": "new wording",
                         "diagnostic_steps": [{"title": "s"}]})
    assert kb.repo.get_kb_version() > v0
    assert rsvc.ensure_fresh() is True  # the edit is visible without a restart
