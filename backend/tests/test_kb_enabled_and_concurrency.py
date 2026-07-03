"""Regression tests for review findings: the `enabled` flag must be honored live, and
the shared IndexStore must be safe under concurrent read/write (Phase 9/10 review)."""
import threading

import numpy as np

from backend.knowledge import skill_builder
from backend.retrieval.context_builder import RetrievalService


def test_disabling_a_global_rule_drops_it_from_live_context(kb):
    kb.save("rule", {"section": "global", "title": "no_writes",
                     "items": ["SELECT only: never write."]})
    rsvc = RetrievalService.from_knowledge_service(kb)
    assert any(r.title == "no_writes" for r in rsvc.global_rules)

    kb.save("rule", {"section": "global", "title": "no_writes",
                     "items": ["SELECT only: never write."]}, enabled=False)
    assert rsvc.ensure_fresh() is True
    assert not any(r.title == "no_writes" for r in rsvc.global_rules)


def test_disabling_a_normalization_rule_drops_its_mappings(kb):
    kb.save("rule", {"section": "normalization", "title": "norm",
                     "items": ["công ty -> cong_ty"]})
    rsvc = RetrievalService.from_knowledge_service(kb)
    assert "cong ty" in rsvc.norm_map

    kb.save("rule", {"section": "normalization", "title": "norm",
                     "items": ["công ty -> cong_ty"]}, enabled=False)
    rsvc.ensure_fresh()
    assert "cong ty" not in rsvc.norm_map


def test_render_skill_md_excludes_disabled_entries(kb):
    kb.save("metric", {"metric": "doanh_thu",
                       "formula": "SUM(chi_tiet_don_hang_ban.thanh_tien)",
                       "required_tables": ["chi_tiet_don_hang_ban"]}, enabled=False)
    md = skill_builder.render_skill_md(kb.repo)
    assert "doanh_thu" not in md


def test_indexstore_search_is_safe_under_concurrent_writes(kb):
    idx = kb.index
    dim = idx.dim
    errors: list = []
    stop = threading.Event()

    def reader():
        q = np.ones(dim, dtype=np.float32)
        while not stop.is_set():
            try:
                idx.search(q, k=8)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

    def writer():
        v = np.ones(dim, dtype=np.float32)
        for i in range(300):
            try:
                idx.upsert(f"tmp_{i % 20}", v, "doc", {"type": "metric", "metric": "tmp"})
                if i % 4 == 0:
                    idx.delete(f"tmp_{i % 20}")
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
        stop.set()

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads.append(threading.Thread(target=writer))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:3]
