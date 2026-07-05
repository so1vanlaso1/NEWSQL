"""Prompts for the analytic pipeline (plan §13, §19, §9).

Phase 13 ships the review **planner** prompt (turn an analytic question into 2-6 validated
SQL diagnostic tasks) plus a per-task **repair** prompt. The writer/follow-up prompts arrive
in Phase 15. Everything is JSON-only and SQLite-pinned, mirroring ``llm/prompt_builder.py``.
"""
from __future__ import annotations

from typing import Optional

from backend.analysis import evidence as evidence_mod
from backend.analysis.models import (
    AdvisorOutput,
    AnalyticContext,
    ChartSpec,
    DateWindow,
    EvidenceItem,
    ReviewSeed,
)

# The planner's JSON envelope (kept literal for stability, matches plan §13.2).
_PLAN_SHAPE = """{
  "analysis_title": "tiêu đề phân tích ngắn gọn bằng ngôn ngữ người dùng",
  "mode_downgrade": null,
  "playbook_used": "playbook:<slug> hoặc \\"\\"",
  "date_range": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "compare_from": "YYYY-MM-DD", "compare_to": "YYYY-MM-DD"},
  "tasks": [
    {"task_id": "t1", "title": "...", "purpose": "...", "expected_shape": "kpi", "sql": "SELECT ..."}
  ]
}"""

_PLANNER_SYSTEM = """You are the analytic query PLANNER for a Vietnamese FMCG sales database (SQLite).

Your job: turn ONE business/investigation question into 2 to 6 diagnostic SQL tasks that,
together, gather the evidence needed to answer it. You do NOT write the final report — you
only design the SQL tasks. A separate deterministic engine runs, profiles, charts, and
narrates them.

Return VALID JSON ONLY (the exact shape is given), no markdown, no text around it.

Task design rules:
- Each task is ONE executable SQLite SELECT using ONLY the tables, columns, joins and
  metrics provided in the CONTEXT below. Never invent tables/columns.
- Use the provided PLAYBOOK's diagnostic steps as your guide when one is given; adapt them
  to the question. Prefer 3-5 tasks that build a clear story (confirm the change, then
  decompose it by driver and by dimension).
- expected_shape is one of: "kpi" (one metric, this period vs previous), "by_dimension"
  (a metric split by a grouping, both periods), "trend" (a metric over months), "top_n"
  (ranking of entities). Choose the shape that matches each task.

Column-naming conventions (REQUIRED so the profiler can read your results):
- kpi          -> SELECT 'ky_nay' AS ky, <agg> AS gia_tri ... UNION ALL
                  SELECT 'ky_truoc' AS ky, <agg> AS gia_tri ...   (exactly two rows)
- by_dimension -> SELECT <label> AS nhom,
                  SUM(CASE WHEN <date in current>  THEN <expr> ELSE 0 END) AS ky_nay,
                  SUM(CASE WHEN <date in previous> THEN <expr> ELSE 0 END) AS ky_truoc
                  ... GROUP BY <label> ORDER BY ky_nay DESC
- trend        -> SELECT strftime('%Y-%m', dh.ngay_dat_hang) AS thang, <agg> AS gia_tri
                  ... GROUP BY 1 ORDER BY thang
- top_n        -> SELECT <label> AS ten, <agg> AS gia_tri ... GROUP BY <label>
                  ORDER BY gia_tri DESC LIMIT 10

SQLite + safety rules:
- SELECT queries ONLY. NEVER INSERT/UPDATE/DELETE/DROP/ALTER/PRAGMA. One statement per task.
- Revenue "doanh_thu" = SUM(chi_tiet_don_hang_ban.thanh_tien). Join the order header to its
  lines on don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id.
- Filter to real orders with don_hang_ban.trang_thai = 'NORMAL'.
- Date column is don_hang_ban.ngay_dat_hang; filter with the literal date range GIVEN below
  (BETWEEN 'from' AND 'to' for the current period, 'compare_from'/'compare_to' for previous).
  Use SQLite date functions only (strftime), never MySQL/Postgres syntax.
- Identifiers MUST be khong dau snake_case exactly as provided. Diacritics are allowed only
  inside string literals.
- If an ENTITY FILTER is given (a previous-result deep dive), add it to EVERY task's WHERE.

Downgrade rule:
- If the question is actually a single, direct lookup (one number / one list, no investigation
  needed), set "mode_downgrade": "NORMAL_SQL" and return an empty "tasks" list. Otherwise keep
  "mode_downgrade": null."""


