# SQLNEW Production System Plan v2

**Scope:** One integrated, production-grade conversational SQL + in-depth analytics chat application over the Vietnamese FMCG sales database, with a live-editable knowledge base and optional web research via SearxNG (native tool-calling).

**Status baseline:** Phases 1–8 (the `NORMAL_SQL` conversational pipeline) are implemented and working. This plan keeps that foundation untouched and specifies everything above it. Implementation order, per-phase file lists, and done-criteria live in `phased.md` — this document is the architecture and the contracts.

**Two invariants (stated once, enforced everywhere):**

```text
1. Every phase leaves the app usable.
2. Every LLM boundary has a deterministic fallback.
```

---

## 0. Goal and Product Definition

One sentence:

```text
A single-user, local-first Vietnamese sales assistant that answers direct database
questions in one validated SQL call, investigates business questions with a
multi-query evidence pipeline, enriches analysis with optional web market context,
renders reports with tables and charts, and lets the owner edit ALL of its
knowledge (schema, metrics, playbooks, caveats, chart rules) at any time through
the UI with immediate effect.
```

Capability list:

```text
- 4 runtime modes: NORMAL_SQL, ANALYTIC_MODE, ANALYTIC_FROM_PREVIOUS_RESULT, ANALYTIC_FOLLOWUP
- Live-editable knowledge base: every entry type editable in the UI, effective on the next question, no restart
- Evidence-backed analytic reports: multiple validated SQL tasks -> profiled evidence -> tables + charts -> explanation -> improvement advice -> caveats
- Optional web research: competitor / market / industry context fetched via SearxNG through native function-calling, cited separately from database facts
- Review memory: every analysis is stored and answerable afterward ("show the SQL", "why did you say X", "continue by route")
- Production hardening: structured logging, health checks, graceful degradation, tests, golden evaluation, one-command startup
```

Deployment target (decided): **single-user, hardened**. No auth, no multi-tenant, no Docker, no job queue. One FastAPI process on the owner's Windows machine, LLM on llama.cpp at `http://192.168.0.5:30187`, embedder on the local GPU.

Language (decided): **Vietnamese-first UI**. All labels, headings, progress steps, and error messages in Vietnamese (centralized in `frontend/src/i18n.ts`); the bot mirrors the user's language in answers.

---

## 1. As-Built Foundation (Phases 1–8, kept)

This section is descriptive. Details and done-criteria live in `phased.md` §1.

### 1.1 Module map

| Path | Responsibility | Status |
|---|---|---|
| `backend/store/` | knowledge.db schema, entry models, CRUD repository | ✅ |
| `backend/knowledge/` | KnowledgeService (save→embed orchestration), skill.md builder, embedding text, seeds, business meta | ✅ |
| `backend/ingestion/` | schema loader, embedding_docs.jsonl export | ✅ |
| `backend/embeddings/` | Qwen3-Embedding-4B (2560-dim, 4-bit NF4, CUDA), numpy IndexStore (vectors.npy + meta.json) | ✅ |
| `backend/retrieval/` | query expansion, bucketed vector search, value pinning, table resolver, FK join expander, compact skill context | ✅ |
| `backend/memory/` | conversations.db, Turn model, intent classifier (7 intents), retrieval planner, result summarizer + entity extraction | ✅ |
| `backend/llm/` | OpenAI-compatible client (streaming, JSON mode with fallback, never raises), prompt builder, defensive response parser | ✅ |
| `backend/validation/` + `backend/execution/` | 6-layer SQL validator, read-only query runner (mode=ro, 10s progress-handler timeout, row caps) | ✅ |
| `backend/api/` | app.py, chat.py (SSE pipeline), conversations.py, entries.py, knowledge.py, retrieve.py, state.py (singletons) | ✅ |
| `frontend/src/` | 5 tabs: Chat (SSE stepper, ResultTable, BarChart), Entries editor, skill.md preview, RetrievalTester, ChatPlanTester | ✅ |

### 1.2 Data stores

| Store | Path | Content |
|---|---|---|
| Business DB (read-only) | `data/sales.db` | FMCG sales data, window `2024-01-01` → `2025-06-24` |
| Knowledge source of truth | `skills/sales/knowledge.db` | entries: `table`, `column`, `metric`, `join_path`, `value`, `rule` |
| Rendered views | `skills/sales/skill.md`, `skills/sales/embedding_docs.jsonl` | human-readable + embedding corpus |
| Vector index | `skills/sales/index/` (`vectors.npy`, `meta.json`) | live numpy index, L2-normalized |
| Conversation memory | `skills/sales/conversations.db` | conversations + turns + full LLM I/O logs |

### 1.3 Pinned business facts

```text
Dialect: SQLite (read-only)
Revenue: doanh_thu = SUM(chi_tiet_don_hang_ban.thanh_tien)
Header-line join: don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id
Status filter: don_hang_ban.trang_thai = 'NORMAL'
Date column: don_hang_ban.ngay_dat_hang
```

### 1.4 The 7 normal-SQL intents (kept)

```text
NEW_QUERY, REFINE_PREVIOUS_QUERY, ASK_ABOUT_PREVIOUS_SQL, ASK_ABOUT_PREVIOUS_RESULT,
DRILL_DOWN_PREVIOUS_RESULT, EXPLAIN_PREVIOUS_RESULT, INSUFFICIENT_CONTEXT
```

### 1.5 Kept principle

```text
The LLM proposes. The backend validates, executes, profiles, visualizes, and remembers.
```

---

## 2. Target Architecture Overview

```text
User message (/api/chat, /api/chat/stream)
↓
ensure_fresh()  ← knowledge base version check (§12)
↓
mode_detector (heuristic, no LLM)
│
├── NORMAL_SQL ────────────── as-built one-call pipeline (§6)
│
├── ANALYTIC_MODE ─────────── analytic controller (§7)
│       context → plan (LLM 1) → SQL tasks → profile → [web research]
│       → charts → advisor → write (LLM 2) → persist review
│
├── ANALYTIC_FROM_PREVIOUS_RESULT ── review seed from last SQL turn (§8) → controller
│
└── ANALYTIC_FOLLOWUP ─────── answer from stored review evidence (§9)
```

### 2.1 New package map

```text
backend/
├── analysis/                    (new)
│   ├── __init__.py
│   ├── models.py                AnalyticContext, ReviewSeed, ReviewPlan, EvidenceItem dataclasses
│   ├── mode_detector.py         4-mode heuristic router
│   ├── review_target_resolver.py  previous-result → ReviewSeed
│   ├── context_builder.py       AnalyticContext (schema + playbooks + dimensions + caveats)
│   ├── planner.py               review planner LLM call + validation ladder
│   ├── fallback_packs.py        deterministic task packs from playbook steps
│   ├── task_runner.py           multi-SQL execution via existing validator/runner
│   ├── profiler.py              rows → profiles (deltas, contributors, trends)
│   ├── evidence.py              evidence item construction (source_type sql|web)
│   ├── chart_planner.py         deterministic chart specs from chart_rule entries
│   ├── advisor.py               deterministic interpretation + improvement bullets
│   ├── writer.py                final report LLM call + skeleton fallback
│   ├── followup.py              ANALYTIC_FOLLOWUP answerer
│   ├── research.py              web research stage (single-shot SearxNG tool-call planner)
│   ├── review_store.py          reviews/evidence persistence in conversations.db
│   └── controller.py            stage orchestration, budgets, SSE emission
│
├── tools/                       (new)
│   ├── __init__.py
│   ├── search_internet.py       SearxNG call (httpx); model-string + structured results
│   ├── registry.py              OpenAI tools schema (one tool) + dispatch + strict arg validation
│   └── cache.py                 research_cache (24h TTL, keyed by normalized query)
│
├── common/logging.py            (new) structured logging setup
├── knowledge/entry_validator.py (new) save-time entry validation
├── api/analysis.py              (new) /api/analysis/plan tester, /api/reviews
├── api/health.py                (new) deep /api/health
└── tests/                       (new) pytest suite

scripts/                         (new) start.ps1, dev.ps1, smoke.ps1, golden_eval.py
golden/                          (new) golden_questions.jsonl
```

### 2.2 Design principles

```text
Deterministic-first     Chart choice, mode detection, profiling, advice mapping,
                        evidence provenance are code, not LLM output.
Fallback-everywhere     2 required LLM calls per analysis; each has a deterministic
                        degradation path (§26). LLM absence degrades quality, never availability.
Offline-first           Web research is an optional enrichment stage. SearxNG down
                        (or search disabled) = full report still ships, with a notice.
One knowledge system    Analytic knowledge is knowledge.db entries — same editor,
                        same embedding flow, same hot-reload (§10, §12).
Single-user honesty     No auth, no queue, no async rewrite. Sync pipeline in
                        FastAPI's threadpool is the documented trade-off.
```

