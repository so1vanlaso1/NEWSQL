"""Phase 10: KB live updates — save reflects on the next retrieval with no restart."""
import pytest

from backend.retrieval.context_builder import RetrievalService


def _save_revenue(kb, formula="SUM(chi_tiet_don_hang_ban.thanh_tien)",
                  tables=("chi_tiet_don_hang_ban",)):
    return kb.save("metric", {"metric": "doanh_thu", "formula": formula,
                              "required_tables": list(tables)})


def test_save_bumps_kb_version(kb):
    v0 = kb.repo.get_kb_version()
    _save_revenue(kb)
    assert kb.repo.get_kb_version() == v0 + 1


def test_edit_is_live_on_next_retrieval(kb):
    _save_revenue(kb)
    rsvc = RetrievalService.from_knowledge_service(kb)
    assert rsvc.metric_defs["doanh_thu"]["formula"] == "SUM(chi_tiet_don_hang_ban.thanh_tien)"

    # edit the formula via the UI path -> the next question must see it (no restart)
    _save_revenue(kb, formula="SUM(don_hang_ban.tong_tien)", tables=("don_hang_ban",))
    assert rsvc.ensure_fresh() is True
    assert rsvc.metric_defs["doanh_thu"]["formula"] == "SUM(don_hang_ban.tong_tien)"
    # nothing changed since -> no needless rebuild
    assert rsvc.ensure_fresh() is False


def test_strict_validation_rejects_broken_formula(kb):
    with pytest.raises(ValueError) as exc:
        kb.save("metric", {"metric": "bad", "formula": "SUM(don_hang_ban.khong_co)"})
    assert "khong_co" in str(exc.value)
    # a rejected save must NOT bump the version or persist the entry
    assert kb.repo.get("metric:bad") is None


def test_restore_reverts_to_a_previous_version(kb):
    _save_revenue(kb)  # create
    _save_revenue(kb, formula="SUM(don_hang_ban.tong_tien)", tables=("don_hang_ban",))  # update
    history = kb.repo.list_history("metric:doanh_thu")
    create_row = [h for h in history if h["action"] == "create"][0]

    result = kb.restore("metric:doanh_thu", create_row["history_id"])
    assert result is not None
    assert kb.repo.get("metric:doanh_thu")["body"]["formula"] == \
        "SUM(chi_tiet_don_hang_ban.thanh_tien)"
    # the restore itself is audited
    assert kb.repo.list_history("metric:doanh_thu")[0]["action"] == "restore"


def test_delete_records_history_and_bumps_version(kb):
    _save_revenue(kb)
    v = kb.repo.get_kb_version()
    assert kb.delete("metric:doanh_thu") is True
    assert kb.repo.get_kb_version() > v
    assert any(h["action"] == "delete" for h in kb.repo.list_history("metric:doanh_thu"))


def test_embedder_down_saves_as_pending(kb_plumbing):
    r = kb_plumbing.save("metric", {"metric": "m", "formula": "SUM(don_hang_ban.tong_tien)",
                                    "required_tables": ["don_hang_ban"]})
    assert r["embed_status"] == "pending"
    assert kb_plumbing.repo.get("metric:m") is not None  # save still succeeded
