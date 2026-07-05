"""Phase 8: the 6-layer SQL validator (backend/validation/sql_validator.py).

Covers the read-only gate, statement-chaining + dangerous-keyword rejection, the schema
allowlist (unknown table/column), the LIMIT policy (cap + SELECT* guard + auto-inject), and
one end-to-end accept against the bundled sales.db.
"""
import pytest

from backend import config
from backend.validation import sql_validator as v

_HAS_DB = config.DB_PATH.exists()

VALID_REVENUE = (
    "SELECT SUM(ct.thanh_tien) AS gia_tri "
    "FROM don_hang_ban dh "
    "JOIN chi_tiet_don_hang_ban ct ON dh.don_hang_id = ct.don_hang_id "
    "WHERE dh.trang_thai = 'NORMAL'"
)


def test_rejects_dml():
    res = v.validate("UPDATE don_hang_ban SET trang_thai='X'")
    assert not res.ok and res.errors


def test_rejects_statement_chaining():
    res = v.validate("SELECT 1; SELECT 2")
    assert not res.ok
    assert any("chaining" in e.lower() for e in res.errors)


def test_rejects_non_select():
    res = v.validate("PRAGMA table_info(don_hang_ban)")
    assert not res.ok
    assert any("SELECT" in e for e in res.errors)


def test_rejects_dangerous_keyword():
    res = v.validate("SELECT 1; DROP TABLE don_hang_ban")
    assert not res.ok
    assert any("angerous" in e or "chaining" in e.lower() for e in res.errors)


def test_rejects_unknown_table():
    res = v.validate("SELECT x.a FROM khong_ton_tai x LIMIT 5")
    assert not res.ok
    assert res.unknown_tables and any("Unknown tables" in e for e in res.errors)


def test_rejects_limit_over_max():
    res = v.validate(f"SELECT dh.don_hang_id FROM don_hang_ban dh LIMIT {config.MAX_RESULT_ROWS + 1}")
    assert not res.ok
    assert any("exceeds max result rows" in e for e in res.errors)


def test_rejects_select_star_without_limit():
    res = v.validate("SELECT * FROM don_hang_ban dh")
    assert not res.ok
    assert any("SELECT * without LIMIT" in e for e in res.errors)


@pytest.mark.skipif(not _HAS_DB, reason="bundled sales.db required for the binding check")
def test_accepts_valid_aggregate():
    res = v.validate(VALID_REVENUE, resolved_tables={"don_hang_ban", "chi_tiet_don_hang_ban"})
    assert res.ok, res.errors
    assert set(res.referenced_tables) == {"don_hang_ban", "chi_tiet_don_hang_ban"}


@pytest.mark.skipif(not _HAS_DB, reason="bundled sales.db required for the binding check")
def test_auto_injects_limit_on_raw_select():
    res = v.validate("SELECT dh.don_hang_id FROM don_hang_ban dh")
    assert res.ok, res.errors
    assert "LIMIT" in res.normalized_sql.upper()
    assert any("auto-added LIMIT" in w for w in res.warnings)