---

## 3. Runtime Modes and Mode Detector

### 3.1 Modes

```python
NORMAL_SQL = "NORMAL_SQL"
ANALYTIC_MODE = "ANALYTIC_MODE"
ANALYTIC_FROM_PREVIOUS_RESULT = "ANALYTIC_FROM_PREVIOUS_RESULT"
ANALYTIC_FOLLOWUP = "ANALYTIC_FOLLOWUP"
```

| Mode | Meaning | Example |
|---|---|---|
| `NORMAL_SQL` | Direct answer, one SQL if needed | `Top 10 khách hàng theo doanh thu tháng 3/2025` |
| `ANALYTIC_MODE` | New multi-query investigation | `Vì sao doanh thu tháng 3 giảm?` |
| `ANALYTIC_FROM_PREVIOUS_RESULT` | Investigation scoped to an entity from the last SQL result | `Phân tích sâu khách hàng top 1` |
| `ANALYTIC_FOLLOWUP` | Question about the last analytic review | `Cho xem SQL đã dùng`, `vì sao miền Trung giảm mạnh nhất?` |

### 3.2 Detector is heuristic-only (no LLM call)

The detector runs on the normalized (khong-dau) message, same normalization as the
existing intent classifier in `backend/memory/intent_classifier.py`.

Analytic triggers (VN/EN, non-exhaustive — final list is a module constant with tests):

```text
phan tich, phan tich sau, danh gia, nguyen nhan, vi sao, tai sao, ly do,
tim nguyen nhan, hieu suat, cai thien, de xuat, khuyen nghi, chuyen sau,
analyze, analysis, review, in-depth, insight, why, reason, root cause,
diagnose, investigate, what caused, how to improve, recommendation
```

Previous-result references:

```text
cai nay, cai do, dong nay, dong dau, dong 1, top 1, khach hang nay, cong ty nay,
san pham nay, ket qua tren, bang nay, trong do,
this, that one, first one, top one, row 1, top customer, highest, lowest
```

Analytic follow-up markers:

```text
bang chung, kiem tra gi, cau sql nao, hien sql, phan tich tiep, di sau hon,
ve bieu do, hien bang, vi sao ban noi,
show evidence, what did you check, which SQL, show SQL, continue, drill down
```

### 3.3 Detector logic

```python
def detect_mode(user_message: str, memory) -> str:
    text = normalize(user_message)
    analytic = contains_any(text, ANALYTIC_TRIGGERS)
    refs_prev = contains_any(text, PREVIOUS_RESULT_REFERENCES)
    followup = contains_any(text, REVIEW_FOLLOWUP_MARKERS)

    last_review = memory.last_review()          # most recent review in this conversation
    last_sql_turn = memory.last_sql_turn()      # most recent turn with result_entities

    # A recent review owns referential/follow-up questions.
    if last_review is not None and (followup or (refs_prev and last_review.is_latest_artifact)):
        return ANALYTIC_FOLLOWUP

    if analytic and refs_prev and last_sql_turn is not None:
        return ANALYTIC_FROM_PREVIOUS_RESULT

    if analytic:
        return ANALYTIC_MODE

    return NORMAL_SQL
```

### 3.4 Downgrade rule (LLM-grade precision without an extra call)

Heuristics over-trigger ("phân tích" can prefix a simple lookup). The review planner
(§13) may return `"mode_downgrade": "NORMAL_SQL"` when the question is actually a
one-query lookup; the controller then re-routes the turn into the normal pipeline.
Cost: zero extra calls — the planner call replaces the normal intent call for that turn.

### 3.5 Config gate

`ANALYTIC_ENABLED` (default `1` once shipped; `0` during rollout — detected-but-disabled
turns run the normal pipeline and log the would-be mode).

---

## 4. Folder Structure (target)

```text
SQLNEW/
├── backend/
│   ├── app.py                       logging init, routers, static dist mount
│   ├── config.py                    all keys (§24)
│   ├── requirements.txt             (unchanged — httpx already present)
│   ├── common/                      schema_def.py, vn_text.py, logging.py (new)
│   ├── store/                       db.py (+ meta, entry_history), models.py (+ new types), repository.py
│   ├── knowledge/                   service.py, skill_builder.py, embedding_text.py,
│   │                                seed.py (+ seed_analysis), entry_validator.py (new), business_meta.py
│   ├── ingestion/                   schema_loader.py, export_docs.py
│   ├── embeddings/                  embedder.py, index_store.py
│   ├── retrieval/                   context_builder.py (+ ensure_fresh), vector_retriever.py (+ buckets),
│   │                                query_expander.py, value_matcher.py, table_resolver.py,
│   │                                join_expander.py, rules_provider.py, skill_context.py, models.py
│   ├── memory/                      db.py, models.py (+ Turn.review_id), store.py, memory_builder.py,
│   │                                intent_classifier.py, retrieval_planner.py, result_summarizer.py
│   ├── llm/                         client.py (+ per-call params, tools passthrough),
│   │                                prompt_builder.py, review_prompts.py (new), response_parser.py
│   ├── validation/sql_validator.py  unchanged
│   ├── execution/query_runner.py    unchanged
│   ├── analysis/                    (new, §2.1)
│   ├── tools/                       (new, §2.1)
│   ├── api/                         chat.py, conversations.py, entries.py (+ history/restore),
│   │                                knowledge.py, retrieve.py, analysis.py (new), health.py (new), state.py
│   └── tests/                       (new)
│
├── frontend/src/
│   ├── App.tsx, api.ts, types.ts, styles.css, i18n.ts (new)
│   └── components/
│       ├── Chat.tsx, ResultTable.tsx (+ pagination), BarChart.tsx (kept),
│       ├── EntryForm.tsx (+ new types, validation errors), EntryList.tsx, StatusBar.tsx (+ health lights),
│       ├── SkillMdPreview.tsx, RetrievalTester.tsx, ChatPlanTester.tsx,
│       ├── AnalyticReport.tsx (new), EvidenceTable.tsx (new), ChartRenderer.tsx (new),
│       ├── ReviewProgress.tsx (new), SourcesList.tsx (new), ErrorBoundary.tsx (new)
│
├── data/sales.db
├── skills/sales/                    knowledge.db, skill.md, embedding_docs.jsonl,
│                                    schema_snapshot.json, metadata.json, conversations.db, index/
├── scripts/                         start.ps1, dev.ps1, smoke.ps1, golden_eval.py (new)
└── golden/golden_questions.jsonl    (new)
```

---

## 5. Unified Main Handler

Same endpoints (`POST /api/chat`, `POST /api/chat/stream`) — no new chat endpoint.
The whole turn runs synchronously in FastAPI's threadpool (documented single-user trade-off).

```python
def handle_user_message(req, services):
    services.retrieval.ensure_fresh()                     # §12 — KB version check, both pipelines

    memory = conversation_store.load_recent(req.conversation_id)
    mode = mode_detector.detect_mode(req.message, memory)
    emit_sse("step", step="mode", status="done", mode=mode)

    if mode == NORMAL_SQL or not config.ANALYTIC_ENABLED:
        return run_normal_sql_pipeline(req, memory)       # as-built, §6

    if mode == ANALYTIC_FROM_PREVIOUS_RESULT:
        seed = review_target_resolver.resolve(req.message, memory)   # §8
        if not seed.ok:
            return safe_insufficient_context_answer(seed.reason)
        return controller.run_review(req, memory, seed=seed)

    if mode == ANALYTIC_FOLLOWUP:
        return followup.handle(req, memory)               # §9

    return controller.run_review(req, memory, seed=None)  # ANALYTIC_MODE, §7
```

`mode_downgrade` from the planner (§3.4) re-enters `run_normal_sql_pipeline` with the
already-loaded memory.

---

## 6. Pipeline 1 — NORMAL_SQL (kept as-built)

### 6.1 Flow (unchanged)

```text
User message
↓ load memory (conversations.db, MEMORY_RECENT_TURNS window)
↓ heuristic intent classifier (7 intents)
↓ retrieval plan (skip retrieval for memory-only intents; pin tables for refine/drill-down)
↓ retrieve → compact SQLite skill context
↓ ONE streamed LLM call → JSON decision
↓ needs_sql: validate (6 layers) → self-repair once → execute read-only → summarize → extract entities → save turn
↓ else: answer from memory → save turn
```