def _fmt_list(values, limit: int = 0) -> str:
    vals = [str(v) for v in (values or []) if str(v).strip()]
    if limit and len(vals) > limit:
        vals = vals[:limit]
    return ", ".join(vals)


def _serialize_schema(ctx: AnalyticContext) -> list[str]:
    sc = ctx.schema_context
    lines: list[str] = []
    if sc is None:
        return lines
    if sc.tables:
        lines.append("BẢNG (tables) & cột chính:")
        for t in sc.tables:
            cols = _fmt_list([c.column for c in t.columns], limit=16)
            meaning = (t.meaning or t.meaning_en or "").strip().replace("\n", " ")
            head = f"- {t.table}" + (f" — {meaning}" if meaning else "")
            lines.append(head)
            if cols:
                lines.append(f"    cột: {cols}")
    if sc.joins:
        lines.append("JOINS:")
        for j in sc.joins:
            lines.append(f"- {j.condition}")
    if sc.metrics:
        lines.append("CHỈ SỐ (metrics):")
        for m in sc.metrics:
            lines.append(f"- {m.metric} = {m.formula}")
    if sc.matched_values:
        vals = "; ".join(
            f"{mv.value} -> {mv.id_column}='{mv.id_value}'" if mv.id_column else mv.value
            for mv in sc.matched_values)
        lines.append(f"GIÁ TRỊ ĐÃ KHỚP: {vals}")
    return lines


def _serialize_analytic(ctx: AnalyticContext) -> list[str]:
    lines: list[str] = []
    if ctx.metric_analysis:
        lines.append("PHÂN RÃ CHỈ SỐ:")
        for m in ctx.metric_analysis:
            bits = []
            if m.get("decomposition"):
                bits.append("phân rã: " + _fmt_list(m["decomposition"]))
            if m.get("direction"):
                bits.append(str(m["direction"]))
            lines.append(f"- {m.get('metric','')}: " + "; ".join(bits))
    if ctx.playbooks:
        pb = ctx.playbooks[0]
        lines.append(f"PLAYBOOK GỢI Ý (playbook:{pb.get('playbook','')}):")
        if pb.get("use_when"):
            lines.append(f"    dùng khi: {pb['use_when']}")
        steps = pb.get("diagnostic_steps") or []
        if steps:
            lines.append("    các bước chẩn đoán gợi ý:")
            for i, s in enumerate(steps, 1):
                meta = f"shape={s.get('expected_shape','')}"
                if s.get("metric"):
                    meta += f", metric={s['metric']}"
                if s.get("dimension"):
                    meta += f", dimension={s['dimension']}"
                lines.append(f"     {i}. {s.get('title','')} ({meta})")
    if ctx.dimensions:
        lines.append("CHIỀU PHÂN TÍCH (dimensions):")
        for d in ctx.dimensions:
            lines.append(f"- {d.get('dimension','')} -> {d.get('table','')}.{d.get('column','')}")
    if ctx.caveats:
        lines.append("LƯU Ý DỮ LIỆU:")
        for c in ctx.caveats:
            title = c.get("title", "")
            content = (c.get("content", "") or "").strip().replace("\n", " ")
            lines.append(f"- {title}: {content}" if content else f"- {title}")
    return lines


def build_planner_user_prompt(ctx: AnalyticContext, window: DateWindow,
                              seed: Optional[ReviewSeed] = None) -> str:
    dw = ctx.data_window or {}
    parts: list[str] = [f"CÂU HỎI: {ctx.question}"]
    if seed is not None and seed.ok:
        if seed.base_fact:
            parts.append(f"BỐI CẢNH (từ kết quả trước): {seed.base_fact}")
        ef = seed.entity_filter_sql()
        if ef:
            parts.append(f"ENTITY FILTER (thêm vào MỌI WHERE của mọi task): {ef}")
    parts.append(
        f"CỬA SỔ DỮ LIỆU: {dw.get('min','')} .. {dw.get('max','')} "
        "(câu hỏi về kỳ ngoài phạm vi này sẽ không có dòng nào).")
    parts.append(
        "KHOẢNG THỜI GIAN ĐỀ XUẤT:\n"
        f"  kỳ này   ({window.label}): '{window.date_from}' .. '{window.date_to}'\n"
        f"  kỳ trước ({window.compare_label}): '{window.compare_from}' .. '{window.compare_to}'\n"
        "  Dùng đúng các mốc ngày này trong SQL (có thể điều chỉnh nếu câu hỏi nêu kỳ khác).")

    context_lines = _serialize_schema(ctx) + _serialize_analytic(ctx)
    parts.append("--- NGỮ CẢNH ---\n" + "\n".join(context_lines))
    parts.append("Trả về JSON ĐÚNG shape sau (không thêm khoá, không markdown):\n" + _PLAN_SHAPE)
    return "\n\n".join(parts)


