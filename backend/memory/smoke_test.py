"""Phase 4 smoke test: conversation store + compact memory + retrieval plan.

Runs against a throwaway conversations.db in the temp dir (no GPU/embedder needed):

    D:\\SQL\\SQL\\.venv\\Scripts\\python.exe -m backend.memory.smoke_test

Set PYTHONPATH=D:\\SQL\\SQLNEW and PYTHONIOENCODING=utf-8.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from backend.memory.memory_builder import build_compact_memory, looks_like_follow_up
from backend.memory.result_summarizer import extract_entities, summarize
from backend.memory.retrieval_planner import build_retrieval_plan
from backend.memory.store import ConversationStore


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    db_path = Path(tempfile.gettempdir()) / "sqlnew_conv_smoke.db"
    if db_path.exists():
        db_path.unlink()
    store = ConversationStore(path=db_path)

    cid = store.create("smoke")
    cols = ["khach_hang_id", "ten_khach_hang", "doanh_thu"]
    rows = [
        {"khach_hang_id": "KH_001", "ten_khach_hang": "Tap Hoa Minh Anh", "doanh_thu": 12000000},
        {"khach_hang_id": "KH_002", "ten_khach_hang": "Sieu Thi Hoa Binh", "doanh_thu": 10500000},
    ]
    summary = summarize(cols, rows)
    entities = extract_entities(cols, rows)

    store.save_sql_turn(
        cid, "Top 10 khách hàng có doanh thu cao nhất tháng này",
        standalone_question="Top 10 khách hàng có doanh thu cao nhất",
        intent="NEW_QUERY",
        selected_tables=["khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"],
        selected_metrics=["doanh_thu"], selected_filters=["thang gan nhat"],
        generated_sql=("SELECT khach_hang.khach_hang_id, khach_hang.ten_khach_hang, "
                       "SUM(chi_tiet_don_hang_ban.thanh_tien) AS doanh_thu FROM khach_hang "
                       "JOIN don_hang_ban ON khach_hang.khach_hang_id = don_hang_ban.khach_hang_id "
                       "JOIN chi_tiet_don_hang_ban ON don_hang_ban.don_hang_id = "
                       "chi_tiet_don_hang_ban.don_hang_id GROUP BY 1,2 ORDER BY doanh_thu DESC LIMIT 10"),
        result_columns=cols, result_preview=rows, result_entities=entities, result_summary=summary)

    turns = store.load_recent(cid)
    print("=== compact memory window ===")
    print(build_compact_memory(turns))
    print("\n=== extracted entities ===")
    for e in entities:
        print(f"  {e.type}: {e.id_value} / {e.name_value}  ({e.id_column}, {e.name_column})")

    print("\n=== retrieval plans (follow-ups against the stored turn) ===")
    # (message, want_needs_retrieval, want_intent) -- covers all 7 plan intents.
    checks = [
        ("chỉ ở Hà Nội", True, "REFINE_PREVIOUS_QUERY"),
        ("what did you query?", False, "ASK_ABOUT_PREVIOUS_SQL"),
        ("cái nào cao nhất?", False, "ASK_ABOUT_PREVIOUS_RESULT"),
        ("tại sao khách hàng này cao nhất?", False, "EXPLAIN_PREVIOUS_RESULT"),
        ("sản phẩm họ đã mua là gì?", True, "DRILL_DOWN_PREVIOUS_RESULT"),
        ("Top 5 công ty theo doanh thu", True, "NEW_QUERY"),
    ]
    failures = 0
    for msg, want_retrieval, want_intent in checks:
        plan = build_retrieval_plan(msg, turns)
        ok = (plan.needs_retrieval == want_retrieval) and (plan.intent_hint == want_intent)
        failures += 0 if ok else 1
        print(f"  {msg!r}")
        print(f"     follow_up={looks_like_follow_up(msg)} intent={plan.intent_hint} "
              f"needs_retrieval={plan.needs_retrieval} pinned={plan.pinned_tables}")
        print(f"     reason={plan.intent_reason!r}")
        print(f"     => {'PASS' if ok else 'FAIL (want %s / %s)' % (want_retrieval, want_intent)}")

    # §20: a drill-down retrieval query must carry the previous result entity forward.
    drill = build_retrieval_plan("sản phẩm họ đã mua là gì?", turns)
    drill_ok = "KH_001" in (drill.retrieval_query or "")
    failures += 0 if drill_ok else 1
    print(f"\n  drill-down query carries entity KH_001 => {'PASS' if drill_ok else 'FAIL'}")
    print(f"     retrieval_query={drill.retrieval_query!r}")

    print("\n=== retrieval plans (no prior turn) ===")
    no_history = [
        ("cái đó thì sao?", False, "INSUFFICIENT_CONTEXT"),
        ("Top 10 khách hàng có doanh thu cao nhất", True, "NEW_QUERY"),
    ]
    for msg, want_retrieval, want_intent in no_history:
        plan = build_retrieval_plan(msg, [])
        ok = (plan.needs_retrieval == want_retrieval) and (plan.intent_hint == want_intent)
        failures += 0 if ok else 1
        print(f"  {msg!r}")
        print(f"     intent={plan.intent_hint} needs_retrieval={plan.needs_retrieval} "
              f"reason={plan.intent_reason!r}")
        print(f"     => {'PASS' if ok else 'FAIL (want %s / %s)' % (want_retrieval, want_intent)}")

    # entity extraction sanity
    top = entities[0] if entities else None
    ent_ok = bool(top and top.type == "khach_hang" and top.id_value == "KH_001"
                  and top.name_value == "Tap Hoa Minh Anh")
    print(f"\n  entity extraction => {'PASS' if ent_ok else 'FAIL'}")
    failures += 0 if ent_ok else 1

    print(f"\n[smoke] {'ALL PASS' if failures == 0 else str(failures) + ' FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