### 6.2 LLM JSON contract (unchanged, verbatim carry-over)

```json
{
  "intent": "NEW_QUERY | REFINE_PREVIOUS_QUERY | ASK_ABOUT_PREVIOUS_SQL | ASK_ABOUT_PREVIOUS_RESULT | DRILL_DOWN_PREVIOUS_RESULT | EXPLAIN_PREVIOUS_RESULT | INSUFFICIENT_CONTEXT",
  "needs_sql": true,
  "standalone_question": "resolved standalone database question, or null",
  "answer": "short friendly reply in the user's language",
  "answer_from_memory": "full answer when needs_sql is false, otherwise null",
  "sql": "one SQLite SELECT query when needs_sql is true, otherwise null",
  "used_previous_context": false,
  "memory_update": {
    "selected_tables": [], "selected_columns": [], "selected_metrics": [],
    "selected_filters": [], "referenced_previous_entities": []
  }
}
```

### 6.3 What normal turns store for analytic reuse (already implemented)

`Turn` already persists: `user_question`, `standalone_question`, `intent`, `generated_sql`,
`selected_tables/columns/metrics/filters`, `result_columns`, `result_preview` (5 rows),
`result_entities` (`type`, `id_column`, `id_value`, `name_column`, `name_value` — extracted by
pairing `*_id` with `ten_*` columns), `result_summary`, `display_rows` (60), full LLM I/O logs.
This is exactly what the review seed (§8) consumes.

### 6.4 Deltas in v2

```text
- Per-call LLM params: SQL turns pinned to temperature LLM_TEMPERATURE_SQL=0,
  max_tokens LLM_MAX_TOKENS_SQL=1200 (resolves the uncommitted client.py drift —
  the analytic writer needs 0.4/4000, SQL generation does not; client.chat()
  already accepts per-call overrides).
- Turn gains nullable review_id (link to §20).
- Structured log fields: request_id, conversation_id, turn_id on every record.
```

---

## 7. Pipeline 2 — ANALYTIC_MODE

### 7.1 Stage table

| # | Stage | Module | LLM? | SSE step | On failure |
|---|---|---|---|---|---|
| 1 | Analytic context | `analysis/context_builder.py` | no | `retrieve` | error answer (retrieval is required) |
| 2 | Review plan | `analysis/planner.py` | **call 1** | `plan` | retry once → fallback pack (§13.4) |
| 3 | SQL tasks | `analysis/task_runner.py` | repair only | `task` (per task) | failed task → failed-evidence, continue |
| 4 | Profiling | `analysis/profiler.py` | no | `profile` | per-task warnings, continue |
| 5 | Web research (LLM call 2 of 3, optional) | `analysis/research.py` | 1 call (opt) | `research` | skip + notice, continue (§16) |
| 6 | Chart specs | `analysis/chart_planner.py` | no | `charts` | no chart, tables still ship |
| 7 | Advice mapping | `analysis/advisor.py` | no | — | empty advice section |
| 8 | Report | `analysis/writer.py` | **call 2** (streamed) | `write` + `token` | deterministic skeleton (§19.4) |
| 9 | Persist | `analysis/review_store.py` | no | `save` | log error, still return report |

### 7.2 Report contract

Rendered headings follow the user's language; Vietnamese set (default):

```text
## Tóm tắt              (Executive Summary)
## Diễn giải            (Explanation — evidence-only)
## Bằng chứng           (Evidence Tables)
## Biểu đồ              (Charts)
## Nguyên nhân chính    (Main Drivers)
## Bối cảnh thị trường  (Market Context — ONLY when web evidence exists, §16)
## Đề xuất cải thiện    (How to Improve)
## Lưu ý                (Caveats)
## Phân tích tiếp theo  (Recommended Next Analysis)
```

### 7.3 Call and wall-clock budgets

```text
Required LLM calls per review:    2   without web research (planner, writer)
                                  3   with web research (planner, web-search planner §16.4, writer)
Web-search planner:               ONE call emitting ≤ SEARCH_MAX_CALLS_PER_REVIEW search tool
                                  calls; backend executes them WITHOUT re-invoking the model;
                                  SearxNG HTTP calls are not LLM calls
Wall clock:                       ANALYTIC_TOTAL_BUDGET_SEC = 120
Budget pressure order:            skip research first, then truncate remaining SQL tasks
                                  (each skip recorded as a caveat line in the report)
```

---

## 8. Pipeline 3 — ANALYTIC_FROM_PREVIOUS_RESULT

### 8.1 Flow

```text
Last SQL turn memory (§6.3) → review_target_resolver.py → ReviewSeed → controller.run_review(seed)
```

### 8.2 Resolver responsibilities

```text
- Load last turn with result_entities
- Resolve "top 1 / dòng 3 / cái này / first one / row 2" by rank position in result_preview
- Match explicit names against result_entities name_value (normalized fuzzy match)
- If exactly one entity exists, referential mention resolves to it
- Refuse safely (ok=False + reason) when ambiguous or no previous result exists
```

### 8.3 ReviewSeed shape (fields sourced from the real `Turn` model)

```json
{
  "ok": true,
  "source_turn_id": "turn_001",
  "source_question": "Top 10 khách hàng theo doanh thu tháng 3/2025",
  "source_sql": "SELECT ...",
  "target_entity": {
    "type": "customer", "rank": 1,
    "id_column": "khach_hang_id", "id_value": "KH_030",
    "name_column": "ten_khach_hang", "name_value": "Cua hang 30"
  },
  "base_metrics": ["doanh_thu"],
  "base_filters": ["2025-03"],
  "base_tables": ["khach_hang", "don_hang_ban", "chi_tiet_don_hang_ban"],
  "base_fact": "KH_030 ranked #1 by doanh_thu in 2025-03."
}
```

The seed is serialized into the planner input so every generated task is scoped to
the target entity (WHERE `khach_hang_id = 'KH_030'` etc.).

---

## 9. Pipeline 4 — ANALYTIC_FOLLOWUP

Answer from the stored review — no new SQL by default.

```text
Load most recent review + its evidence items (titles + profiles + ≤5 rows each)
↓
ONE LLM call: "answer from this evidence only" →
  {"answer": "...", "needs_new_analysis": false, "suggested_question": null}
↓
Deterministic fallback (LLM fails): keyword-match the question against evidence
titles/columns → re-render the best-matching evidence table with a template sentence.
↓
Escalation: needs_new_analysis=true → answer ships with a suggestion chip that,
when clicked, submits suggested_question as a fresh ANALYTIC_MODE turn.
```

Special cases answered without any LLM call: "hiện SQL / show SQL" (render stored task
SQL list), "cho xem bằng chứng / show evidence" (render evidence tables), "vẽ lại biểu
đồ / show chart" (re-emit stored chart specs).

Follow-up window: the most recent review in the conversation. A later NORMAL_SQL turn
does not close it; a newer review replaces it.

---

## 10. Analytic Knowledge as knowledge.db Entry Types ⭐

### 10.1 Decision record

The old plan kept analytic knowledge in static `backend/analysis_knowledge/*.md` files.
**Rejected** — it would create a second knowledge system with:

```text
no editor UI          (requirement: easy-to-use editing)
no embed-on-save      (requirement: updates effective immediately)
no hot-reload         (files read at import time)
no audit/versioning   (§12.4 applies only to knowledge.db)
no id discipline      (entries have deterministic ids; files do not)
```

Instead, analytic knowledge becomes **new entry types in knowledge.db**, flowing through
the exact same pipeline as schema knowledge: Pydantic body model → derived id →
`embedding_text` → `content_hash` → upsert → embed → index upsert → skill.md render.

### 10.2 New entry types

Type registry changes in `backend/store/models.py`:

```text
ENTRY_TYPES      += playbook, caveat, dimension, chart_rule
EMBEDDABLE_TYPES += playbook, caveat, dimension          (chart_rule is policy: NOT embedded,
                                                          loaded fresh via kb_version like rules)
```

Body schemas (Pydantic, `extra="forbid"`, matching existing style):

