"""Phase 10: save-time entry validation (backend/knowledge/entry_validator.py)."""
from backend.knowledge import entry_validator as ev


def test_valid_metric_passes():
    assert ev.validate_entry("metric", {
        "metric": "doanh_thu",
        "formula": "SUM(chi_tiet_don_hang_ban.thanh_tien)",
        "required_tables": ["chi_tiet_don_hang_ban"],
    }) == []


def test_metric_unknown_column_rejected():
    errs = ev.validate_entry("metric", {
        "metric": "x", "formula": "SUM(don_hang_ban.khong_ton_tai)"})
    assert any("khong_ton_tai" in e for e in errs)


def test_metric_unknown_table_rejected():
    errs = ev.validate_entry("metric", {
        "metric": "x", "formula": "SUM(khong_co_bang.cot)"})
    assert any("khong_co_bang" in e for e in errs)


def test_metric_unparseable_formula_rejected():
    errs = ev.validate_entry("metric", {"metric": "x", "formula": "SUM("})
    assert errs  # a parse failure is reported


def test_metric_empty_formula_rejected():
    errs = ev.validate_entry("metric", {"metric": "x", "formula": "  "})
    assert errs


def test_metric_required_table_must_exist():
    errs = ev.validate_entry("metric", {
        "metric": "x", "formula": "SUM(don_hang_ban.tong_tien)",
        "required_tables": ["nope"]})
    assert any("nope" in e for e in errs)


def test_join_path_unknown_table_rejected():
    errs = ev.validate_entry("join_path", {
        "name": "jp", "tables": ["don_hang_ban", "ghost"],
        "joins": ["don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id"]})
    assert any("ghost" in e for e in errs)


def test_join_path_bad_column_rejected():
    errs = ev.validate_entry("join_path", {
        "name": "jp", "tables": ["don_hang_ban"],
        "joins": ["don_hang_ban.khong_co = chi_tiet_don_hang_ban.don_hang_id"]})
    assert any("khong_co" in e for e in errs)


def test_value_bad_column_rejected():
    errs = ev.validate_entry("value", {
        "table": "khach_hang", "column": "khong_co_cot", "value": "X"})
    assert any("khong_co_cot" in e for e in errs)


def test_value_valid_passes():
    assert ev.validate_entry("value", {
        "table": "khach_hang", "column": "ten_khach_hang", "value": "Cua hang 1",
        "id_column": "khach_hang_id", "id_value": "KH_001"}) == []


def test_column_valid_and_invalid():
    assert ev.validate_entry("column", {"table": "don_hang_ban", "column": "tong_tien"}) == []
    assert ev.validate_entry("column", {"table": "don_hang_ban", "column": "nope"})


def test_rule_has_no_schema_checks():
    assert ev.validate_entry("rule", {"section": "global", "title": "t", "content": "c"}) == []