def build_planner_system_prompt() -> str:
    return _PLANNER_SYSTEM


def build_planner_retry_user_prompt(previous_user_prompt: str, errors: list[str]) -> str:
    """Second-chance planner prompt: some tasks failed validation (plan §13.3 step 5)."""
    err = "\n".join(f"- {e}" for e in errors) or "- không đủ task hợp lệ (cần ít nhất 2)."
    return (
        f"{previous_user_prompt}\n\n"
        "Kế hoạch trước có task KHÔNG hợp lệ và đã bị loại:\n"
        f"{err}\n\n"
        "Hãy sửa và trả về LẠI đúng JSON shape, đảm bảo có ÍT NHẤT 2 task SELECT SQLite hợp lệ, "
        "chỉ dùng bảng/cột đã cho, đúng quy ước đặt tên cột (ky/ky_nay/ky_truoc/gia_tri/nhom/thang/ten).")


# ---- final report writer (plan §19) ----------------------------------------
_WRITER_SYSTEM = """Bạn là biên tập viên phân tích kinh doanh cho dữ liệu FMCG Việt Nam.

Viết báo cáo markdown bằng đúng ngôn ngữ của người dùng, ưu tiên tiếng Việt rõ ràng.
Quy tắc bắt buộc:
- Mọi con số phải đến từ EVIDENCE. Không bịa số, nguyên nhân, bảng, cột, hay nguồn.
- Tách dữ kiện khỏi diễn giải; dùng "có thể", "khả năng", "nên kiểm tra" cho nhận định tương quan.
- Không chép lại toàn bộ bảng trong prose; frontend sẽ render bảng và biểu đồ từ dữ liệu cấu trúc.
- Nếu có caveat hoặc bước lỗi, nêu trong "Lưu ý" thay vì đoán.
- Web/source chỉ được dùng trong mục "## Bối cảnh thị trường". Mỗi ý dẫn nguồn bằng [n] khớp
  với danh sách "sources"; TUYỆT ĐỐI không trộn số liệu web vào các mục dữ liệu nội bộ. Nếu
  "sources" rỗng thì bỏ hẳn mục này.
- Không dùng JSON. Trả về markdown thuần.

Cấu trúc mong muốn:
## <tiêu đề>
## Tóm tắt điều hành
## Bằng chứng chính
## Diễn giải
## Bối cảnh thị trường   (CHỈ khi "sources" không rỗng; mỗi ý dẫn [n])
## Khuyến nghị cải thiện
## Lưu ý
## Phân tích tiếp theo
"""


def _compact_rows(ev: EvidenceItem, limit: int = 10) -> list[dict]:
    return [dict(r) for r in (ev.rows or [])[:limit]]


def _compact_evidence(evidence: list[EvidenceItem]) -> list[dict]:
    out: list[dict] = []
    for ev in evidence:
        out.append({
            "evidence_id": ev.evidence_id,
            "title": ev.title,
            "purpose": ev.purpose,
            "source_type": ev.source_type,
            "status": ev.status,
            "metric": ev.metric,
            "columns": ev.columns,
            "rows": _compact_rows(ev),
            "profile": ev.profile,
            "profile_sentence": evidence_mod.profile_sentence(ev),
        })
    return out


def _compact_charts(charts: list[ChartSpec]) -> list[dict]:
    return [{
        "chart_id": c.chart_id,
        "type": c.type,
        "title": c.title,
        "unit": c.unit,
        "evidence_id": c.evidence_id,
    } for c in charts]


def build_writer_system_prompt() -> str:
    return _WRITER_SYSTEM