```python
class DiagnosticStep(BaseModel):
    title: str                      # "So sánh doanh thu kỳ này với kỳ trước"
    purpose: str
    metric: str = ""                # metric entry name, e.g. "doanh_thu"
    dimension: str = ""             # dimension entry slug, e.g. "category"
    expected_shape: Literal["kpi", "by_dimension", "trend", "top_n"] = "kpi"
    sql_hint: str = ""              # optional template with {date_from} {date_to} {compare_from}
                                    # {compare_to} {dimension_column} {entity_filter} placeholders

class PlaybookBody(BaseModel):      # id: playbook:{playbook}
    playbook: str                   # slug, e.g. "revenue_drop"
    kind: Literal["diagnostic", "comparison", "ranking", "overview"]
    aliases: list[str] = []         # "vì sao doanh thu giảm", "sales dropped", ...
    use_when: str                   # embedded — drives retrieval
    main_metrics: list[str] = []
    required_comparison: Literal["previous_period", "same_period_last_year", "none"] = "previous_period"
    diagnostic_steps: list[DiagnosticStep]
    interpretation_rules: list[str] = []   # "IF active_customers drops more than AOV THEN ..."
    improvement_rules: list[str] = []      # "IF active customers dropped: reactivate lost customers ..."
    caveats: list[str] = []
    notes: str = ""

class CaveatBody(BaseModel):        # id: caveat:{slug(title)}
    title: str
    content: str                    # "Dữ liệu chỉ có đến 2025-06-24 ..."
    applies_to_metrics: list[str] = []
    applies_to_tables: list[str] = []
    severity: Literal["info", "warning"] = "info"
    aliases: list[str] = []

class DimensionBody(BaseModel):     # id: dimension:{dimension}
    dimension: str                  # slug, e.g. "category"
    aliases: list[str] = []         # "ngành hàng", "nhóm sản phẩm", "category"
    table: str                      # dimension table
    column: str                     # label column
    id_column: str = ""
    join_requirement: str = ""      # join path name needed to reach fact tables
    drill_down_to: list[str] = []   # next dimensions ("product", "customer")
    use_when: str = ""

class ChartRuleBody(BaseModel):     # id: chart_rule:{shape}
    shape: Literal["kpi_comparison", "trend", "top_n", "composition", "raw"]
    chart_type: Literal["grouped_bar", "line", "horizontal_bar", "stacked_bar", "none"]
    max_categories: int = 12
    min_rows: int = 2
    notes: str = ""
```

Backward-compatible **metric extensions** (optional fields on the existing `MetricBody`;
all default so existing entries stay valid):

```python
direction: Literal["higher_is_better", "lower_is_better", "neutral"] = "higher_is_better"
decomposition: list[str] = []        # ["order_count", "active_customer_count", "average_order_value"]
default_comparisons: list[str] = []  # ["previous_period"]
default_dimensions: list[str] = []   # ["category", "customer", "region"]
interpretation_down: str = ""        # "Giảm thường do mất khách hoặc giảm giá trị đơn ..."
interpretation_up: str = ""
```

### 10.3 Rendering

- `knowledge/skill_builder.py` gains sections: `## Analysis Playbooks`, `## Dimensions`,
  `## Analysis Caveats`, `## Chart Rules`.
- `knowledge/embedding_text.py` rules: playbook embeds `use_when` + aliases + step titles +
  `main_metrics`; dimension embeds aliases + `table.column` + `use_when`; caveat embeds
  title + content + `applies_to_*`.
- `ingestion/export_docs.py` picks the new embeddable types up automatically (it iterates
  `EMBEDDABLE_TYPES`).

### 10.4 Seed content (`knowledge/seed.py::seed_analysis`)

```text
4 playbooks:   revenue_drop (root cause — ports the old §9.4/§9.5 diagnostic content),
               top_customer_analysis, product_category_performance, region_channel_comparison
~8 dimensions: category, product, customer, company, distributor, route, city/region, month
~6 caveats:    data window 2024-01-01→2025-06-24; trang_thai='NORMAL' filter; net revenue
               definition; no promotion/inventory/visit-log data; VALUE sampling limits;
               incomplete current period
5 chart_rules: one per shape (kpi_comparison→grouped_bar, trend→line, top_n→horizontal_bar,
               composition→stacked_bar, raw→none)
metric ext.:   doanh_thu (decomposition + interpretations), san_luong, order_count aliases
```

Seeds are ordinary entries: the owner edits or replaces every one of them in the UI.

---

## 11. Retrieval Upgrade + Analytic Context Builder

### 11.1 New retrieval buckets

Same index, same bucketed search (`type` is already the bucket key in
`retrieval/vector_retriever.py`):

```text
RETRIEVAL_TOPK_PLAYBOOK  = 2
RETRIEVAL_TOPK_CAVEAT    = 3
RETRIEVAL_TOPK_DIMENSION = 4
```

### 11.2 AnalyticContext (`analysis/context_builder.py`)

Reuses the shared `RetrievalService` instance (schema retrieval identical to normal mode),
then adds analytic buckets:

```json
{
  "schema_context": "<ResolvedContext: tables, columns, joins, metrics, values, rules>",
  "playbooks": [], "dimensions": [], "caveats": [],
  "metric_analysis": [],            // analytic extensions of retrieved metrics
  "chart_rules": [],                // loaded fresh (non-embedded)
  "review_seed": null,              // §8.3 when present
  "recent_turn_summaries": [],      // compact memory window
  "data_window": {"min": "2024-01-01", "max": "2025-06-24"}
}
```

---

## 12. KB Live-Update Architecture ⭐

Requirement: **every knowledge edit is effective on the next question, no restart.**

### 12.1 The gap being fixed

Today `RetrievalService` (singleton, `backend/api/state.py`) builds derived caches at
construction: `norm_map`, `value_index`, `global_rules`, `table_defs`, `metric_defs`,
`column_defs`, `join_path_defs`. Entry saves update knowledge.db and the numpy index but
NOT these caches; skill.md and embedding_docs.jsonl only rebuild via manual `POST /rebuild/*`.

Note: the numpy `IndexStore` object is already **shared in memory** between
`KnowledgeService` and `RetrievalService` — vectors are never stale. Only the derived
dicts and rendered files are.

### 12.2 Mechanism: monotonic `kb_version` + per-request `ensure_fresh()`

```text
WRITE PATH (KnowledgeService.save / delete, under one process-wide lock):
  1. validate entry            (§12.3; strict errors reject the save)
  2. write entry_history row   (§12.4)
  3. upsert into knowledge.db
  4. embed                     (embedder up → encode + index.upsert + index.save;
                                embedder down → embed_status='pending', save still succeeds)
  5. render skill.md + export embedding_docs.jsonl        (KB_AUTO_RENDER=1)
  6. bump meta.kb_version      (INTEGER, monotonic, in knowledge.db)

READ PATH (top of every RetrievalService.retrieve / chat turn):
  ensure_fresh():
    one SQLite read of meta.kb_version        (~microseconds)
    if version != cached version:
        rebuild the derived dicts from the repository   (<100 ms at current entry count)
        reload chart_rules / global_rules
```

Event-driven invalidation was considered and rejected: same process, one writer,
version-check-per-request is simpler and cannot miss.

### 12.3 Save-time validation (`knowledge/entry_validator.py`)

```text
metric      formula must sqlglot-parse (sqlite dialect); referenced tables/columns must
            exist in schema_snapshot.json
join_path   join expressions parse; both tables exist
playbook    every sql_hint parses after placeholder substitution; referenced metric /
            dimension entries exist
dimension   table.column exists; join_requirement (if set) names an existing join_path
value       source table.column exists
```

`KB_VALIDATE_ON_SAVE = strict | warn | off` (default `strict`). Errors reject the save
with a field-level message the UI shows inline; warnings never block.

### 12.4 Audit and versioning

New table in knowledge.db:

```sql
CREATE TABLE entry_history (
  history_id  INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id    TEXT NOT NULL,
  action      TEXT NOT NULL,          -- create | update | delete | restore
  old_body    TEXT,                   -- JSON, null on create
  new_body    TEXT,                   -- JSON, null on delete
  changed_at  TEXT NOT NULL
);
```

Endpoints: `GET /api/entries/{id}/history`, `POST /api/entries/{id}/restore`
(restore = save(old_body) → new history row; nothing is ever silently lost).
Soft-disable remains the existing `enabled` flag; hard delete writes a history row first.

### 12.5 Embedder-down resilience

Saves never fail because the GPU/embedder is unavailable: the entry persists with
`embed_status='pending'`; startup and `POST /api/entries/{id}/reembed` (and a
"re-embed pending" button in StatusBar) retry pending rows. Retrieval simply won't
surface a pending entry semantically until embedded (exact value pinning still works).

### 12.6 Value freshness (secondary)

`VALUE_SAMPLE_LIMIT` raised (30 → 200) and a new `POST /api/knowledge/sync-values`
re-samples distinct values from sales.db on demand — no reseed of curated entries.

