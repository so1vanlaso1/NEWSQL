"""Golden question evaluation (plan §25.5).

Runs a curated set of Vietnamese questions and prints a pass/fail table. Two layers, both
offline (no GPU, no live LLM required):

- **Mode routing** (always): every question's detected mode must match ``expected_mode``.
- **Analytic plans** (``--deep``): each analytic question is run through the DETERMINISTIC
  planner (``client=None`` -> deterministic fallback pack) against a hashing-embedder KB and
  must yield a valid plan with a task count within ``[task_min, task_max]``. ``expected_playbook``
  is checked *softly* (reported, never failed) because retrieval ranking with the hashing
  embedder is approximate — the real embedder is only needed for a live run.

Exit code is non-zero if any HARD check fails.

Usage:
    PYTHONPATH=<repo> .venv/Scripts/python.exe scripts/golden_eval.py [--deep]
                      [--file golden/golden_questions.jsonl]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Force the dependency-free hashing embedder + isolated temp stores BEFORE importing backend,
# so a golden run never needs a GPU and never touches the real knowledge.db.
_TMP = Path(tempfile.mkdtemp(prefix="sqlnew_golden_"))
os.environ.setdefault("EMBEDDER", "hashing")
os.environ.setdefault("EMBED_LOAD_IN_4BIT", "0")
os.environ.setdefault("KNOWLEDGE_DB_PATH", str(_TMP / "knowledge.db"))
os.environ.setdefault("INDEX_DIR", str(_TMP / "index"))
os.environ.setdefault("CONV_DB_PATH", str(_TMP / "conversations.db"))
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:1/v1")  # never reach a real LLM
os.environ.setdefault("SEARCH_ENABLED", "0")
os.environ.setdefault("LOG_LEVEL", "ERROR")


def load_golden(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def check_modes(rows: list[dict]) -> list[dict]:
    from backend.analysis import mode_detector as md

    out = []
    for r in rows:
        got = md.detect_mode(r["question"], [], last_review=None)
        out.append({"id": r["id"], "check": "mode", "expected": r["expected_mode"],
                    "got": got, "ok": got == r["expected_mode"], "hard": True})
    return out


def check_analytic_plans(rows: list[dict]) -> list[dict]:
    from backend import config
    from backend.analysis import context_builder, date_window, planner as planner_mod
    from backend.knowledge import analysis_meta
    from backend.knowledge.service import KnowledgeService
    from backend.embeddings.embedder import get_embedder
    from backend.embeddings.index_store import IndexStore
    from backend.retrieval.context_builder import RetrievalService
    from backend.store.repository import Repository

    # Seed a hashing-embedder KB once (mirrors seed.seed_analysis staging order).
    emb = get_embedder()
    kb = KnowledgeService(Repository(path=_TMP / "gkb.db"), emb,
                          IndexStore(dim=emb.dim, model_name=emb.model_name))
    kb.stage("metric", {
        "metric": "doanh_thu", "formula": "SUM(chi_tiet_don_hang_ban.thanh_tien)",
        "required_tables": ["chi_tiet_don_hang_ban"], "aliases": ["doanh thu", "revenue"],
        "direction": "higher_is_better", "decomposition": ["so_don_hang"],
        "interpretation_down": "giảm do mất khách"})
    for e in analysis_meta.build_analysis_entries(config.DATA_MIN_DATE, config.DATA_MAX_DATE):
        kb.stage(e["type"], e["body"])
    kb.embed_pending()
    rsvc = RetrievalService.from_knowledge_service(kb)

    out = []
    for r in [x for x in rows if x.get("kind") == "analytic"]:
        q = r["question"]
        try:
            query = context_builder.build_retrieval_query(q, None)
            ctx = context_builder.build_analytic_context(
                rsvc, q, mode="ANALYTIC_MODE", retrieval_query=query,
                pinned_tables=[], review_seed=None, recent_turns=[])
            window = date_window.resolve_window(q, None, config.DATA_MIN_DATE, config.DATA_MAX_DATE)
            plan = planner_mod.plan_review(ctx, window, None, None)  # client=None -> fallback
            n = len(plan.tasks)
            lo, hi = r.get("task_min", 2), r.get("task_max", 6)
            out.append({"id": r["id"], "check": "plan", "expected": f"{lo}-{hi} tasks",
                        "got": f"{n} tasks", "ok": lo <= n <= hi and not plan.is_downgrade,
                        "hard": True})
            # Soft playbook check (retrieval ranking is approximate under hashing).
            exp_pb = r.get("expected_playbook")
            if exp_pb:
                got_pb = (plan.playbook_used or "").replace("playbook:", "")
                out.append({"id": r["id"], "check": "playbook(soft)", "expected": exp_pb,
                            "got": got_pb or "(none)", "ok": got_pb == exp_pb, "hard": False})
        except Exception as exc:  # noqa: BLE001 - a crash IS a hard failure for that row
            out.append({"id": r["id"], "check": "plan", "expected": "valid plan",
                        "got": f"ERROR {exc.__class__.__name__}: {exc}", "ok": False, "hard": True})
    return out


def print_table(results: list[dict]) -> None:
    print(f"{'ID':<6} {'CHECK':<15} {'EXPECTED':<28} {'GOT':<28} RESULT")
    print("-" * 92)
    for r in results:
        mark = "PASS" if r["ok"] else ("FAIL" if r["hard"] else "warn")
        print(f"{r['id']:<6} {r['check']:<15} {str(r['expected'])[:27]:<28} "
              f"{str(r['got'])[:27]:<28} {mark}")


def main() -> int:
    ap = argparse.ArgumentParser(description="SQLNEW golden question evaluation")
    ap.add_argument("--file", default=str(_REPO / "golden" / "golden_questions.jsonl"))
    ap.add_argument("--deep", action="store_true",
                    help="also run deterministic analytic-plan checks (seeds a hashing KB)")
    ap.add_argument("--offline", action="store_true",
                    help="offline only (default; kept for symmetry — no live LLM is ever used)")
    args = ap.parse_args()

    rows = load_golden(Path(args.file))
    results = check_modes(rows)
    if args.deep:
        results += check_analytic_plans(rows)

    print_table(results)
    hard = [r for r in results if r["hard"]]
    hard_fail = [r for r in hard if not r["ok"]]
    soft_fail = [r for r in results if not r["hard"] and not r["ok"]]
    print("-" * 92)
    print(f"HARD: {len(hard) - len(hard_fail)}/{len(hard)} passed"
          + (f"  ({len(soft_fail)} soft warnings)" if soft_fail else ""))
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
