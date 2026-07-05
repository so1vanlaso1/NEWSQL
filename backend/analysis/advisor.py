"""Deterministic interpretation and improvement advice (Phase 15, plan §18).

The advisor reads profiled evidence and editable KB playbook/metric rules. It does not
invent facts and it never calls the LLM; its output is a structured set of bullets for the
writer and the skeleton fallback.
"""
from __future__ import annotations

from backend.analysis import evidence as evidence_mod
from backend.analysis.models import AdvisorOutput, AnalyticContext, EvidenceItem, ReviewPlan


def _dedupe(items: list[str], limit: int = 0) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        s = " ".join(str(item or "").split())
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if limit and len(out) >= limit:
            break
    return out


def _fmt_num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f):
        return f"{int(f):,}".replace(",", ".")
    return f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(v) -> str:
    if v is None:
        return ""
    try:
        return f"{float(v):.1f}%".replace(".", ",")
    except (TypeError, ValueError):
        return str(v)


def _metric_map(ctx: AnalyticContext) -> dict[str, dict]:
    return {m.get("metric", ""): m for m in (ctx.metric_analysis or []) if m.get("metric")}


def _playbook(ctx: AnalyticContext, plan: ReviewPlan | None) -> dict:
    if plan and plan.playbook_used:
        slug = plan.playbook_used.split(":", 1)[-1]
        for pb in ctx.playbooks or []:
            if pb.get("playbook") == slug:
                return pb
    return (ctx.playbooks or [{}])[0] if ctx.playbooks else {}


def _trend_label(trend: str) -> str:
    return {"down": "giảm", "up": "tăng", "flat": "đi ngang"}.get(trend or "", "thay đổi")


def _largest_kpi_move(evidence: list[EvidenceItem]) -> EvidenceItem | None:
    candidates = [
        ev for ev in evidence
        if ev.status == "success" and (ev.profile or {}).get("shape") == "kpi"
        and (ev.profile or {}).get("absolute_change") is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda ev: abs(float((ev.profile or {}).get("absolute_change") or 0)))


def _largest_dimension_move(evidence: list[EvidenceItem]) -> tuple[EvidenceItem, dict] | None:
    best: tuple[EvidenceItem, dict] | None = None
    best_abs = -1.0
    for ev in evidence:
        p = ev.profile or {}
        if ev.status != "success" or p.get("shape") != "by_dimension":
            continue
        mover = p.get("biggest_mover") or {}
        change = mover.get("change")
        try:
            score = abs(float(change))
        except (TypeError, ValueError):
            continue
        if score > best_abs:
            best_abs = score
            best = (ev, mover)
    return best


def _concentration_hits(evidence: list[EvidenceItem]) -> list[tuple[EvidenceItem, float]]:
    hits: list[tuple[EvidenceItem, float]] = []
    for ev in evidence:
        conc = (ev.profile or {}).get("top3_concentration")
        try:
            val = float(conc)
        except (TypeError, ValueError):
            continue
        if val > 0.5:
            hits.append((ev, val))
    return hits


def _metric_interpretations(evidence: list[EvidenceItem], metrics: dict[str, dict]) -> list[str]:
    bullets: list[str] = []
    for ev in evidence:
        p = ev.profile or {}
        metric = ev.metric or ""
        meta = metrics.get(metric) or {}
        if ev.status != "success" or p.get("shape") != "kpi":
            continue
        trend = p.get("trend")
        if trend == "down" and meta.get("interpretation_down"):
            bullets.append(f"{metric}: có thể {meta['interpretation_down']}")
        elif trend == "up" and meta.get("interpretation_up"):
            bullets.append(f"{metric}: có thể {meta['interpretation_up']}")
    return bullets


def _rule_bullets(pb: dict, *, concentration: bool, has_down: bool, has_up: bool) -> tuple[list[str], list[str]]:
    interpretations: list[str] = []
    improvements: list[str] = []
    interp_rules = list(pb.get("interpretation_rules") or [])
    improve_rules = list(pb.get("improvement_rules") or [])

    for rule in interp_rules:
        low = str(rule).lower()
        if concentration and ("top-3" in low or "chiếm" in low or "tập trung" in low):
            interpretations.append(f"Khả năng diễn giải: {rule}")
        elif has_down and ("giảm" in low or "kéo xuống" in low or "mất" in low):
            interpretations.append(f"Khả năng diễn giải: {rule}")
        elif has_up and ("tăng" in low or "bứt phá" in low):
            interpretations.append(f"Khả năng diễn giải: {rule}")
    if not interpretations and interp_rules:
        interpretations.append(f"Khả năng diễn giải: {interp_rules[0]}")

    for rule in improve_rules:
        low = str(rule).lower()
        if concentration and ("giảm mạnh" in low or "ngành" in low or "vùng" in low):
            improvements.append(str(rule))
        elif has_down and ("giảm" in low or "rà soát" in low or "tái" in low):
            improvements.append(str(rule))
        elif has_up and ("đảm bảo" in low or "mở rộng" in low):
            improvements.append(str(rule))
    if not improvements and improve_rules:
        improvements.append(str(improve_rules[0]))
    return interpretations, improvements