---

## 13. Review Planner

### 13.1 Input

Compactly serialized `AnalyticContext` (§11.2) + the user question + the data window +
`ReviewSeed` when present. Prompt lives in
`backend/llm/review_prompts.py`. Temperature 0 (`LLM_TEMPERATURE_SQL`).

### 13.2 Output contract (JSON only)

```json
{
  "analysis_title": "Phân tích doanh thu giảm tháng 3/2025",
  "mode_downgrade": null,
  "playbook_used": "playbook:revenue_drop",
  "date_range": {
    "from": "2025-03-01", "to": "2025-03-31",
    "compare_from": "2025-02-01", "compare_to": "2025-02-28"
  },
  "tasks": [
    {
      "task_id": "t1",
      "title": "Doanh thu kỳ này so với kỳ trước",
      "purpose": "Confirm the revenue change.",
      "expected_shape": "kpi",
      "sql": "SELECT ..."
    }
  ]
}
```

The planner no longer emits research tasks. Web research is a separate, self-directed stage:
when `SEARCH_ENABLED=1` the research stage (§16.4) makes its own single web-search-planner call
over the SQL findings and decides its own queries.

### 13.3 Validation ladder

```text
1. Defensive JSON parse (existing response_parser patterns: fences, trailing text)
2. Structural check: 2–6 tasks, each with task_id/title/purpose/expected_shape/sql
3. EVERY task.sql through the full 6-layer validator (schema allowlist included)
4. Invalid tasks dropped; duplicates (normalized SQL) dropped
5. If < 2 tasks survive → ONE retry with the validation errors appended to the prompt
6. If still < 2 → deterministic fallback pack (§13.4)
```

### 13.4 Fallback pack (`analysis/fallback_packs.py`)

Instantiate the top retrieved playbook's `diagnostic_steps` deterministically:

```text
- dates: resolved from the question (existing VN date parsing) or the ReviewSeed filters,
  else the last full month in the data window vs the month before
- {dimension_column}: from the referenced dimension entry's table.column
- {entity_filter}: from the ReviewSeed target entity, when present
- sql_hint present → substitute placeholders; absent → build from step metric's formula
  (metric entry) grouped by the step dimension
- every generated SQL still passes the full validator; steps that fail are skipped
```

The fallback pack is also the unit-test fixture path — it must produce a complete,
correct review for the seeded playbooks with the LLM disabled.

---

## 14. Query Task Runner

`analysis/task_runner.py` — reuses `validation/sql_validator.py` and
`execution/query_runner.py` **unchanged**.

```text
Per task:  ≤1 self-repair round (ANALYTIC_MAX_REPAIRS_PER_TASK=1),
           QUERY_TIMEOUT_SEC=10, MAX rows fetched per existing runner caps
Sequence:  sequential execution (single user, small DB — parallelism not justified)
Failures:  recorded as failed-evidence {task_id, status:"failed", reason}; the review
           continues and the report names the diagnostic step that could not run
Stops:     total review budget exceeded → remaining tasks skipped with a notice
Caps:      ≤ ANALYTIC_MAX_TASKS=6 tasks per review
```

Output per task: `{task_id, title, purpose, expected_shape, sql, status, rows, error}`.

---

## 15. Result Profiler + Evidence Store

### 15.1 Profiles by expected shape (`analysis/profiler.py`, pure functions)

```text
kpi           current, previous, absolute_change, pct_change
by_dimension  per-row change, top positive/negative contributors, contribution share,
              top-3 concentration share, biggest mover
trend         direction (up/down/flat), best/worst period, last-vs-first change
top_n         ranking, leader share, gap to #2
warnings      empty result, divide-by-zero, incomplete current period (data window),
              all-null columns
```

```python
def pct_change(current, previous):
    if previous in (0, None): return None
    return (current - previous) / previous * 100
```

### 15.2 Evidence item schema

```json
{
  "evidence_id": "ev1",
  "review_id": "rv_001",
  "task_id": "t1",
  "kind": "kpi_comparison",
  "source_type": "sql",
  "title": "Doanh thu kỳ này so với kỳ trước",
  "columns": ["ky", "doanh_thu"],
  "rows": [["2025-03", 820000000], ["2025-02", 1040000000]],
  "profile": {
    "current": 820000000, "previous": 1040000000,
    "absolute_change": -220000000, "pct_change": -21.15,
    "top_contributors": [], "trend": "down", "warnings": []
  },
  "web": null,
  "chart_id": "c1",
  "created_at": "2026-07-02T10:00:00"
}
```

Web variant (§16): `source_type:"web"`, `rows`/`profile` null,
`web: {query, url, source_title, snippet, published_at, retrieved_at, tool_name}`.

Row cap: `ANALYTIC_EVIDENCE_MAX_ROWS=20` stored per item (full result never persisted).

### 15.3 Storage (conversations.db — DDL in §20)

`source_type` is a hard column: the writer prompt and the frontend both distinguish
database facts from web claims structurally, never by parsing text.

---

## 16. Web Research via SearxNG (native tool-calling) ⭐

### 16.1 Topology

```text
llama.cpp @ http://192.168.0.5:30187  ← serves the model (Qwen 3.5 9B), OpenAI-compatible,
                                        started with --jinja (Qwen tool-call templates)
SearxNG @ SEARXNG_URL                 ← self-hosted meta-search, JSON API (/search)
SQLNEW backend                        ← runs the search stage: hands the model ONE tool,
                                        executes every call against SearxNG, owns provenance
```

The model never calls SearxNG directly. The backend exposes exactly one function to the
model — `search_internet(query)` — and brokers every invocation, so every search is
logged, cached, argument-validated, and turned into attributable evidence. There is no MCP
host, no tool discovery, and no second research path: a single native tool-call planner, with
"skip and ship the offline report" as the only fallback.

### 16.2 The single tool (`backend/tools/search_internet.py`)

Ported from the reference impl, on **httpx** (matching `llm/client.py`, not `requests`),
reading config, using the project logger. Returns BOTH the model-facing string AND the
structured results used to build evidence.

```text
GET {SEARXNG_URL}/search?q=<query>&format=json&language=SEARCH_LANGUAGE, timeout=SEARCH_TIMEOUT_SEC
  URL normalized exactly as in the sample (append "/search" if missing)
  sort results by "score" desc, take top SEARCH_MAX_RESULTS
  each: title, content (truncated to SEARCH_MAX_SNIPPET_CHARS + "..."), url, publishedDate
  text    → "[i] {title} ({published})\nNội dung: {content}\nNguồn: {url}" joined by blank lines
  results → [{"title","url","snippet","published"}]   (structured — never re-parsed from text)
  zero results        → text="Không tìm thấy kết quả nào ..."; results=[]
  timeout             → text="Công cụ tìm kiếm không phản hồi (timeout). Tiếp tục ..."; results=[]
  any other exception → text="Công cụ tìm kiếm tạm thời không khả dụng: {e}"; results=[]
```

`search_internet` never raises (same discipline as `llm/client.py`): every failure maps to
a VN model-facing sentence and an empty `results` list. `SEARXNG_URL` falls back to the
legacy `DATAMIND_SEARXNG_URL` env var when unset (§24.2).

### 16.3 Modules (`backend/tools/`)

```text
backend/tools/search_internet.py  SearxNG call (httpx, SEARCH_* config, logger); returns
                                  SearchResult(text, results[]). Never raises.
backend/tools/registry.py         SEARCH_TOOLS_SCHEMA (the OpenAI `tools` list, one entry),
                                  name→callable dispatch, strict arg validation (name known;
                                  args parse as JSON; query non-empty str ≤ SEARCH_MAX_QUERY_CHARS=200).
backend/tools/cache.py            research_cache table (conversations.db, §20.1); key = normalized
                                  query; TTL SEARCH_CACHE_TTL_HOURS=24. Stores structured results;
                                  a cache hit skips the SearxNG HTTP call but still yields results for evidence.
```

The `tools` schema (fixed; planner/writer never touch it):

```json
[{
  "type": "function",
  "function": {
    "name": "search_internet",
    "description": "Tìm kiếm thông tin trên Internet (SearxNG). Chỉ dùng khi cần thông tin thị trường/đối thủ/ngành bên ngoài hệ thống nội bộ.",
    "parameters": {
      "type": "object",
      "properties": { "query": {"type": "string", "description": "Câu truy vấn tìm kiếm bằng tiếng Việt."} },
      "required": ["query"]
    }
  }
}]
```