def build_writer_user_prompt(*, question: str, title: str, evidence: list[EvidenceItem],
                             charts: list[ChartSpec], advice: AdvisorOutput,
                             caveats: list[str], sources: list[dict] | None = None) -> str:
    import json

    bundle = {
        "question": question,
        "analysis_title": title,
        "evidence": _compact_evidence(evidence),
        "charts": _compact_charts(charts),
        "advisor": advice.model_dump(),
        "caveats": caveats,
        "sources": sources or [],
    }
    return (
        "Dựa CHỈ trên gói dữ liệu sau, hãy viết báo cáo markdown hoàn chỉnh.\n"
        "Không thêm số ngoài evidence. Không nhắc 'theo JSON'.\n\n"
        + json.dumps(bundle, ensure_ascii=False, indent=2)
    )


# ---- analytic follow-up (plan §9) ------------------------------------------
_FOLLOWUP_SYSTEM = """Bạn trả lời câu hỏi tiếp nối về MỘT báo cáo phân tích đã lưu.

Chỉ dùng REVIEW_EVIDENCE đã cung cấp. Không chạy SQL, không bịa thêm bảng hoặc số.
Nếu câu hỏi cần truy vấn/phân tích mới ngoài evidence, đặt needs_new_analysis=true.
Trả về JSON hợp lệ, không markdown ngoài JSON:
{
  "answer": "câu trả lời ngắn, có dẫn evidence_id khi hữu ích",
  "needs_new_analysis": false,
  "matching_evidence_ids": ["..."],
  "follow_up_suggestions": ["..."]
}
"""


def build_followup_system_prompt() -> str:
    return _FOLLOWUP_SYSTEM


def build_followup_user_prompt(*, question: str, review_question: str,
                               evidence: list[EvidenceItem],
                               charts: list[ChartSpec],
                               caveats: list[str]) -> str:
    import json

    bundle = {
        "followup_question": question,
        "original_review_question": review_question,
        "review_evidence": _compact_evidence(evidence),
        "charts": _compact_charts(charts),
        "caveats": caveats,
    }
    return json.dumps(bundle, ensure_ascii=False, indent=2)


# ---- web-search planner (plan §16.4) ---------------------------------------
_RESEARCH_SYSTEM = """Bạn là trợ lý phân tích. Bạn CHỈ có công cụ search_internet để tra cứu
bối cảnh thị trường/ngành/đối thủ bên ngoài hệ thống nội bộ.

Hãy phát ra các lệnh gọi search_internet cần thiết TRONG MỘT LƯỢT (mỗi lệnh một câu truy vấn
tiếng Việt ngắn gọn). Không bịa số, không tự trả lời. Nếu không cần tra cứu web thì không gọi
công cụ nào. Tối đa 5 lệnh gọi; ưu tiên truy vấn bám sát phát hiện từ dữ liệu nội bộ."""


def build_research_system_prompt() -> str:
    return _RESEARCH_SYSTEM


def build_research_user_prompt(*, title: str, evidence: list[EvidenceItem],
                               window: Optional[DateWindow] = None,
                               dimensions: Optional[list[dict]] = None) -> str:
    """Seed the web-search planner with the analysis title + real SQL findings (plan §16.4)."""
    parts: list[str] = [f"CHỦ ĐỀ PHÂN TÍCH: {title}"]
    findings = [evidence_mod.profile_sentence(ev)
                for ev in (evidence or []) if ev.status == "success"]
    if findings:
        parts.append("PHÁT HIỆN TỪ DỮ LIỆU NỘI BỘ:\n"
                     + "\n".join(f"- {f}" for f in findings[:6]))
    if window is not None and window.label:
        parts.append(f"KỲ PHÂN TÍCH: {window.label} (so với {window.compare_label}).")
    dims = [d.get("dimension", "") for d in (dimensions or []) if d.get("dimension")]
    if dims:
        parts.append("CHIỀU ĐANG XÉT: " + _fmt_list(dims, limit=6))
    parts.append(
        "Hãy đề xuất các truy vấn tra cứu bối cảnh thị trường/ngành/đối thủ có liên quan "
        "(giá cả, xu hướng ngành hàng FMCG, khuyến mãi đối thủ, sức mua…) bằng cách gọi "
        "search_internet. Chỉ gọi công cụ, không viết văn.")
    return "\n\n".join(parts)


# ---- geolocation tool + prospect writer (Phase 19) -------------------------
_GEO_RESEARCH_SYSTEM = """Bạn là trợ lý phân tích thị trường FMCG. Bạn có công cụ find_nearby_stores
để tra cứu số cửa hàng bán lẻ quanh một khu vực trên Google Maps và độ phủ khách hàng hiện có.

Khi câu hỏi liên quan đến doanh thu/độ phủ theo KHU VỰC (quận, tỉnh, tuyến nhân viên, quanh một
khách hàng...), hãy gọi find_nearby_stores(area=...) cho khu vực đó để lấy bối cảnh thị trường
(mỗi khu vực một lần). Nếu câu hỏi KHÔNG gắn với địa lý thì không gọi công cụ nào. Chỉ gọi công
cụ, không tự viết văn, không bịa số."""


