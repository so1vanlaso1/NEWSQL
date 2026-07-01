"""Phase 6 smoke test: the LLM skill-context serializer (GPU-free).

Builds a synthetic ``ResolvedContext`` (no embedder / no index) and checks the
serialized §27 context: every section present, SQLite (not MySQL) dialect, the data
window folded in, guardrail caps enforced (per-table columns, metric aliases), and
the no-retrieval (``resolved=None``) path. Run:

    D:\\SQL\\SQL\\.venv\\Scripts\\python.exe -m backend.retrieval.skill_context_smoke_test

Set PYTHONPATH=D:\\SQL\\SQLNEW and PYTHONIOENCODING=utf-8.
"""
from __future__ import annotations

import sys

from backend import config
from backend.knowledge.business_meta import rules as business_rules
from backend.retrieval.models import (
    GlobalRule,
    MatchedValue,
    ResolvedColumn,
    ResolvedContext,
    ResolvedJoin,
    ResolvedMetric,
    ResolvedTable,
)
from backend.retrieval.skill_context import ContextLimits, build_llm_skill_context


def _rules() -> list[GlobalRule]:
    return [GlobalRule(section=r.get("section", "global"), title=r.get("title", ""),
                       content=r.get("content", ""), items=list(r.get("items", [])))
            for r in business_rules(config.DATA_MIN_DATE, config.DATA_MAX_DATE)]


def _wide_khach_hang() -> ResolvedTable:
    # 16 columns (> default cap 14) to exercise per-table truncation.
    cols = [ResolvedColumn(table="khach_hang", column="khach_hang_id",
                           data_type="TEXT", meaning="customer id", is_key=True),
            ResolvedColumn(table="khach_hang", column="ten_khach_hang",
                           data_type="TEXT", meaning="customer name")]
    cols += [ResolvedColumn(table="khach_hang", column=f"attr_{i:02d}",
                            data_type="TEXT", meaning=f"attribute {i}") for i in range(1, 15)]
    return ResolvedTable(table="khach_hang", meaning="khách hàng",
                         meaning_en="customer / retailer / shop",
                         primary_key="khach_hang_id", columns=cols, reason="pinned")


def _synthetic_context() -> ResolvedContext:
    don_hang = ResolvedTable(
        table="don_hang_ban", meaning="đơn hàng bán", meaning_en="sales order header",
        primary_key="don_hang_id",
        columns=[
            ResolvedColumn(table="don_hang_ban", column="don_hang_id", meaning="order id", is_key=True),
            ResolvedColumn(table="don_hang_ban", column="khach_hang_id", meaning="customer fk"),
            ResolvedColumn(table="don_hang_ban", column="ngay_dat_hang", meaning="order date"),
            ResolvedColumn(table="don_hang_ban", column="trang_thai", meaning="order status"),
        ], reason="revenue join")
    chi_tiet = ResolvedTable(
        table="chi_tiet_don_hang_ban", meaning="chi tiết đơn hàng", meaning_en="sales order line",
        primary_key="chi_tiet_id",
        columns=[
            ResolvedColumn(table="chi_tiet_don_hang_ban", column="chi_tiet_id", meaning="line id", is_key=True),
            ResolvedColumn(table="chi_tiet_don_hang_ban", column="don_hang_id", meaning="order fk"),
            ResolvedColumn(table="chi_tiet_don_hang_ban", column="thanh_tien", meaning="net line total"),
            ResolvedColumn(table="chi_tiet_don_hang_ban", column="so_luong", meaning="quantity"),
            ResolvedColumn(table="chi_tiet_don_hang_ban", column="don_gia", meaning="unit price"),
        ], reason="revenue metric")

    metric = ResolvedMetric(
        metric="doanh_thu", formula="SUM(chi_tiet_don_hang_ban.thanh_tien)",
        aliases=["doanh thu", "doanh so", "sales", "revenue", "net sales",
                 "tong tien", "ALIAS_SEVEN", "ALIAS_EIGHT", "ALIAS_NINE"],
        required_tables=["don_hang_ban", "chi_tiet_don_hang_ban"],
        required_joins=["don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id"],
        use_when="revenue questions", notes="net after promotions", score=0.9)

    joins = [
        ResolvedJoin(left_table="khach_hang", left_column="khach_hang_id",
                     right_table="don_hang_ban", right_column="khach_hang_id",
                     condition="khach_hang.khach_hang_id = don_hang_ban.khach_hang_id"),
        ResolvedJoin(left_table="don_hang_ban", left_column="don_hang_id",
                     right_table="chi_tiet_don_hang_ban", right_column="don_hang_id",
                     condition="don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id"),
    ]
    focus = [
        ResolvedColumn(table="khach_hang", column="khach_hang_id", meaning="customer id", is_key=True),
        ResolvedColumn(table="khach_hang", column="ten_khach_hang", meaning="customer name"),
        ResolvedColumn(table="chi_tiet_don_hang_ban", column="thanh_tien", meaning="net line total"),
    ]
    values = [MatchedValue(table="cong_ty", column="ten_cong_ty", value="Cong ty FMCG An Phat",
                           id_column="cong_ty_id", id_value="CTY_001", matched_alias="An Phat")]
    return ResolvedContext(
        dialect="sqlite", retrieval_query="Top khách hàng doanh thu",
        pinned_tables=["khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"],
        final_tables=["khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"],
        tables=[_wide_khach_hang(), don_hang, chi_tiet], columns=focus,
        metrics=[metric], joins=joins, matched_values=values, rules=_rules())