### 16.4 The research stage — a single-shot web-search planner (`analysis/research.py`)

A dedicated stage (NOT the writer) makes **one** LLM call — the **2nd of the 3 LLM calls** a
web-enriched review makes (planner → *this* → writer): the "web search planner". It runs
**after** the SQL tasks execute and profile, so it is seeded with the real findings and its
queries are grounded in them. The model emits `search_internet` tool calls in a single
response; the backend executes them and hands the structured results to the writer. **The
research model is never re-invoked with the tool results** — there is no agentic loop. The
writer (Call 3) is the model that "continues processing" the search results.

```text
Preconditions: SEARCH_ENABLED=1. Stage 5 in §7.1 (optional, after profiling).
Single LLM call:
  system: "Bạn là trợ lý phân tích. Bạn CHỈ có công cụ search_internet để tra cứu bối cảnh
           thị trường/ngành/đối thủ bên ngoài. Hãy phát ra các lệnh gọi search_internet cần
           thiết trong MỘT lượt. Không bịa số."
  user:   compact analysis context (analysis_title, key SQL findings/deltas, data window,
           dimensions in play) + "Hãy đề xuất các truy vấn tra cứu bối cảnh thị trường liên quan."
  res = client.chat(system, user, tools=SEARCH_TOOLS_SCHEMA, tool_choice="auto",
                    temperature=LLM_TEMPERATURE_SQL, max_tokens=LLM_MAX_TOKENS_SQL)

Backend then, WITHOUT re-invoking the model:
  if res.error or not res.tool_calls:        # declined / LLM down → offline degradation (§16.6)
      web_context = empty ; return
  for each tool_call (up to SEARCH_MAX_CALLS_PER_REVIEW=5 total):
      validate (registry): name known, args parse, query non-empty ≤ SEARCH_MAX_QUERY_CHARS
        └─ invalid → skip this call (recorded), continue with the rest
      cache first (cache.py); else search_internet(query); cache structured results
      build web evidence from result.results (§15.2), source_type="web",
        capped to SEARCH_MAX_SOURCES_PER_QUERY=3 per query

Output: web_context = { queries:[...], sources:[ {n,title,url,snippet,published,retrieved_at} ],
        evidence_ids:[...] }  handed to the writer under a separate key (§19).
```

Notes:
- Only the model's `tool_calls` (the queries) are used; any free-text it emits is ignored.
  The writer, not the research model, writes "Bối cảnh thị trường".
- No re-invocation and no summarizer LLM call — web evidence is built by the backend from the
  structured SearxNG results and passed straight to the writer, which cites it.
- 9B models sometimes emit malformed tool calls; `--jinja` grammar-constrained tool calling
  helps but isn't perfect — hence strict per-call validation and the ≤5-call cap. A malformed
  call is skipped; the remaining calls still run; it never aborts the review.

### 16.5 Citation and provenance

```text
- every kept SearxNG result → evidence item source_type="web" (§15.2 web variant), built by
  the backend from the STRUCTURED results, never from model text
- "Bối cảnh thị trường" is the ONLY place web claims may appear, each with [n]; web numbers
  may NEVER be mixed into database sections (writer rule §19.2)
- frontend SourcesList renders [n] → {title, url, retrieved_at} from the evidence store
- research_cache (§20.1): a repeated query within SEARCH_CACHE_TTL_HOURS is free (no SearxNG hit)
```

### 16.6 Kill switch and failure behavior

```text
SEARCH_ENABLED=0 by default during rollout (flips to 1 after Phase 17 live verification).
SearxNG unreachable / HTTP error / timeout / zero results / model emits no tool calls:
  → research yields empty web_context, SSE {"type":"step","step":"research","status":"skipped"},
    one caveat line ("Không truy cập được nguồn web; báo cáo dựa trên dữ liệu nội bộ.")
  → SQL evidence path NEVER blocked; offline report ships in full
Budget: research is skipped first under wall-clock pressure (§7.3), before truncating SQL tasks.
```

---

## 17. Visualization Layer

### 17.1 Deterministic chart planner

`analysis/chart_planner.py`: `expected_shape` + profile + the `chart_rule` entries (§10.2,
KB-editable) → chart spec. No LLM involvement.

| Shape | Default chart (from seeded chart_rule) |
|---|---|
| `kpi_comparison` | grouped_bar (current vs previous) |
| `trend` | line |
| `top_n` | horizontal_bar |
| `composition` | stacked_bar |
| `raw` / empty / >max_categories | none — table only |

### 17.2 Chart spec contract

```json
{
  "chart_id": "c1",
  "type": "horizontal_bar",
  "title": "Giảm doanh thu theo ngành hàng",
  "x_field": "category",
  "series": [{"name": "Thay đổi doanh thu", "value_field": "revenue_change"}],
  "data": [{"category": "Beverage", "revenue_change": -120000000}],
  "unit": "VND",
  "evidence_id": "ev4",
  "notes": ""
}
```

Caps: ≤50 data points per chart; only aggregated/profiled data is charted, never raw rows.

### 17.3 Frontend rendering

- **`ChartRenderer.tsx` on `recharts`** — decision: extending the hand-rolled SVG
  BarChart to grouped bars, lines, stacks, legends, tooltips, and responsive sizing costs
  more than the dependency; recharts is pure React/SVG and works offline from npm.
- **`react-markdown` + `remark-gfm`** render the report body.
- Existing `BarChart.tsx` stays untouched for NORMAL_SQL results (zero regression).
- These are the only three new frontend dependencies.

---

## 18. Explanation Builder + Improvement Advisor (deterministic)

`analysis/advisor.py` — no LLM call. Matches profiler outcomes against the used
playbook's `interpretation_rules` / `improvement_rules` and the metric entries'
`interpretation_down/up` + `decomposition`:

```text
Which decomposition metric moved most?   (order_count vs active_customers vs AOV)
Which dimension concentrates the change? (top-3 concentration share > 50% → concentrated)
→ matched interpretation bullets  (facts vs likely-driver phrasing preserved)
→ matched improvement bullets     (e.g. active customers dropped → reactivation list,
                                   route coverage check; AOV dropped → product mix, bundles)
→ next-drilldown suggestions      (playbook + dimension drill_down_to)
```

Output bullets are **inputs to the writer** (§19) and the skeleton fallback — they are
never presented as LLM reasoning. Because the rules live in KB entries, the owner tunes
the advice without touching code.

---

## 19. Final Analytic Writer

### 19.1 Input bundle

```text
user question + analysis_title | evidence items (profiles + ≤10 rows per table) |
advisor bullets | caveats (KB caveat entries + runtime warnings + skipped-stage notices) |
web_context (separate key, §16) | report section contract (§7.2)
```

### 19.2 Writer rules (prompt)

```text
- Mirror the user's language.
- Every number must come from the evidence bundle. Do not invent numbers, causes,
  tables, or columns.
- Say "likely / có thể" for correlational interpretation; facts and interpretation
  stay separated.
- Web claims ONLY in "Bối cảnh thị trường", each with [n]; never blend web numbers
  into database sections.
- Mention missing data (caveats) instead of guessing.
- Do not restate full tables in prose — reference them; the frontend renders tables
  and charts from structured data, and formats VND.
```

### 19.3 Parameters

Streamed over SSE `token` events. `LLM_TEMPERATURE_WRITER=0.4`,
`LLM_MAX_TOKENS_WRITER=4000` (per-call overrides on the existing client).

### 19.4 Skeleton fallback (writer LLM fails)

Deterministic markdown assembled from: analysis title, per-evidence profiler sentences
("Doanh thu giảm 21.2% (820M so với 1,040M)…"), evidence tables, chart placeholders,
advisor bullets, caveat list — headed by:

```text
> Báo cáo rút gọn (LLM không phản hồi) — số liệu và bảng bên dưới là chính xác;
> phần diễn giải tự động được tạo từ quy tắc.
```

The user always gets the numbers, tables, and charts.

---

## 20. Review Memory + Follow-ups

### 20.1 Storage (new tables in conversations.db)

A review is 1 turn → N tasks → N evidence items → 1 report; flattening that into `Turn`
columns would be lossy. `Turn` gains one nullable `review_id` link.