def build_geo_research_system_prompt() -> str:
    return _GEO_RESEARCH_SYSTEM


def build_geo_research_user_prompt(*, title: str, question: str,
                                   evidence: list[EvidenceItem]) -> str:
    """Seed the geo tool planner with the title + question + internal findings (Phase 19)."""
    parts: list[str] = [f"CHỦ ĐỀ PHÂN TÍCH: {title}", f"CÂU HỎI: {question}"]
    findings = [evidence_mod.profile_sentence(ev)
                for ev in (evidence or []) if ev.status == "success"]
    if findings:
        parts.append("PHÁT HIỆN TỪ DỮ LIỆU NỘI BỘ:\n"
                     + "\n".join(f"- {f}" for f in findings[:6]))
    parts.append(
        "Nếu câu hỏi gắn với một khu vực địa lý, hãy gọi find_nearby_stores(area=\"...\") cho khu "
        "vực đó để đánh giá độ phủ thị trường (số cửa hàng bán lẻ quanh đó so với khách hàng hiện "
        "có). Chỉ gọi công cụ, không viết văn.")
    return "\n\n".join(parts)


_GEO_WRITER_SYSTEM = """Bạn là chuyên viên phát triển thị trường FMCG. Bạn nhận danh sách cửa hàng
bán lẻ GẦN một vị trí (lấy từ Google Maps) mà CHƯA có trong tập khách hàng, kèm số liệu độ phủ.

Hãy viết PHẦN DIỄN GIẢI ngắn gọn bằng tiếng Việt:
- 1–2 câu tóm tắt cơ hội (dựa trên số cửa hàng tiềm năng và độ phủ khách hàng hiện tại).
- Mục "## Gợi ý mời hàng" với vài gạch đầu dòng hành động cụ thể (ưu tiên cửa hàng gần nhất, ưu
  tiên ngành hàng phù hợp, cách tiếp cận).
KHÔNG tự tạo bảng danh sách cửa hàng — hệ thống sẽ tự chèn bảng bên dưới. Chỉ dùng dữ liệu được
cung cấp, KHÔNG bịa thêm cửa hàng hay số liệu. Không nhắc tới 'JSON'."""


def build_geo_writer_system_prompt() -> str:
    return _GEO_WRITER_SYSTEM


def build_geo_writer_user_prompt(*, question: str, label: str, penetration: dict,
                                 prospects: list[dict], caveats: list[str]) -> str:
    import json

    bundle = {
        "cau_hoi": question,
        "khu_vuc": label,
        "do_phu_thi_truong": penetration,
        "cua_hang_tiem_nang": prospects,
        "luu_y": caveats,
    }
    return (
        "Dựa CHỈ trên dữ liệu sau, hãy viết phần diễn giải + gợi ý mời hàng (tiếng Việt).\n"
        "KHÔNG tạo bảng — chỉ viết văn và mục '## Gợi ý mời hàng'.\n\n"
        + json.dumps(bundle, ensure_ascii=False, indent=2)
    )


# ---- per-task repair (plan §14) --------------------------------------------
_TASK_REPAIR_SYSTEM = (
    "You fix a single SQLite SELECT for a Vietnamese FMCG sales database. Return VALID JSON "
    "ONLY: {\"sql\": \"<corrected SQLite SELECT>\"}. SELECT only, khong dau identifiers, keep "
    "the same result columns and intent, one statement, no markdown.")


def build_task_repair_system_prompt() -> str:
    return _TASK_REPAIR_SYSTEM


def build_task_repair_user_prompt(title: str, bad_sql: str, error: str,
                                  context_lines: str = "") -> str:
    ctx = f"\n\nNGỮ CẢNH SCHEMA:\n{context_lines}" if context_lines else ""
    return (
        f"Task: {title}\n"
        f"SQL bị lỗi:\n{bad_sql}\n\n"
        f"Lỗi:\n{error}{ctx}\n\n"
        'Trả về JSON: {"sql": "..."} với câu SELECT SQLite đã sửa (giữ nguyên các cột kết quả).')