def build_advice(ctx: AnalyticContext, plan: ReviewPlan | None,
                 evidence: list[EvidenceItem]) -> AdvisorOutput:
    """Build deterministic advisor bullets from profiles + KB rules. Never raises."""
    try:
        return _build_advice(ctx, plan, evidence)
    except Exception:  # noqa: BLE001 - advice must never break a review
        return AdvisorOutput(
            driver_summary="Không tạo được phần tư vấn tự động từ hồ sơ bằng chứng.",
            interpretation_bullets=[],
            improvement_bullets=[],
            next_questions=[],
        )


def _build_advice(ctx: AnalyticContext, plan: ReviewPlan | None,
                  evidence: list[EvidenceItem]) -> AdvisorOutput:
    metrics = _metric_map(ctx)
    pb = _playbook(ctx, plan)
    interpretation: list[str] = []
    improvements: list[str] = []
    next_q: list[str] = []

    kpi_moves = [
        ev for ev in evidence
        if ev.status == "success" and (ev.profile or {}).get("shape") == "kpi"
    ]
    has_down = any((ev.profile or {}).get("trend") == "down" for ev in kpi_moves)
    has_up = any((ev.profile or {}).get("trend") == "up" for ev in kpi_moves)

    largest = _largest_kpi_move(evidence)
    driver_parts: list[str] = []
    if largest is not None:
        p = largest.profile or {}
        driver_parts.append(
            f"Biến động KPI lớn nhất là {largest.title}: {_trend_label(p.get('trend', ''))} "
            f"{_fmt_num(p.get('absolute_change'))}"
            + (f" ({_fmt_pct(p.get('pct_change'))})" if p.get("pct_change") is not None else "")
        )

    dim = _largest_dimension_move(evidence)
    if dim is not None:
        ev, mover = dim
        label = mover.get("label") or "một nhóm"
        change = mover.get("change")
        driver_parts.append(f"Nhóm kéo biến động mạnh nhất là {label} trong '{ev.title}' ({_fmt_num(change)}).")
        interpretation.append(
            f"Về dữ liệu, {label} là điểm cần soi trước vì có mức thay đổi tuyệt đối lớn nhất trong bảng '{ev.title}'."
        )
        next_q.append(f"Phân tích sâu nhóm {label}")

    conc_hits = _concentration_hits(evidence)
    concentration = bool(conc_hits)
    for ev, val in conc_hits[:2]:
        interpretation.append(
            f"Biến động ở '{ev.title}' khá tập trung: top-3 nhóm chiếm {_fmt_pct(val * 100)} mức thay đổi tuyệt đối."
        )

    interpretation.extend(_metric_interpretations(evidence, metrics))
    rule_i, rule_p = _rule_bullets(pb, concentration=concentration, has_down=has_down, has_up=has_up)
    interpretation.extend(rule_i)
    improvements.extend(rule_p)

    if has_down and not improvements:
        improvements.append("Rà soát các nhóm giảm mạnh nhất, kiểm tra độ phủ, tần suất đặt hàng và cơ cấu sản phẩm trước khi kết luận nguyên nhân.")
    if not interpretation:
        interpretation.extend(evidence_mod.profile_sentence(ev) for ev in evidence[:3] if ev.status == "success")

    dims_used = {t.dimension for t in (plan.tasks if plan else []) if t.dimension}
    dim_labels = {d.get("dimension"): ((d.get("aliases") or [d.get("dimension")])[0]) for d in ctx.dimensions}
    for d in ctx.dimensions:
        for target in d.get("drill_down_to") or []:
            if target not in dims_used and target in dim_labels:
                next_q.append(f"Phân tích sâu theo {dim_labels[target]}")

    return AdvisorOutput(
        driver_summary=" ".join(driver_parts) if driver_parts else "Chưa có một driver nổi bật duy nhất; nên đọc các bảng bằng chứng theo thứ tự.",
        interpretation_bullets=_dedupe(interpretation, 6),
        improvement_bullets=_dedupe(improvements, 5),
        next_questions=_dedupe(next_q, 4),
    )