```sql
CREATE TABLE reviews (
  review_id         TEXT PRIMARY KEY,
  conversation_id   TEXT NOT NULL,
  turn_id           TEXT NOT NULL,
  mode              TEXT NOT NULL,        -- ANALYTIC_MODE | ANALYTIC_FROM_PREVIOUS_RESULT
  question          TEXT NOT NULL,
  review_seed_json  TEXT,                 -- §8.3, null for fresh analyses
  plan_json         TEXT NOT NULL,        -- §13.2 (validated plan, incl. dropped-task notes)
  findings_summary  TEXT,                 -- short extract for memory windows
  report_markdown   TEXT NOT NULL,
  sources_json      TEXT,                 -- web sources [{n, title, url, retrieved_at}]
  status            TEXT NOT NULL,        -- complete | degraded | failed
  created_at        TEXT NOT NULL
);

CREATE TABLE evidence (
  evidence_id  TEXT PRIMARY KEY,
  review_id    TEXT NOT NULL REFERENCES reviews(review_id),
  task_id      TEXT,
  kind         TEXT NOT NULL,
  source_type  TEXT NOT NULL,             -- 'sql' | 'web'
  title        TEXT NOT NULL,
  sql          TEXT,
  columns_json TEXT, rows_json TEXT,      -- ≤ ANALYTIC_EVIDENCE_MAX_ROWS
  profile_json TEXT, web_json TEXT,
  chart_json   TEXT,                      -- §17.2 spec, nullable
  status       TEXT NOT NULL,             -- success | failed | skipped
  created_at   TEXT NOT NULL
);

CREATE TABLE research_cache (
  query_norm   TEXT PRIMARY KEY,
  results_json TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
```

### 20.2 Follow-up chips

The writer's "Phân tích tiếp theo" items and the advisor's drill-down suggestions ship in
the final SSE payload as `follow_up_suggestions[]`; the frontend renders them as clickable
chips that submit the suggestion as the next message.

---

## 21. Safety Rules (unified)

### 21.1 Normal SQL (existing, unchanged)

```text
SELECT/WITH only · no DML/DDL/PRAGMA · single statement · sqlglot AST parse ·
schema allowlist (tables + columns) · no diacritic identifiers · explicit LIMIT ≤ 500
(raw non-aggregate selects capped at RAW_SELECT_LIMIT=100) ·
AUTO_LIMIT=200 injected on raw selects · read-only connection (mode=ro) ·
10s progress-handler timeout · MAX_RESULT_ROWS=500 · one self-repair round
```

### 21.2 Analytic additions

```text
≤ 6 SQL tasks per review              (ANALYTIC_MAX_TASKS)
≤ 1 repair per task                   (ANALYTIC_MAX_REPAIRS_PER_TASK)
≤ 10s per task                        (QUERY_TIMEOUT_SEC, existing)
≤ 120s per review                     (ANALYTIC_TOTAL_BUDGET_SEC)
≤ 20 evidence rows stored per task    (ANALYTIC_EVIDENCE_MAX_ROWS)
≤ 50 chart data points
≤ 2 planner attempts, then fallback pack
≤ 5 search calls per review           (SEARCH_MAX_CALLS_PER_REVIEW)
≤ 10s per search call                 (SEARCH_TIMEOUT_SEC)
≤ 200 chars per search query          (SEARCH_MAX_QUERY_CHARS)
report ≤ 4000 tokens                  (LLM_MAX_TOKENS_WRITER)
duplicate-SQL detection across tasks
ALL analytic SQL through the same validator + the same read-only connection —
analytic mode can never write.
```

---

## 22. API Surface + SSE Catalog

### 22.1 Endpoints

| Endpoint | Status | Purpose |
|---|---|---|
| `POST /api/chat`, `POST /api/chat/stream` | existing | all 4 modes route inside (§5) |
| `GET/POST /api/conversations`… | existing | conversation CRUD |
| `GET/POST/PUT/DELETE /api/entries`, `POST /api/entries/{id}/reembed` | existing | KB CRUD (now with validation errors) |
| `GET /api/entries/{id}/history`, `POST /api/entries/{id}/restore` | **new** | audit + rollback (§12.4) |
| `GET /api/knowledge/status`, `POST /api/knowledge/seed`, `/rebuild/*`, `/export-docs` | existing | management (rebuilds become rarely-needed) |
| `POST /api/knowledge/sync-values` | **new** | refresh value entries (§12.6) |
| `GET /api/kb/version` | **new** | current kb_version (UI freshness badge) |
| `POST /api/retrieve`, `POST /api/chat/plan` | existing | testers |
| `POST /api/analysis/plan` | **new** | tester: question → mode + context + (dry) plan, no execution |
| `GET /api/reviews/{review_id}` | **new** | full review + evidence + charts (re-render on reopen) |
| `GET /api/conversations/{id}/reviews` | **new** | review list for a conversation |
| `POST /api/research/test` | **new** | run one query through SearxNG, return normalized results |
| `GET /api/health` | **new** | deep health (§25.3), cached 30s |

### 22.2 SSE catalog

Existing envelope kept: `{"type":"step","step":"...","status":"start|done|error", ...}`
with existing steps `plan, retrieve, llm, repair, validate, execute, summarize` (normal mode).

New steps (analytic mode):

```text
mode      {"mode": "ANALYTIC_MODE"}
plan      planner running / done {"task_count": 5}
task      {"task_index": 2, "task_total": 5, "title": "..."} per SQL task
profile   profiling done
research  {"query": "...", "source_count": 3} or {"status": "skipped"}
charts    chart specs built {"chart_count": 2}
write     writer started (then `token` events stream the report)
save      review persisted {"review_id": "rv_001"}
```

New event types:

```text
{"type": "evidence", "evidence": {…§15.2}}   pushed as each task completes → tables render progressively
{"type": "chart",    "chart": {…§17.2}}
{"type": "token",    "text": "…"}            existing type, reused for writer streaming
{"type": "final",    "response": {"mode", "review_id", "report_markdown", "evidence": [],
                                  "charts": [], "sources": [], "follow_up_suggestions": [],
                                  "caveats": [], "error": null}}
```

---

## 23. Frontend Design (Vietnamese-first)

### 23.1 Structure

Tab layout unchanged (Chat / Entries / skill.md / Retrieval tester / Plan tester).
Chat gains analytic rendering; Entries gains the analytic types and safety UX.

### 23.2 New components

```text
AnalyticReport.tsx   react-markdown report body; EvidenceTable / ChartRenderer / SourcesList
                     interleaved by section; collapsible "SQL & bằng chứng" drawer
EvidenceTable.tsx    reuses ResultTable styling + CSV export, shows task title + status
ChartRenderer.tsx    recharts: grouped_bar | line | horizontal_bar | stacked_bar (§17)
ReviewProgress.tsx   task-level stepper driven by SSE `task`/`research`/`write` events
SourcesList.tsx      [n] → title, url, retrieved_at (web evidence only)
ErrorBoundary.tsx    per-tab and per-report; a render error never blanks the app
```

Plus: ResultTable client-side pagination (50/page, replaces the silent cap), follow-up
suggestion chips, reopening an old conversation re-renders its reports via `GET /api/reviews/*`.

### 23.3 Vietnamese-first

All user-visible strings centralized in `frontend/src/i18n.ts` (a plain VN dictionary —
no i18n library). Existing hardcoded labels migrate there. The bot mirrors the user's
message language (prompt rule, already partially in place).

### 23.4 KB editor UX (requirement: easy to use)

```text
- Type-specific structured form for playbook: diagnostic step list with add/remove/reorder,
  dropdowns for metric/dimension references (populated from existing entries)
- "Tạo từ mẫu" (create-from-template): one template per seeded playbook kind
- Inline save-time validation errors (field-level, from §12.3)
- Entry history viewer + "Khôi phục" restore per version (§12.4)
- "Chạy thử playbook" button → POST /api/analysis/plan dry-run showing the task pack
  the playbook would generate
- embed_status badge incl. 'pending' (§12.5) + re-embed action
- kb_version badge in StatusBar ("Kiến thức đã cập nhật · v123")
```

---

## 24. Configuration Reference

All keys env-overridable via `.env` at repo root; `.env.example` committed and kept complete.

### 24.1 Changed defaults

| Key | New default | Note |
|---|---|---|
| `LLM_BASE_URL` | `http://192.168.0.5:30187/v1` | llama.cpp; dead ngrok default removed |
| `LLM_MODEL` | `""` (auto-discover via `GET /models`) | fallback `LLM_MODEL_FALLBACK` |
| `LLM_NGROK_SKIP_WARNING` | **deleted** | ngrok-specific |
| `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` | replaced by per-call keys below | resolves uncommitted client.py drift |

### 24.2 New keys

