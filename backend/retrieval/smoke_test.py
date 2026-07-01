"""Phase 3 smoke test: run Vietnamese queries through RetrievalService and check
the resolved tables / metrics / joins.

Run (needs the Qwen embedder + the built index):

    D:\\SQL\\SQL\\.venv\\Scripts\\python.exe -m backend.retrieval.smoke_test

Set PYTHONPATH=D:\\SQL\\SQLNEW and PYTHONIOENCODING=utf-8.
"""
from __future__ import annotations

import sys

from backend.knowledge.service import KnowledgeService
from backend.memory.memory_builder import build_compact_memory
from backend.retrieval.context_builder import RetrievalService
from backend.retrieval.skill_context import approx_token_count, build_llm_skill_context

# (query, pinned_tables, expected_tables_subset, expected_metric_or_None)
CASES = [
    ("Top 10 khách hàng có doanh thu cao nhất", [],
     {"khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"}, "doanh_thu"),
    ("doanh thu theo công ty", [],
     {"cong_ty", "don_hang_ban", "chi_tiet_don_hang_ban"}, "doanh_thu"),
    ("sản phẩm bán chạy nhất ngành hàng Sữa", [],
     {"san_pham", "chi_tiet_don_hang_ban"}, None),
    ("chỉ ở Hà Nội", ["khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"],
     {"khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban", "vi_tri"}, None),
    ("Công ty FMCG An Phát", [],
     {"cong_ty"}, None),
    ("tỉ lệ viếng thăm thành công theo nhân viên", [],
     {"lich_su_vieng_tham", "nhan_vien"}, "ty_le_vieng_tham_thanh_cong"),
]


def _fmt_join(j) -> str:
    return f"{j.condition}  [{j.source}]"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    svc = KnowledgeService.build(load_embedder=True)
    rsvc = RetrievalService.from_knowledge_service(svc)
    print(f"[smoke] index={len(rsvc.index)} value_aliases={len(rsvc.value_index.by_alias)} "
          f"metrics={len(rsvc.metric_defs)} join_paths={len(rsvc.join_path_defs)}\n")

    failures = 0
    for query, pinned, want_tables, want_metric in CASES:
        ctx = rsvc.retrieve(query, pinned)
        got_tables = set(ctx.final_tables)
        got_metrics = {m.metric for m in ctx.metrics}
        missing = want_tables - got_tables
        metric_ok = (want_metric is None) or (want_metric in got_metrics)

        print(f"Q: {query!r}  pinned={pinned}")
        print(f"   tables : {ctx.final_tables}")
        print(f"   metrics: {[m.metric for m in ctx.metrics]}")
        print("   joins  :")
        for j in ctx.joins:
            print(f"      {_fmt_join(j)}")
        if ctx.matched_values:
            print("   values :")
            for mv in ctx.matched_values:
                print(f"      {mv.value} -> {mv.table}.{mv.column} (via '{mv.matched_alias}')")
        if ctx.debug.get("unreachable_tables"):
            print(f"   UNREACHABLE: {ctx.debug['unreachable_tables']}")

        # Phase 6: serialize the compact LLM skill context this turn would send.
        skill_context = build_llm_skill_context(query, build_compact_memory([]), ctx)
        print(f"   --- LLM skill context (~{approx_token_count(skill_context)} tokens) ---")
        for line in skill_context.splitlines():
            print(f"   | {line}")

        ok = (not missing) and metric_ok
        if not ok:
            failures += 1
            if missing:
                print(f"   !! MISSING tables: {sorted(missing)}")
            if not metric_ok:
                print(f"   !! MISSING metric: {want_metric}")
        print(f"   => {'PASS' if ok else 'FAIL'}\n")

    print(f"[smoke] {len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