REQUIRED_HEADERS = [
    "DATABASE SKILL CONTEXT", "SQL DIALECT:", "GLOBAL RULES:", "DATA WINDOW:",
    "REVENUE / METRIC POLICY:", "NORMALIZATION:", "CONVERSATION MEMORY:",
    "CURRENT USER MESSAGE:", "RETRIEVED METRIC RULES:", "RELEVANT TABLES:",
    "RELEVANT COLUMNS:", "ALLOWED JOINS:", "MATCHED VALUES:",
]


def _check(name: str, cond: bool, failures: list[str]) -> None:
    print(f"   [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    limits = ContextLimits.from_config()
    ctx = _synthetic_context()
    msg = "Top 10 khách hàng có doanh thu cao nhất tháng gần nhất"
    out = build_llm_skill_context(msg, "No previous SQL query.", ctx)

    print("=== serialized skill context (retrieval turn) ===\n")
    print(out)
    print("\n=== assertions ===")
    failures: list[str] = []

    for h in REQUIRED_HEADERS:
        _check(f"header present: {h}", h in out, failures)
    _check("dialect is SQLite", "SQLite" in out, failures)
    # The dialect must not be *prescribed* as MySQL (the plan doc's stale §27/§30 text);
    # the rule content legitimately forbids MySQL functions, which we want present.
    _check("dialect not MySQL", "SQL DIALECT:\nMySQL" not in out, failures)
    _check("MySQL functions forbidden", "Do NOT use MySQL" in out, failures)
    _check("data window year 2024 present", config.DATA_MIN_DATE in out, failures)
    _check("data window year 2025 present", config.DATA_MAX_DATE in out, failures)
    _check("wide table truncated", "more columns)" in out, failures)
    _check("metric aliases capped (7th hidden)", "ALIAS_SEVEN" not in out, failures)
    _check("metric first alias shown", "doanh thu" in out, failures)
    _check("matched value id rendered", "cong_ty_id=CTY_001" in out, failures)
    _check("standalone omitted when none", "STANDALONE QUESTION CANDIDATE:" not in out, failures)
    _check("revenue formula present", "SUM(chi_tiet_don_hang_ban.thanh_tien)" in out, failures)
    _check("relevant columns section distinct", "- khach_hang.ten_khach_hang: customer name" in out, failures)

    # Standalone rendered when provided and different from the message.
    out_sa = build_llm_skill_context(
        "now only in HCM", "PREVIOUS QUERY MEMORY:\n...", ctx,
        standalone_question="Top khách hàng doanh thu tại HCM")
    _check("standalone shown when provided", "STANDALONE QUESTION CANDIDATE:" in out_sa, failures)

    # No-retrieval path: resolved is None, rules supplied as fallback.
    out_none = build_llm_skill_context(
        "what did you query?", "PREVIOUS QUERY MEMORY:\nLast user question: ...",
        resolved=None, rules=_rules())
    print("\n=== serialized skill context (no-retrieval turn) ===\n")
    print(out_none)
    print("\n=== assertions (no-retrieval) ===")
    _check("none-path dialect present", "SQLite" in out_none, failures)
    _check("none-path dialect not MySQL", "SQL DIALECT:\nMySQL" not in out_none, failures)
    _check("none-path global rules present", "GLOBAL RULES:" in out_none, failures)
    _check("none-path tables answer-from-memory",
           "None (answering from memory)" in out_none, failures)
    _check("none-path columns None", "RELEVANT COLUMNS:\nNone" in out_none, failures)
    _check("none-path joins None", "ALLOWED JOINS:\nNone" in out_none, failures)
    _check("none-path memory present", "CONVERSATION MEMORY:" in out_none, failures)

    print(f"\n[smoke] {'ALL PASS' if not failures else f'{len(failures)} FAILURE(S): {failures}'}"
          f"  (~{len(out)} chars / ~{(len(out) + 3) // 4} tokens for the retrieval turn)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