```text
# LLM per-call
LLM_TEMPERATURE_SQL=0            LLM_MAX_TOKENS_SQL=1200
LLM_TEMPERATURE_WRITER=0.4       LLM_MAX_TOKENS_WRITER=4000

# Analytic
ANALYTIC_ENABLED=1               ANALYTIC_MAX_TASKS=6
ANALYTIC_TOTAL_BUDGET_SEC=120    ANALYTIC_EVIDENCE_MAX_ROWS=20
ANALYTIC_MAX_REPAIRS_PER_TASK=1

# Retrieval buckets
RETRIEVAL_TOPK_PLAYBOOK=2  RETRIEVAL_TOPK_CAVEAT=3  RETRIEVAL_TOPK_DIMENSION=4

# Knowledge base
KB_AUTO_RENDER=1                 KB_VALIDATE_ON_SAVE=strict     # strict|warn|off
VALUE_SAMPLE_LIMIT=200           # raised from 30

# Web research (SearxNG, native tool-calling)
SEARCH_ENABLED=0                 SEARXNG_URL=http://192.168.0.190:30192  # legacy alias: DATAMIND_SEARXNG_URL
SEARCH_TIMEOUT_SEC=10            SEARCH_LANGUAGE=vi
SEARCH_MAX_RESULTS=5             SEARCH_MAX_SNIPPET_CHARS=500
SEARCH_MAX_CALLS_PER_REVIEW=5    SEARCH_MAX_SOURCES_PER_QUERY=3
SEARCH_MAX_QUERY_CHARS=200       SEARCH_CACHE_TTL_HOURS=24

# Logging
LOG_LEVEL=INFO                   LOG_FORMAT=console             # |json
LOG_FILE=logs/app.jsonl
```

All existing keys (paths, embedding block, retrieval top-ks, memory knobs, skill-context
caps, SQL limits) are unchanged.

---

## 25. Production Hardening

### 25.1 Logging

`backend/common/logging.py`: stdlib logging, console + JSON-lines file formatter,
request-id middleware, contextual fields (`request_id`, `conversation_id`, `turn_id`,
`review_id`). Every `print()` in the backend is replaced.

### 25.2 Startup

```text
scripts/start.ps1   venv check → npm build if frontend/dist missing/stale →
                    uvicorn serving API + static dist  (ONE process, one command)
scripts/dev.ps1     uvicorn --reload + vite dev concurrently
```

`backend/app.py` mounts `frontend/dist` as static files when present.

### 25.3 Health

`GET /api/health` (cached 30s):

```json
{
  "db": {"ok": true, "path": "data/sales.db"},
  "knowledge": {"entries": 412, "kb_version": 57, "pending_embeds": 0},
  "index": {"vectors": 398, "dim": 2560},
  "embedder": {"ok": true, "model": "unsloth/Qwen3-Embedding-4B", "device": "cuda"},
  "llm": {"reachable": true, "model": "qwen3.5-9b", "latency_ms": 180},
  "search": {"enabled": false, "reachable": null, "url": "http://192.168.0.190:30192"}
}
```

Frontend StatusBar shows traffic lights for LLM / embedder / search (SearxNG).

### 25.4 Graceful degradation matrix

| Failure | Behavior |
|---|---|
| LLM down | chat returns a clear VN error + health hint; KB editor fully unaffected |
| Embedder down | app still starts (lazy load); KB saves persist as `embed_status=pending` + retried (§12.5); semantic retrieval degraded, exact pinning still works |
| SearxNG down / search disabled | research skipped with notice (§16.6); analysis fully functional |
| Writer LLM fails mid-review | skeleton report with real tables/charts (§19.4) |
| Planner JSON invalid | retry → fallback pack (§13.4) |
| sales.db missing | startup error naming the expected path and the `DB_PATH` override |

### 25.5 Tests + golden evaluation

```text
backend/tests/ (pytest, EMBEDDER=hashing so no GPU needed):
  test_sql_validator, test_mode_detector (≥30 VN/EN fixtures),
  test_entry_validator, test_kb_hot_reload (save → retrieve sees change, no restart),
  test_review_target_resolver, test_planner_validation (fixture JSONs incl. malformed),
  test_fallback_packs, test_profiler, test_chart_planner, test_review_store,
  test_advisor, test_followup_fallback, test_writer_fallback,
  test_search_internet, test_research_cache, test_research_planner, test_research_degradation (mock SearxNG),
  test_response_parsers, test_health

golden/golden_questions.jsonl:
  ~30 normal VN questions (expected tables touched / non-empty result flags)
  ~10 analytic questions (expected mode, expected playbook, task-count bounds)
scripts/golden_eval.py: runs the set, prints a pass/fail table;
  LLM-dependent assertions marked skippable for offline runs.
```

---

## 26. Failure Modes & Fallback Matrix ⭐

The thesis of this design: **the pipeline is deterministic scaffolding; the LLM fills in
plans and prose. LLM absence degrades output quality, never availability.**

| Boundary | Failure signature | Detection | Fallback | User sees |
|---|---|---|---|---|
| Normal-SQL JSON | malformed JSON / wrong shape | defensive parser (existing) | error answer w/ retry hint | friendly VN error |
| Planner JSON | malformed / <2 valid tasks | validation ladder §13.3 | 1 retry → playbook fallback pack | full review, no visible difference |
| Task SQL | validator reject / exec error | validator + runner | ≤1 repair → failed-evidence | report notes the step that failed |
| Writer | timeout / empty / garbage | client error or empty content | skeleton report §19.4 | tables+charts+bullets, "báo cáo rút gọn" banner |
| Follow-up JSON | malformed | parser | keyword-match evidence rendering | matching evidence table + template sentence |
| Search tool call | malformed/hallucinated call | strict arg validation §16.4 | skip that call; remaining calls still run | nothing — review still ships |
| SearxNG transport | unreachable / timeout / zero results | search_internet (never raises) | skip research + caveat | notice line in report |
| Embedder | unavailable | encode raises | pending-embed + retry §12.5 | KB save succeeds, badge shows pending |
| kb staleness | (eliminated) | ensure_fresh §12.2 | — | edits always live |

---

## 27. MVP Definition of Done

### Flow A — fresh analytic question

```text
User: "Phân tích vì sao doanh thu tháng 3/2025 giảm?"
→ ANALYTIC_MODE · revenue_drop playbook retrieved · 5–6 validated SQL tasks
→ progressive evidence tables in the UI while tasks run
→ report with ≥3 evidence tables, ≥1 chart, explanation, improvement advice,
  caveats, follow-up chips · review persisted
```

### Flow B — analytic from previous result

```text
User: "Top 10 khách hàng theo doanh thu tháng 3/2025"   → NORMAL_SQL table
User: "Phân tích sâu khách hàng top 1"
→ ANALYTIC_FROM_PREVIOUS_RESULT · seed = KH rank-1 from result_entities
→ customer-scoped task pack → full report on that customer
```

### Flow C — analytic follow-up

```text
User: "Vì sao khu vực miền Trung giảm mạnh nhất?"
→ ANALYTIC_FOLLOWUP · answered from stored evidence (no new SQL)
User: "Cho xem SQL đã dùng" → stored task SQL list rendered
```

Plus: **KB flow** — edit the `doanh_thu` formula in the UI → the very next question uses
it, no restart; break the formula → save rejected with an inline message.
**Research flow** — with `SEARCH_ENABLED=1` + SearxNG up, Flow A's report includes
"Bối cảnh thị trường" with ≥1 cited source; with SearxNG down, same question ships the
full offline report with a notice.

---

## 28. Section → Phase Map and V2

Implementation order, files, tasks, and done-criteria: see `phased.md`.

| plan.md section | phased.md phase |
|---|---|
| §24–25 config/logging/startup/health | Phase 9 |
| §12 KB live updates | Phase 10 |
| §10–11 analytic knowledge + retrieval | Phase 11 |
| §3, §5, §8, §11 mode router + context + seed | Phase 12 |
| §13–14 planner + task runner | Phase 13 |
| §15, §17, §20.1 profiler + evidence + review storage + chart specs | Phase 14 |
| §18–19, §20.2, §9 writer + advisor + follow-up | Phase 15 |
| §23, §17.3 frontend report UI + KB UX | Phase 16 |
| §16 web research (SearxNG) | Phase 17 |
| §25.5, §26 hardening + golden eval | Phase 18 |

V2 (after Phase 18): multi-round review decider, user-selectable review depth
(quick/standard/deep), async task execution + result caching, value auto-sync scheduler,
report export (PDF/DOCX).

---

*The LLM proposes. The backend validates, executes, profiles, visualizes, remembers —
and now researches and hot-reloads its own knowledge.*



