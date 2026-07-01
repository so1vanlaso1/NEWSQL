"""Phase 7 prompt construction (design §30, corrected for SQLite).

The system prompt pins the dialect + safety + JSON-only contract; the user prompt
wraps the already-built compact skill context (the single source of truth for
schema/rules/metrics/memory) plus the real TODAY date and the data window.

Extension vs the plan doc's §30: a friendly ``answer`` field so SQL turns can carry a
one-line Vietnamese preamble (the backend fills the actual numbers after running SQL).
The plan doc's example SQL is stale MySQL (``DATE_FORMAT``/``don_hang_ban_id``); the
prompt below forces SQLite functions and the real ``don_hang_id`` join key.
"""
from __future__ import annotations

from datetime import date

from backend import config

# The exact JSON envelope the model must return (kept as a literal for stability).
_JSON_SHAPE = """{
  "intent": "NEW_QUERY | REFINE_PREVIOUS_QUERY | ASK_ABOUT_PREVIOUS_SQL | ASK_ABOUT_PREVIOUS_RESULT | DRILL_DOWN_PREVIOUS_RESULT | EXPLAIN_PREVIOUS_RESULT | INSUFFICIENT_CONTEXT",
  "needs_sql": true,
  "standalone_question": "resolved standalone database question, or null",
  "answer": "short friendly reply in the user's language (a preamble for SQL turns; no fabricated numbers)",
  "answer_from_memory": "full answer when needs_sql is false, otherwise null",
  "sql": "one SQLite SELECT query when needs_sql is true, otherwise null",
  "used_previous_context": false,
  "memory_update": {
    "selected_tables": [],
    "selected_columns": [],
    "selected_metrics": [],
    "selected_filters": [],
    "referenced_previous_entities": []
  }
}"""

_SYSTEM_PROMPT = """You are a conversational SQL agent for a Vietnamese FMCG sales database.

Your job:
1. Classify the current user message into exactly one intent (list below).
2. If SQL is needed, generate ONE executable SQLite SELECT query using ONLY the
   provided tables, columns, joins, metrics, and matched values.
3. If SQL is not needed, answer fully from the conversation memory provided.
4. Always write a short, friendly reply in the SAME LANGUAGE as the user
   (Vietnamese if the user wrote Vietnamese).
5. Never invent tables, columns, joins, result rows, or values.

Dialect is SQLite. Use SQLite syntax ONLY:
- Dates: date('now'), date('now','start of month'), date('now','-1 month'),
  strftime('%Y-%m', ngay_dat_hang), strftime('%Y', ngay_dat_hang).
  Do NOT use MySQL (DATE_FORMAT, CURDATE, DATE_ADD ... INTERVAL) or Postgres syntax.
- Identifiers (table/column names) MUST be khong dau snake_case exactly as provided
  (khach_hang, chi_tiet_don_hang_ban, ngay_dat_hang). NEVER put Vietnamese diacritics
  in an identifier. Diacritics are allowed ONLY inside string literals
  (for example: WHERE tinh_thanh = 'Ha Noi').
- Revenue "doanh_thu" = SUM(chi_tiet_don_hang_ban.thanh_tien). Join the order header to
  its lines on don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id.
- SELECT queries only. NEVER INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE/PRAGMA.
- Always add a sensible LIMIT (for "top N" use N; otherwise LIMIT 200 or less).
- When a MATCHED VALUES entry is given for something the user named, filter by its id
  column and id_value (e.g. WHERE cong_ty.cong_ty_id = 'CTY_001'), NOT by retyping the
  display name - the stored name may differ from what the user typed.
- When listing or ranking entities (khach_hang, san_pham, cong_ty, nhan_vien,
  nha_phan_phoi, ...), SELECT the entity's id column (e.g. khach_hang_id) together with
  its ten_ name, so later follow-up questions can drill into a specific row.

Allowed intents:
- NEW_QUERY: a brand-new database question.
- REFINE_PREVIOUS_QUERY: modifies the previous query (add a filter, change period/sort).
- ASK_ABOUT_PREVIOUS_SQL: asks what SQL/tables were used (answer from memory, no SQL).
- ASK_ABOUT_PREVIOUS_RESULT: asks about the previous result rows (answer from memory).
- DRILL_DOWN_PREVIOUS_RESULT: asks for new detail about an entity from the last result.
- EXPLAIN_PREVIOUS_RESULT: asks to interpret the last result (answer from memory).
- INSUFFICIENT_CONTEXT: the reference is unclear or unanswerable from memory/schema.

Rules:
- needs_sql=true  => "sql" is exactly one SQLite SELECT; "answer" is a one-sentence
  friendly preamble in the user's language (e.g. "Day la top 10 khach hang theo doanh
  thu:"). Do NOT put fabricated numbers in "answer" - the backend runs the SQL and fills
  the real results.
- needs_sql=false => "sql" is null; "answer_from_memory" answers using ONLY the provided
  conversation memory; "answer" is a short display version of that answer.
- Relative periods ("thang nay", "tuan nay", "hom nay", "nam nay") are computed against
  TODAY using date('now', ...). Generate correct relative-date SQL even if the data may
  not cover recent periods - the backend explains any empty result.
- For a drill-down follow-up ("họ đã mua gì", "sản phẩm của họ", "đơn hàng của khách
  đó", "what did they buy", "show their orders"), the pronoun/reference points at an
  entity in the CONVERSATION MEMORY's previous result (use the TOP row if there are
  several). Classify it DRILL_DOWN_PREVIOUS_RESULT, set needs_sql=true, and generate SQL
  filtering by that entity's id from memory. Do NOT answer INSUFFICIENT_CONTEXT when the
  previous result already contains a usable entity to drill into.
- If the question cannot be answered from memory or the provided schema, use
  INSUFFICIENT_CONTEXT with needs_sql=false.

Return VALID JSON ONLY. No markdown, no code fences, no text before or after the JSON."""


def build_system_prompt() -> str:
    return _SYSTEM_PROMPT


def build_user_prompt(
    llm_skill_context: str,
    *,
    today: str | None = None,
    data_min: str | None = None,
    data_max: str | None = None,
) -> str:
    today = today or date.today().isoformat()
    data_min = data_min or config.DATA_MIN_DATE
    data_max = data_max or config.DATA_MAX_DATE
    return (
        f"TODAY (real system date): {today}\n"
        f"DATA AVAILABLE FROM {data_min} TO {data_max} "
        f"(queries about periods outside this range return no rows).\n\n"
        f"{llm_skill_context}\n\n"
        f"Return JSON with EXACTLY this shape (no extra keys, no markdown):\n{_JSON_SHAPE}"
    )


def build_repair_user_prompt(previous_user_prompt: str, bad_sql: str, error: str) -> str:
    """Second-chance prompt: the prior SQL failed validation/execution."""
    return (
        f"{previous_user_prompt}\n\n"
        f"The SQL you previously returned was rejected. Fix it and return the SAME JSON "
        f"shape again.\n"
        f"Rejected SQL:\n{bad_sql}\n\n"
        f"Problem:\n{error}\n\n"
        f"Return corrected VALID JSON ONLY (SQLite SELECT, khong dau identifiers, a LIMIT)."
    )
