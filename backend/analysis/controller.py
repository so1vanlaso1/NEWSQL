"""Analytic review controller — stage orchestration + SSE emission (plan §7).

Drives one analytic review end to end and yields progress events (mirroring the normal
pipeline's generator style in ``api/chat.py``):

    retrieve -> plan -> per-task run (+ evidence) -> profile -> charts -> persist -> final

Phase 13 shipped context->plan->tasks; Phase 14 adds profiling, evidence, deterministic
chart specs, and review persistence. The narrated report (LLM writer) arrives in Phase 15 —
until then the controller assembles a deterministic interim report from the profiles, so the
user always gets correct numbers, tables, and charts. Every stage degrades rather than
raising; the planner may signal a downgrade to the normal SQL pipeline (plan §3.4).
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Iterator, Optional

from backend import config
from backend.analysis import (
    advisor,
    chart_planner,
    context_builder,
    date_window,
    evidence as evidence_mod,
    planner as planner_mod,
    profiler,
    research as research_mod,
    task_runner,
    writer,
)
from backend.analysis.models import ChartSpec, EvidenceItem, ReviewRecord, ReviewSeed
from backend.analysis.task_runner import run_task
from backend.common.logging import get_logger
from backend.llm.client import LlmClient

log = get_logger(__name__)

ANALYTIC_MODES = ("ANALYTIC_MODE", "ANALYTIC_FROM_PREVIOUS_RESULT")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _step(step: str, status: str, **extra) -> dict:
    return {"type": "step", "step": step, "status": status, **extra}


def _dedupe(items: list[str]) -> list[str]:
    out, seen = [], set()
    for it in items:
        s = (it or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _collect_caveats(ctx, results, plan) -> list[str]:
    caveats: list[str] = []
    for c in ctx.caveats:
        content = (c.get("content") or c.get("title") or "").strip()
        if content:
            caveats.append(content)
    failed = [r for r in results if r.status == "failed"]
    skipped = [r for r in results if r.status == "skipped"]
    empty = [r for r in results if r.status == "success" and r.row_count == 0]
    if failed:
        caveats.append("Một số bước không chạy được: "
                       + ", ".join(r.title for r in failed) + ".")
    if skipped:
        caveats.append("Một số bước bị bỏ qua do vượt ngân sách thời gian: "
                       + ", ".join(r.title for r in skipped) + ".")
    if empty:
        caveats.append("Một số bước không có dữ liệu trong kỳ: "
                       + ", ".join(r.title for r in empty) + ".")
    if plan.source.startswith("fallback"):
        caveats.append("Kế hoạch phân tích được tạo tự động từ playbook (mô hình không đưa ra "
                       "kế hoạch hợp lệ).")
    return _dedupe(caveats)


def _suggestions(ctx, plan) -> list[str]:
    """A few deterministic drill-down suggestions from the plan's dimensions (plan §18 lite)."""
    dims_used = {t.dimension for t in plan.tasks if t.dimension}
    label = {d.get("dimension"): (d.get("aliases") or [d.get("dimension")])[0]
             for d in ctx.dimensions}
    out: list[str] = []
    for d in ctx.dimensions:
        drill = d.get("drill_down_to") or []
        for target in drill:
            if target not in dims_used and target in label:
                out.append(f"Phân tích sâu theo {label.get(target, target)}")
        if len(out) >= 3:
            break
    return _dedupe(out)[:3]


def _deterministic_report(title: str, evidence: list[EvidenceItem], caveats: list[str]) -> str:
    lines = [f"## {title}", "", "## Tóm tắt"]
    any_finding = False
    for ev in evidence:
        if ev.status == "success":
            lines.append(f"- {evidence_mod.profile_sentence(ev)}")
            any_finding = True
    if not any_finding:
        lines.append("- Chưa thu thập được bằng chứng định lượng cho câu hỏi này.")
    lines += ["", "## Bằng chứng"]
    for ev in evidence:
        badge = {"success": "", "failed": " (lỗi)", "skipped": " (bỏ qua)"}.get(ev.status, "")
        lines.append(f"- {ev.title}{badge}")
    if caveats:
        lines += ["", "## Lưu ý"]
        lines += [f"- {c}" for c in caveats]
    lines += ["",
              "> Báo cáo tạm thời — số liệu và bảng bên dưới là chính xác; phần diễn giải chi "
              "tiết bằng AI sẽ được bổ sung ở bước sau."]
    return "\n".join(lines)


def _short_answer(title: str, evidence: list[EvidenceItem]) -> str:
    firsts = [evidence_mod.profile_sentence(e) for e in evidence if e.status == "success"]
    if firsts:
        return " ".join(firsts[:2])
    n = len([e for e in evidence if e.status != "success"])
    return f"Đã chạy phân tích nhưng {n} bước chưa có dữ liệu. " + title


