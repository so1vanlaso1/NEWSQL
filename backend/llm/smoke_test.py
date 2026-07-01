"""Smoke test for the Phase 7/8 layer.

Runnable from the SQLNEW directory with the SQL venv (has sqlglot/httpx/torch):

    $env:PYTHONPATH="D:\\SQL\\SQLNEW"; $env:PYTHONIOENCODING="utf-8"
    D:\\SQL\\SQL\\.venv\\Scripts\\python.exe -m backend.llm.smoke_test          # offline-safe
    D:\\SQL\\SQL\\.venv\\Scripts\\python.exe -m backend.llm.smoke_test --live   # full pipeline

Offline mode (default) exercises the parser + validator + executor against sales.db and
probes the endpoint (reporting cleanly if the tunnel is down). ``--live`` additionally
loads the embedder, retrieves context, calls the model, and validates + runs the SQL.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from backend import config
from backend.execution.query_runner import run_query
from backend.llm.client import get_client
from backend.llm.prompt_builder import build_system_prompt, build_user_prompt
from backend.llm.response_parser import parse_decision
from backend.validation.sql_validator import validate

_DEMO_Q = "Top 10 khach hang theo doanh thu"
_GOOD_SQL = (
    "SELECT khach_hang.khach_hang_id, khach_hang.ten_khach_hang, "
    "SUM(chi_tiet_don_hang_ban.thanh_tien) AS doanh_thu "
    "FROM khach_hang "
    "JOIN don_hang_ban ON khach_hang.khach_hang_id = don_hang_ban.khach_hang_id "
    "JOIN chi_tiet_don_hang_ban ON don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id "
    "GROUP BY khach_hang.khach_hang_id, khach_hang.ten_khach_hang "
    "ORDER BY doanh_thu DESC LIMIT 10"
)


def check_offline() -> bool:
    ok = True
    print("== 1. parser ==")
    d = parse_decision('```json\n{"intent":"NEW_QUERY","needs_sql":true,"answer":"Day la ket qua:",'
                        f'"sql":"{_GOOD_SQL}"}}\n```')
    print("   intent=", d.intent, "needs_sql=", d.needs_sql, "sql_clean=", d.sql is not None)
    ok &= d.intent == "NEW_QUERY" and d.needs_sql and d.sql is not None
    g = parse_decision("no json here at all")
    print("   garbage -> intent=", g.intent, "parse_ok=", g.parse_ok)
    ok &= not g.parse_ok

    print("== 2. validator ==")
    vr = validate(_GOOD_SQL, resolved_tables={"khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"})
    print("   good sql ok=", vr.ok, "referenced=", vr.referenced_tables)
    ok &= vr.ok and set(vr.referenced_tables) == {"khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"}
    bad = validate("DELETE FROM khach_hang")
    print("   DELETE blocked=", not bad.ok)
    ok &= not bad.ok
    dia = validate("SELECT khách_hàng.khach_hang_id FROM khach_hang LIMIT 5")
    print("   diacritic blocked=", not dia.ok)
    ok &= not dia.ok

    print("== 3. executor (sales.db) ==")
    qr = run_query(vr.normalized_sql)
    print("   error=", qr.error, "row_count=", qr.row_count)
    if qr.rows:
        print("   top=", qr.rows[0])
    ok &= qr.error is None and qr.row_count > 0

    print("== 4. endpoint probe ==")
    client = get_client()
    print("   base=", config.LLM_BASE_URL, "resolved_model=", client.resolve_model())
    res = client.chat("You reply with valid JSON only.",
                      'Return exactly this JSON: {"ping":"pong"}')
    if res.error:
        print("   [endpoint OFFLINE]", res.error)
    else:
        print("   status ok, latency_ms=", res.latency_ms, "used_json_object=", res.used_json_object)
        print("   content[:120]=", res.content[:120].replace("\n", " "))
    return ok


def check_live() -> bool:
    print("== live: build retrieval service (loads embedder) ==")
    from backend.knowledge.service import KnowledgeService
    from backend.memory.memory_builder import build_compact_memory
    from backend.retrieval.context_builder import RetrievalService
    from backend.retrieval.skill_context import build_llm_skill_context

    svc = KnowledgeService.build(load_embedder=True)
    rsvc = RetrievalService.from_knowledge_service(svc)
    resolved = rsvc.retrieve(_DEMO_Q, [])
    skill_ctx = build_llm_skill_context(_DEMO_Q, build_compact_memory([]), resolved,
                                        rules=rsvc.global_rules)
    system = build_system_prompt()
    user = build_user_prompt(skill_ctx, today=date.today().isoformat(),
                             data_min=config.DATA_MIN_DATE, data_max=config.DATA_MAX_DATE)
    print("== live: LLM call ==")
    res = get_client().chat(system, user)
    if res.error:
        print("   [endpoint OFFLINE]", res.error)
        return False
    print("   latency_ms=", res.latency_ms, "used_json_object=", res.used_json_object,
          "usage=", res.usage)
    d = parse_decision(res.content)
    print("   intent=", d.intent, "needs_sql=", d.needs_sql)
    print("   sql=", d.sql)
    if not d.sql:
        print("   [no SQL returned]  answer=", d.answer)
        return False
    vr = validate(d.sql, resolved_tables=set(resolved.final_tables))
    print("   valid=", vr.ok, "errors=", vr.errors, "warnings=", vr.warnings)
    if not vr.ok:
        return False
    qr = run_query(vr.normalized_sql)
    print("   run error=", qr.error, "row_count=", qr.row_count)
    if qr.rows:
        print("   top row=", qr.rows[0])
    return qr.error is None and qr.row_count > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="run the full pipeline against the model")
    args = ap.parse_args()
    offline_ok = check_offline()
    print("\nOFFLINE CHECKS:", "PASS" if offline_ok else "FAIL")
    if args.live:
        live_ok = check_live()
        print("\nLIVE CHECK:", "PASS" if live_ok else "FAIL (endpoint offline or bad SQL)")
        return 0 if (offline_ok and live_ok) else 1
    return 0 if offline_ok else 1


if __name__ == "__main__":
    sys.exit(main())