def _final_response(*, conversation_id: str, turn_id: str, mode: str, review: ReviewRecord,
                    llm_model: str) -> dict:
    """A ChatResponse-compatible dict (extra analytic keys are known ChatResponse fields)."""
    return {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "intent": mode,
        "needs_sql": False,
        "answer": review.findings_summary or _short_answer(review.question, review.evidence),
        "mode": mode,
        "review_id": review.review_id,
        "report_markdown": review.report_markdown,
        "evidence": [e.model_dump() for e in review.evidence],
        "charts": [c.model_dump() for c in review.charts],
        "sources": review.sources,
        "follow_up_suggestions": review.follow_up_suggestions,
        "caveats": review.caveats,
        "analytic_status": review.status,
        "llm_model": llm_model,
        "error": None if review.status != "failed" else "no evidence produced",
    }


def run_review(*, message: str, conversation_id: str, turns, rsvc, mode: str,
               seed: Optional[ReviewSeed], store, review_store,
               client: Optional[LlmClient], t0: Optional[float] = None) -> Iterator[dict]:
    """Run one analytic review, yielding SSE events. A terminal ``{"type":"downgrade"}``
    event tells the caller to re-route this turn into the normal SQL pipeline (plan §3.4)."""
    started = t0 if t0 is not None else time.monotonic()
    llm_model = client.resolve_model() if client is not None else ""

    # ---- stage 1: analytic context (retrieval required) -------------------
    yield _step("retrieve", "start", query=message)
    query = context_builder.build_retrieval_query(message, seed)
    pinned = list(seed.base_tables) if (seed and seed.ok) else []
    ctx = context_builder.build_analytic_context(
        rsvc, message, mode=mode, retrieval_query=query, pinned_tables=pinned,
        review_seed=seed, recent_turns=turns)
    tables = list(ctx.schema_context.final_tables) if ctx.schema_context else []
    yield _step("retrieve", "done", tables=tables)

    window = date_window.resolve_window(message, seed, config.DATA_MIN_DATE, config.DATA_MAX_DATE)

    # ---- stage 2: review plan (LLM call 1 + validation ladder) ------------
    yield _step("plan", "start")
    plan = planner_mod.plan_review(ctx, window, client, seed)
    if plan.is_downgrade:
        log.info("planner requested mode_downgrade -> NORMAL_SQL")
        yield {"type": "downgrade"}
        return
    if plan.date_window is None:
        plan.date_window = window
    title = plan.analysis_title or (message or "Phân tích").strip()
    yield _step("plan", "done", task_count=len(plan.tasks), source=plan.source,
                title=title, dropped=plan.dropped)

    # ---- stage 3+4: run tasks, profile, build evidence (progressive) ------
    review_id = "rv_" + uuid.uuid4().hex[:16]
    created = _now()
    evidence_items: list[EvidenceItem] = []
    results = []

    # Drive the runner task-by-task here so we can yield an SSE event between tasks
    # (the shared budget/skip logic lives in task_runner; controller owns emission).
    capped = plan.tasks[: config.ANALYTIC_MAX_TASKS]
    total = len(capped)
    for i, task in enumerate(capped, 1):
        yield _step("task", "start", task_index=i, task_total=total, title=task.title)
        if time.monotonic() - started > config.ANALYTIC_TOTAL_BUDGET_SEC:
            tr = task_runner.skipped_result(task, "Bỏ qua do vượt ngân sách thời gian phân tích.")
        else:
            tr = run_task(task, client=client)
        results.append(tr)
        prof = profiler.profile(tr.expected_shape, tr.columns, tr.rows)
        ev = evidence_mod.build_evidence(
            f"{review_id}_ev{i}", review_id, tr, prof, created_at=created)
        evidence_items.append(ev)
        yield {"type": "evidence", "evidence": ev.model_dump()}
        yield _step("task", "done", task_index=i, task_total=total, title=task.title,
                    task_status=tr.status, row_count=tr.row_count)

    yield _step("profile", "done", evidence_count=len(evidence_items))

    # ---- stage 6: deterministic chart specs -------------------------------
    yield _step("charts", "start")
    charts: list[ChartSpec] = chart_planner.plan_charts(evidence_items, ctx.chart_rules)
    for c in charts:
        yield {"type": "chart", "chart": c.model_dump()}
    yield _step("charts", "done", chart_count=len(charts))

    caveats = _collect_caveats(ctx, results, plan)

    # ---- stage 5: web research (optional; plan §16) -----------------------
    # Runs AFTER profiling so it is seeded with real findings. Budget-gated: research is the
    # FIRST thing skipped under wall-clock pressure (plan §7.3, §16.6). Web evidence is kept
    # separate from the SQL evidence handed to advisor/writer — it surfaces only as cited
    # ``sources`` in "Bối cảnh thị trường"; the SQL report ships regardless of the outcome.
    sources: list[dict] = []
    web_evidence: list[EvidenceItem] = []
    if config.SEARCH_ENABLED:
        yield _step("research", "start")
        if time.monotonic() - started > config.ANALYTIC_TOTAL_BUDGET_SEC:
            yield _step("research", "skipped", reason="Bỏ qua do vượt ngân sách thời gian")
            caveats = _dedupe(caveats + ["Không truy cập được nguồn web; báo cáo dựa trên dữ liệu nội bộ."])
        else:
            rr = research_mod.run_research(
                title=title, evidence_items=evidence_items, window=plan.date_window,
                dimensions=ctx.dimensions, client=client, review_store=review_store,
                review_id=review_id, created_at=created)
            if rr.skipped_reason:
                yield _step("research", "skipped", reason=rr.skipped_reason)
                caveats = _dedupe(caveats + ["Không truy cập được nguồn web; báo cáo dựa trên dữ liệu nội bộ."])
            else:
                sources = rr.sources
                web_evidence = rr.evidence
                yield _step("research", "done", source_count=len(sources),
                            query_count=len(rr.queries))

    # ---- stage 7+8: deterministic advice + streamed writer ----------------
    advice = advisor.build_advice(ctx, plan, evidence_items)

    yield _step("write", "start")
    write_result = None
    for kind, payload in writer.stream_report(
            client=client, title=title, question=message, evidence=evidence_items,
            charts=charts, advice=advice, caveats=caveats, sources=sources):
        if kind == "delta":
            yield {"type": "token", "delta": str(payload)}
        else:
            write_result = payload
    if write_result is None:
        write_result = writer.WriteResult(
            report_markdown=writer.skeleton_report(
                title=title, evidence=evidence_items, charts=charts,
                advice=advice, caveats=caveats, reason="writer không trả kết quả"),
            used_fallback=True,
            error="writer returned no result",
        )
    if write_result.used_fallback:
        caveats = _dedupe(caveats + ["Báo cáo dùng bản rút gọn vì writer LLM không phản hồi đầy đủ."])
    yield _step("write", "done", fallback=write_result.used_fallback,
                error=write_result.error)

    n_ok = len([e for e in evidence_items if e.status == "success"])
    status = "failed" if n_ok == 0 else (
        "degraded" if (n_ok != len(evidence_items) or write_result.used_fallback) else "complete")
    report_md = write_result.report_markdown
    findings = advice.driver_summary or _short_answer(title, evidence_items)
    followups = _dedupe(_suggestions(ctx, plan) + advice.next_questions)

    review = ReviewRecord(
        review_id=review_id, conversation_id=conversation_id, mode=mode, question=message,
        review_seed=seed if (seed and seed.ok) else None, plan=plan,
        findings_summary=findings, report_markdown=report_md,
        evidence=evidence_items + web_evidence, charts=charts, sources=sources,
        follow_up_suggestions=followups, caveats=caveats,
        status=status, created_at=created)

    # ---- stage 9: persist ----
    # Save the review FIRST, then link the turn to it only if that write succeeded, so a
    # failed review persist can never leave a turn pointing at a review that does not exist
    # (a dangling review_id -> 404 on reopen). Both writes are guarded: a DB error degrades
    # persistence but still ships the answer — the live response carries evidence/charts inline.
    yield _step("save", "start")
    turn_id = uuid.uuid4().hex
    review.turn_id = turn_id
    review_saved = False
    try:
        review_store.save_review(review)
        review_saved = True
    except Exception:  # noqa: BLE001 - a persistence error must not drop the answer
        log.exception("failed to persist review %s", review_id)
    try:
        saved_turn = store.save_non_sql_turn(
            conversation_id, message, intent=mode, answer=findings, turn_id=turn_id,
            review_id=review_id if review_saved else "", llm_model=llm_model,
            error="" if status != "failed" else "no evidence produced")
        turn_id = saved_turn.turn_id
    except Exception:  # noqa: BLE001 - never abort the stream on a turn-persist error
        log.exception("failed to persist analytic turn for review %s", review_id)
    yield _step("save", "done", review_id=review_id if review_saved else "",
                review_status=status)

    yield {"type": "final",
           "response": _final_response(conversation_id=conversation_id,
                                       turn_id=turn_id, mode=mode,
                                       review=review, llm_model=llm_model)}
