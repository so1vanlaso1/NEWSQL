# SQLNEW Implementation Roadmap v2

> Companion to `plan.md` (SQLNEW Production System Plan v2). plan.md holds the
> architecture and contracts; this file holds the build order.
>
> - **Phases 1–8: ✅ DONE** — the as-built `NORMAL_SQL` conversational pipeline (kept as historical record, compressed).
> - **Phases 9–10: ✅ DONE** — platform foundation (logging, health, per-call LLM params, one-command start) + live-editable knowledge base (kb_version hot-reload, save-time validation, history/restore, embedder-down resilience).
> - **Phases 11–12: ✅ DONE** — analytic knowledge types (playbook/caveat/dimension/chart_rule + metric extensions, seeded and hot-reloaded) + the 4-mode heuristic router, AnalyticContext builder, and previous-result ReviewSeed resolver (behind `ANALYTIC_ENABLED=0`).
> - **Phases 13–14: ✅ DONE** — the review planner + validation ladder + deterministic fallback packs + task runner (analytic answers ship, `ANALYTIC_ENABLED=1`), then the profiler, provenance-tagged evidence store, deterministic chart specs, and review persistence with read endpoints.
> - **Phases 15–18: the remaining v2 roadmap** — writer + advisor + follow-up, visualization/report UI, SearxNG web research, golden evaluation.

## 0. How to Read This File

Legend:

```text
✅ DONE     shipped and working
⭐ NEXT     the next phase to build
⬜ PLANNED  designed, not started
🔒          safety / validation requirement
```

**Two invariants, enforced in every phase:**

```text
1. Every phase leaves the app usable.
2. Every LLM boundary has a deterministic fallback.
```

Dependency graph:

```text
9 (platform foundation)
└── 10 (KB live updates)
    └── 11 (analytic knowledge types)
        └── 12 (mode router + context + seed)
            └── 13 (planner + task runner)   ← analytic answers first ship here
                └── 14 (profiler + evidence + chart specs)
                    └── 15 (writer + advisor + follow-up)   ← analytic MVP complete, offline
                        ├── 16 (frontend report UI + KB UX)
                        └── 17 (web research: SearxNG)       ← needs 16's SourcesList for full UX
                            └── 18 (hardening + golden eval + docs)
```

Every new phase uses the same template: **Goal / New & modified files / Config added /
Tasks / Done when / Test plan.** Contracts (JSON shapes, DDL, SSE events, config keys)
are defined once in plan.md and referenced here by section number.

---

## 1. Phases 1–8 — ✅ DONE (as-built record)

### Phase 1 — Database Knowledge Foundation ✅

Goal: knowledge source of truth for the FMCG sales DB.
As built: `skills/sales/knowledge.db` (entries: table, column, metric, join_path, value, rule;
deterministic ids; content_hash) via `backend/store/{db,models,repository}.py`;
`skill.md` rendered by `backend/knowledge/skill_builder.py`; SQLite dialect pinned;
`doanh_thu = SUM(chi_tiet_don_hang_ban.thanh_tien)` with join
`don_hang_ban.don_hang_id = chi_tiet_don_hang_ban.don_hang_id`, status `trang_thai='NORMAL'`.

### Phase 2 — Embedding Document Builder ✅

As built: per-type embedding docs (`backend/knowledge/embedding_text.py`), export to
`embedding_docs.jsonl` (`backend/ingestion/export_docs.py`), Qwen3-Embedding-4B
(2560-dim, 4-bit NF4, CUDA) in `backend/embeddings/embedder.py`, hashing fallback embedder,
per-entry re-embed on save (hash-gated).

### Phase 3 — Vector Index + Retrieval ✅

As built: numpy IndexStore (`skills/sales/index/`), query expansion, per-type bucketed
search, exact value/entity pinning (alias index), weighted table resolver
(budget `RETRIEVAL_MAX_TABLES=6`), FK-graph BFS join expansion with curated overrides →
compact `ResolvedContext` (`backend/retrieval/context_builder.py`).

### Phase 4 — Conversation Memory ✅

As built: `skills/sales/conversations.db`, conversation CRUD, persistent turns, compact
memory window (`MEMORY_RECENT_TURNS=6`), result preview (5 rows) + display rows (60),
result summary, entity extraction (`*_id` paired with `ten_*` columns), full model I/O logs.

The Turn fields (verbatim carry-over — later phases reference this):

```text
turn_id, conversation_id, turn_index, user_question, normalized_question,
standalone_question, intent, needs_sql, selected_tables/columns/metrics/filters,
generated_sql, result_columns, result_preview, result_entities
(type/id_column/id_value/name_column/name_value), result_summary, answer,
answer_from_memory, display_rows, row_count, truncated, error, llm_model,
llm_skill_context, llm_system_prompt, llm_user_prompt, llm_raw_response, created_at
```

### Phase 5 — Intent + Retrieval Planning ✅

As built: heuristic 7-intent classifier (`backend/memory/intent_classifier.py`) on
normalized khong-dau text; retrieval planner gates retrieval by intent and pins previous
tables/entities for refine/drill-down; LLM stays authoritative for the final intent.

### Phase 6 — Compact Skill Context Builder ✅

As built: `backend/retrieval/skill_context.py` — SQLite rules, data window, metric policy,
per-table column caps (`SKILL_CTX_*` knobs), join + matched-value context, rules fallback
for no-retrieval turns.

### Phase 7 — One-LLM SQL Decision ✅

As built: OpenAI-compatible client (`backend/llm/client.py`: streaming with blocking
fallback, `response_format=json_object` with auto-retry-without, model auto-discovery,
never raises), SQLite-pinned prompt (`prompt_builder.py`), defensive JSON parser
(`response_parser.py`).

The LLM JSON contract (verbatim carry-over): see plan.md §6.2.

### Phase 8 — SQL Validation, Execution, Memory Update ✅

As built: 6-layer validator (`backend/validation/sql_validator.py`: regex gate, sqlglot
AST, diacritics check, schema allowlist, LIMIT policy, EXPLAIN binding), read-only runner
(`backend/execution/query_runner.py`: mode=ro, 10s progress-handler timeout,
`MAX_RESULT_ROWS=500`), one self-repair round, result summarization + entity extraction,
memory write-back, SSE step/token streaming wired to the chat UI.

---

## Phase 9 — Platform Foundation ✅ DONE

**Goal.** Point the system at the real LLM endpoint, clean up configuration, add
structured logging and health checks, and make startup one command. Everything later
depends on this being solid.

> **As built.** `config.py` LLM block rewritten (real `LLM_BASE_URL` default, per-call
> `LLM_TEMPERATURE/MAX_TOKENS_SQL|WRITER`, logging keys); `common/logging.py` (console +
> rotating JSON-lines, request-id middleware, contextual fields via contextvars); deep
> cached `api/health.py`; `app.py` mounts `frontend/dist` + request-id middleware;
> `scripts/start.ps1` + `dev.ps1`; regenerated `.env.example`; server-runtime `print()`
> calls replaced with logging (CLI `__main__` blocks + `smoke_test.py` harnesses keep
> stdout, by design). **Deviation:** `LLM_NGROK_SKIP_WARNING` kept (harmless header for
> llama.cpp) rather than deleted, to avoid churning the client signature.

**New & modified files**

```text
backend/config.py                LLM block rewrite (plan.md §24.1): default
                                 LLM_BASE_URL=http://192.168.0.5:30187/v1, delete ngrok
                                 default + LLM_NGROK_SKIP_WARNING, per-call LLM keys
backend/llm/client.py            per-call temperature/max_tokens overrides on chat()/
                                 stream_chat(); resolves the uncommitted temp=1/4000 drift
                                 into LLM_*_SQL / LLM_*_WRITER params, then commit
backend/common/logging.py        (new) stdlib logging, JSON-lines file + console,
                                 request-id middleware, contextual fields
backend/app.py                   logging init, static frontend/dist mount, middleware
backend/api/health.py            (new) deep GET /api/health (plan.md §25.3), cached 30s
scripts/start.ps1, scripts/dev.ps1   (new) one-command start / dev mode
.env.example                     complete key list; README quickstart section
```

**Config added:** `LLM_TEMPERATURE_SQL=0`, `LLM_MAX_TOKENS_SQL=1200`,
`LLM_TEMPERATURE_WRITER=0.4`, `LLM_MAX_TOKENS_WRITER=4000`, `LOG_LEVEL`, `LOG_FORMAT`,
`LOG_FILE`.

**Tasks**

```text
[x] Rewrite the LLM config block; remove the broken placeholder base-url default
[x] Per-call param overrides in LlmClient; pin SQL calls to 0/1200 (writer 0.4/4000)
[x] Resolve the staged client.py change (per-call overrides already present)
[x] backend/common/logging.py + replace server-runtime print() with logging
[x] Request-id middleware; contextual log fields (request_id/conversation_id/turn_id/review_id)
[x] backend/api/health.py with db/knowledge/index/embedder/llm/mcp blocks (cached 30s)
[x] Mount frontend/dist static files in app.py when the folder exists
[x] scripts/start.ps1 (venv check → conditional npm build → uvicorn) and dev.ps1
[x] .env.example regenerated from config.py; README quickstart
```

**Done when**

```text
- scripts\start.ps1 on a fresh clone serves UI + API in one process against llama.cpp
- GET /api/health reports llm reachable with model id + latency
- zero print() calls remain under backend/
- 5 golden VN questions answer correctly through the llama.cpp endpoint
```

**Test plan:** `pytest backend/tests/test_health.py` (mocked LLM); manual smoke:
"Top 10 khách hàng theo doanh thu tháng 3/2025", "5 sản phẩm bán chạy nhất 2025",
one refine follow-up, one drill-down, one "câu SQL vừa rồi là gì?".

---

## Phase 10 — KB Live Updates ✅ DONE 🔒 (fulfils requirement: KB editable anytime)

**Goal.** Every knowledge edit takes effect on the next question with no restart; invalid
entries are rejected at save with a clear message; every change is audited and restorable;
the embedder being down never blocks editing. Mechanism: plan.md §12.

> **As built.** `store/db.py` gains `meta` (kb_version, seeded to 0) + `entry_history`
> (both `IF NOT EXISTS`, so a pre-Phase-10 knowledge.db upgrades in place);
> `store/repository.py` gets `get/bump_kb_version` + `record_history/list_history/get_history_row`;
> `knowledge/entry_validator.py` (metric formula sqlglot-parse + schema col refs; join_path;
> value/column/table; dormant dimension branch for Phase 11); `knowledge/service.py` runs the
> whole save/delete under one re-entrant lock — validate → history → upsert → embed-or-pending
> → auto-render skill.md + docs → bump version — plus `restore()` and `sync_values()`;
> `retrieval/context_builder.py` adds `ensure_fresh()` (version check + derived-dict rebuild),
> called at the top of `retrieve()`, `chat._run_turn`, and `chat_plan`; endpoints:
> `GET /api/kb/version`, `POST /api/knowledge/sync-values`, `POST /api/embed-pending`,
> `GET/POST /api/entries/{id}/history|restore`. Frontend: KB-version badge + Embed-pending +
> Sync-values in StatusBar, History viewer + Restore in EntryForm. Verified by
> `backend/tests/test_kb_hot_reload.py` + `test_entry_validator.py`.
>
> **Post-implementation adversarial review hardening** (`test_kb_enabled_and_concurrency.py`):
> (1) the shared in-memory `IndexStore` now takes a re-entrant lock around upsert/delete/
> search/save — a chat/retrieve turn can no longer crash or read a torn index while an edit
> mutates it; (2) `RetrievalService.ensure_fresh()` swaps its derived caches as one atomic
> bundle and `retrieve()` snapshots it once, so a turn can't mix two kb_versions; (3) the
> `enabled` flag is now honored live — a disabled rule/normalization entry drops out of the
> LLM context and skill.md (previously a silent no-op). Known minor limitation: for the brief
> window between a new entry's index upsert and its kb_version bump, a concurrent turn may see
> the vector before the enriched body (empty formula for one turn; self-heals next turn).

**New & modified files**

```text
backend/store/db.py              + meta table (kb_version), + entry_history table (§12.4 DDL)
backend/store/repository.py      + version bump, history writes, process-wide save lock
backend/knowledge/entry_validator.py   (new) save-time validation (plan.md §12.3)
backend/knowledge/service.py     save pipeline: validate → history → upsert → embed-or-pending
                                 → render skill.md + export jsonl (KB_AUTO_RENDER) → bump version
backend/retrieval/context_builder.py   ensure_fresh(): kb_version check + derived-dict rebuild
backend/api/chat.py              call ensure_fresh() at turn start
backend/api/entries.py           validation errors as field-level 422s; + GET {id}/history,
                                 POST {id}/restore
backend/api/knowledge.py         + POST /api/knowledge/sync-values, + GET /api/kb/version
frontend/src/components/EntryForm.tsx   inline validation error display
frontend/src/components/EntryList.tsx   history viewer + restore
frontend/src/components/StatusBar.tsx   kb_version badge, pending-embed retry button
```

**Config added:** `KB_AUTO_RENDER=1`, `KB_VALIDATE_ON_SAVE=strict`, `VALUE_SAMPLE_LIMIT=200`.

**Tasks**

```text
[x] meta.kb_version + entry_history schema and repository support
[x] 🔒 entry_validator: metric formula sqlglot-parse + schema references; join_path,
    value, column, table checks (dimension/playbook branches dormant until Phase 11)
[x] Save pipeline ordering per plan.md §12.2 under one process (re-entrant) lock
[x] embed_status='pending' path when embedder unavailable + startup/manual retry
[x] ensure_fresh() in RetrievalService: rebuild norm_map, value_index, global_rules,
    table/metric/column/join_path defs on version change
[x] Auto render skill.md + export embedding_docs.jsonl on every save/delete (KB_AUTO_RENDER)
[x] history + restore endpoints and UI; sync-values endpoint
```

**Done when**

```text
- Edit the doanh_thu formula in the UI → the very next chat question uses it (no restart)
- Save a metric with a broken formula → 422 with a field-level VN message shown inline
- Restore an old entry version from the history viewer → active immediately
- Stop the embedder → entry save still succeeds as pending; retry embeds it later
- skill.md and embedding_docs.jsonl always match knowledge.db after any save
```

**Test plan:** `test_kb_hot_reload.py` (save → retrieve reflects the change in the same
process), `test_entry_validator.py` (valid/invalid fixtures per type); manual: formula
edit + broken formula + restore + embedder-down drill.

---

## Phase 11 — Analytic Knowledge Entry Types ✅ DONE

**Goal.** Add `playbook`, `caveat`, `dimension`, `chart_rule` entry types and analytic
metric extensions to the SAME knowledge system (plan.md §10) — editable and hot-reloaded;
playbook/caveat/dimension embeddable, chart_rule non-embedded policy — and ship the seed
content.

> **As built.** `store/models.py` gains `DiagnosticStep/PlaybookBody/CaveatBody/DimensionBody/
> ChartRuleBody` + 6 optional `MetricBody` extension fields (direction, decomposition,
> default_comparisons/dimensions, interpretation_up/down); `ENTRY_TYPES`/`EMBEDDABLE_TYPES`
> extended (chart_rule stays out of the embeddable set); `_slug` now strips Vietnamese
> diacritics so caveat ids are clean (`caveat:pham_vi_du_lieu`). `embedding_text.py` +
> `skill_builder.py` render the 3 embeddable types + the 4 new skill.md sections. New
> `knowledge/analysis_meta.py` holds the curated content (4 playbooks with parse-valid
> `sql_hint` templates, 8 dimensions, 6 caveats, 5 chart_rules, 3 metric extensions);
> `seed.seed_analysis()` stages them and merges metric extensions. `entry_validator.py`
> gains playbook (sql_hint parse + cross-entry metric/dimension refs), dimension
> (join_requirement), and chart_rule checks, and an optional `repo` param for cross-entry
> validation (service.py passes it). `vector_retriever.analytic_topk()` + 3 config keys add
> the buckets; `frontend/types.ts` gets generic FIELD_SPECS for the 4 types + metric
> extensions. Verified by `test_analytic_entry_types.py`.

**New & modified files**

```text
backend/store/models.py          + PlaybookBody, CaveatBody, DimensionBody, ChartRuleBody,
                                 DiagnosticStep; metric extensions; ENTRY_TYPES/EMBEDDABLE_TYPES
backend/knowledge/embedding_text.py    embedding text rules for the new types (§10.3)
backend/knowledge/skill_builder.py     new skill.md sections (§10.3)
backend/knowledge/seed.py        seed_analysis(): 4 playbooks, ~8 dimensions, ~6 caveats,
                                 5 chart_rules, metric extensions (§10.4)
backend/knowledge/entry_validator.py   playbook/dimension/chart_rule validation rules
backend/retrieval/vector_retriever.py  + playbook/caveat/dimension buckets
backend/config.py                + RETRIEVAL_TOPK_PLAYBOOK/CAVEAT/DIMENSION
frontend/src/types.ts            + FIELD_SPECS for the new types (generic forms;
                                 rich playbook editor arrives in Phase 16)
```

**Config added:** `RETRIEVAL_TOPK_PLAYBOOK=2`, `RETRIEVAL_TOPK_CAVEAT=3`,
`RETRIEVAL_TOPK_DIMENSION=4`.

**Tasks**

```text
[x] Body models with extra="forbid" + deterministic ids (playbook:{slug} etc.)
[x] Embedding text + skill.md rendering per type
[x] seed_analysis() content (port the old revenue-drop playbook §9.4–9.5 material)
[x] Retrieval buckets + config keys
[x] Validation rules for the new types (uses Phase 10 validator)
[x] Generic editor forms via FIELD_SPECS; entries appear in the type filter
```

**Done when**

```text
- POST /api/knowledge/seed stages + embeds the analytic entries
- skill.md shows "## Analysis Playbooks", "## Dimensions", "## Analysis Caveats", "## Chart Rules"
- POST /api/retrieve for "phân tích doanh thu giảm" returns the revenue_drop playbook,
  relevant dimensions, and caveats in their buckets
- Editing a seeded playbook in the UI re-embeds it and updates retrieval immediately
```

**Test plan:** id-derivation + body-validation units; bucket integration test with the
hashing embedder; manual retrieval checks for 5 analytic phrasings (VN + EN).

---

## Phase 12 — Mode Router + Analytic Context + Review Seed ✅ DONE

**Goal.** Route turns across the 4 modes (plan.md §3), build `AnalyticContext`
(plan.md §11), and resolve previous-result targets into a `ReviewSeed` (plan.md §8) —
all behind `ANALYTIC_ENABLED=0` so normal chat is untouched until Phase 13.

> **As built.** New `backend/analysis/` package: `models.py` (pydantic `TargetEntity`,
> `ReviewSeed` with `entity_filter_sql()`, `AnalyticContext` nesting `ResolvedContext`);
> `mode_detector.py` (3 không-dấu trigger lexicons + `detect_mode`, with the FOLLOWUP branch
> dormant until reviews exist in Phase 14); `review_target_resolver.py` (rank/name/last-row
> resolution against `result_preview`/`display_rows`, entity-column inference, safe refusal);
> `context_builder.py` (schema retrieval + analytic buckets + metric extensions + fresh
> chart_rules + memory window). `api/analysis.py` adds `POST /api/analysis/plan`; `app.py`
> registers it. `chat.py` detects the mode every turn and logs it, emitting the `mode` SSE
> step **only** when `ANALYTIC_ENABLED` (so the disabled-flag stream is byte-identical); every
> mode still falls through to the normal pipeline until Phase 13. `config.ANALYTIC_ENABLED=0`.
> Verified by `test_mode_detector.py` (33 VN/EN cases), `test_review_target_resolver.py`,
> `test_analytic_context.py`.

**New & modified files**

```text
backend/analysis/__init__.py, models.py     AnalyticContext, ReviewSeed dataclasses
backend/analysis/mode_detector.py           heuristic 4-mode router + trigger lexicons (§3.2–3.3)
backend/analysis/context_builder.py         AnalyticContext from shared RetrievalService (§11.2)
backend/analysis/review_target_resolver.py  previous-result → ReviewSeed (§8.2–8.3)
backend/api/chat.py                         mode detection at turn start; detected-but-disabled
                                            → normal pipeline + log; SSE step "mode"
backend/api/analysis.py                     (new) POST /api/analysis/plan tester
                                            (mode + retrieved context + seed; no execution yet)
```

**Config added:** `ANALYTIC_ENABLED=0` (flips in Phase 13).

**Tasks**

```text
[x] Trigger lexicons (VN khong-dau + EN) as module constants
[x] detect_mode() incl. follow-up window rule (last review = latest artifact)
[x] review_target_resolver: rank refs ("top 1", "dòng 3", "first one"), name fuzzy match,
    single-entity default, safe refusal with reason
[x] AnalyticContext assembly: ResolvedContext + playbooks/dimensions/caveats/metric ext.
    + chart_rules (fresh) + seed + memory summaries + data window
[x] Wire detection into chat.py behind the flag; emit SSE "mode"
[x] /api/analysis/plan tester endpoint
```

**Done when**

```text
- Detector fixture suite (≥30 VN/EN cases from plan.md §3.1 examples) passes
- Tester shows: "Vì sao doanh thu giảm?" → ANALYTIC_MODE + revenue_drop playbook;
  after a top-10 query, "phân tích thằng top 1" → ANALYTIC_FROM_PREVIOUS_RESULT
  + seed for the rank-1 entity
- With the flag off, normal chat behavior is byte-identical
```

**Test plan:** `test_mode_detector.py`, `test_review_target_resolver.py` (turn fixtures
with result_entities); manual tester sweep.

---

## Phase 13 — Review Planner + Task Runner (analytic answers first ship) ✅ DONE

**Goal.** Turn an analytic question into 2–6 validated, executed SQL tasks (plan.md
§13–14) and ship a first, table-first analytic answer. The deterministic fallback pack
means this works even when the 9B model returns garbage.

> **As built.** `analysis/date_window.py` (new) resolves the current-vs-comparison period
> deterministically from the question ("tháng 3/2025", "quý 1 2025", "năm 2024"), the seed
> filter, else the last full month. `llm/review_prompts.py` (new) serializes the
> AnalyticContext + seed + date window into the planner prompt (JSON contract §13.2) and a
> per-task repair prompt. `analysis/planner.py` runs LLM call 1 + the full ladder (parse →
> structure → per-task 6-layer `validate()` → dedupe → one retry-with-errors → fallback
> pack), returns a `mode_downgrade` when the question is really a lookup, and takes
> `client=None` to force the deterministic path. `analysis/fallback_packs.py` instantiates
> the top playbook's `diagnostic_steps` (placeholder substitution + validator gate) and, if a
> playbook yields < 2 valid tasks or none is retrieved, tops up with a guaranteed revenue
> KPI + monthly-trend pack. `analysis/task_runner.py` (`run_task` + `skipped_result`) reuses
> the validator + read-only runner unchanged with ≤1 LLM repair per runtime error.
> `analysis/controller.py` drives context → plan → per-task run, yielding SSE
> `mode/retrieve/plan/task` events; a `{"type":"downgrade"}` event tells `chat.py` to fall
> through to the normal pipeline. `api/chat.py` routes ANALYTIC modes to the controller
> (safe insufficient-context answer when a previous-result seed can't resolve) and
> `ChatResponse` gains the analytic fields. `ANALYTIC_ENABLED` default flipped to `1`.
> Frontend: `Chat.tsx` renders multi-table analytic turns (evidence tables via the existing
> `ResultTable`) + follow-up chips, and the stepper labels the analytic SSE steps. Verified
> by `test_date_window.py`, `test_fallback_packs.py`, `test_planner_validation.py`,
> `test_review_controller.py`, `test_chat_analytic_integration.py`.

**New & modified files**

```text
backend/analysis/date_window.py      (new) deterministic current/compare period resolver
backend/llm/review_prompts.py        (new) planner prompt (JSON contract §13.2) + repair prompt
backend/analysis/planner.py          LLM call 1 + validation ladder (§13.3)
backend/analysis/fallback_packs.py   playbook diagnostic_steps → task pack (§13.4)
backend/analysis/task_runner.py      sequential validated execution (§14)
backend/analysis/controller.py       context → plan → tasks → evidence/charts (Phase 14) → final
backend/api/chat.py                  ANALYTIC routes call the controller; flag flips to 1
frontend/src/components/Chat.tsx     render multi-table analytic turns with existing
                                     ResultTable; SSE steps plan/task in the stepper
```

**Config added:** `ANALYTIC_MAX_TASKS=6`, `ANALYTIC_MAX_REPAIRS_PER_TASK=1`,
`ANALYTIC_TOTAL_BUDGET_SEC=120`; `ANALYTIC_ENABLED` (introduced in Phase 12) flips to `1`.

**Tasks**

```text
[x] Planner prompt: compact AnalyticContext serialization, seed injection, date window,
    mode_downgrade instruction, JSON-only contract
[x] 🔒 Validation ladder: parse → structure → per-task 6-layer validation → dedupe →
    retry-with-errors → fallback pack
[x] Fallback packs: placeholder substitution ({date_from}, {dimension_column},
    {entity_filter}), metric-formula task synthesis, validator gate
[x] Task runner: ≤1 repair/task, budget stop, failed-evidence records
[x] mode_downgrade → re-enter normal pipeline with loaded memory
[x] SSE task events {"task_index","task_total","title"}; Chat.tsx interim rendering
```

**Done when**

```text
- "Phân tích doanh thu tháng 5" runs 3–6 validated tasks and renders their tables
- Sabotaging the planner (test hook returns garbage) still yields a complete
  fallback-pack review
- "Phân tích doanh thu tháng 3" when it's a simple lookup downgrades to NORMAL_SQL
- Flow B (previous-result seed) generates entity-scoped tasks
```

**Test plan:** `test_planner_validation.py` (fixture JSONs: valid / malformed / unknown
tables / dupes), `test_fallback_packs.py` (LLM disabled → complete valid pack for each
seeded playbook); manual e2e checklist against live llama.cpp.

---

## Phase 14 — Profiler + Evidence Store + Chart Specs ✅ DONE

**Goal.** Convert raw task rows into compact profiled evidence with hard provenance
(`source_type`), persist reviews + evidence (plan.md §15, §20.1), and emit deterministic
chart specs (plan.md §17).

> **As built.** `analysis/profiler.py` (pure functions) profiles each `expected_shape`:
> kpi (current/previous/absolute_change/pct_change/trend), by_dimension (per-row change, top
> ±contributors, top-3 concentration, biggest mover, single-period ranking), trend
> (direction, best/worst period, last-vs-first), top_n (leader share, gap to #2), each with a
> `warnings` list (empty/no-baseline/all-null). `analysis/evidence.py` builds `EvidenceItem`s
> with a hard `source_type="sql"` column, rows capped at `ANALYTIC_EVIDENCE_MAX_ROWS`, and a
> `profile_sentence` VN renderer reused by the interim report. `analysis/chart_planner.py`
> maps each evidence `kind` to a `chart_rule` entry (hot-reloaded, owner-editable) → chart
> spec, honoring `max_categories`/`min_rows` and `ANALYTIC_CHART_MAX_POINTS`, linking each
> chart back to its evidence; `chart_type:"none"`/too-few-rows yields no chart (table still
> ships). `analysis/review_store.py` adds `reviews` + `evidence` + `research_cache` tables to
> conversations.db with save/get/list/last CRUD (charts stored on their evidence rows).
> `Turn.review_id` added (model + db migration + store round-trip). The controller wires
> stages 4/6/9 (profile per task → progressive `{"type":"evidence"}` events, chart specs →
> `{"type":"chart"}`, persist review + link the saved turn) and assembles a deterministic
> interim report (the LLM writer lands in Phase 15). `api/analysis.py` adds
> `GET /api/reviews/{id}` and `GET /api/conversations/{id}/reviews` (new `reviews_router`
> registered in `app.py`). Verified by `test_profiler.py`, `test_chart_planner.py`,
> `test_review_store.py`, plus the controller/integration e2e tests.

**New & modified files**

```text
backend/analysis/profiler.py     pure profile functions per expected_shape (§15.1)
backend/analysis/evidence.py     evidence item construction (§15.2) + VN profile sentence
backend/analysis/chart_planner.py    chart specs from chart_rule entries (§17.1–17.2)
backend/analysis/review_store.py     reviews/evidence/research_cache DDL (§20.1) + CRUD
backend/memory/models.py + db.py + store.py  Turn.review_id nullable link (migration)
backend/analysis/controller.py       stages 4/6/9 wired; SSE steps profile/charts/save
                                     + event types evidence/chart
backend/api/analysis.py + app.py     + GET /api/reviews/{id}, GET /api/conversations/{id}/reviews
frontend/src/types.ts + Chat.tsx     EvidenceItem/ChartSpec types; evidence table rendering
```

**Config added:** `ANALYTIC_EVIDENCE_MAX_ROWS=20`, `ANALYTIC_CHART_MAX_POINTS=50`.

**Tasks**

```text
[x] Profile functions: kpi delta/pct, contributors + shares + concentration,
    trend direction, ranking; warnings (empty, div-zero, incomplete period)
[x] Evidence items with source_type="sql", rows capped at 20
[x] reviews/evidence/research_cache tables + Turn.review_id migration
[x] Chart planner decision table driven by chart_rule entries; ≤50 points
[x] SSE {"type":"evidence"} pushed per completed task; {"type":"chart"} after planning
[x] Review read endpoints for re-rendering old conversations
```

**Done when**

```text
- A review persists to reviews + evidence and is retrievable via GET /api/reviews/{id}
- Profiles carry correct deltas/contributors for fixture result sets
- Chart specs follow the seeded chart_rules; editing a chart_rule entry changes the
  next review's chart with no restart
```

**Test plan:** `test_profiler.py` (fixture rows → expected profiles),
`test_chart_planner.py` (shape/profile → expected spec incl. none-cases),
`test_review_store.py` (round-trip).

---

## Phase 15 — Writer + Advisor + Follow-up (analytic MVP complete, offline) ⭐ NEXT

**Goal.** Ship the full analyst-style report (plan.md §19), deterministic interpretation
and improvement advice (plan.md §18), and follow-up answering from stored evidence
(plan.md §9). After this phase the analytic MVP (flows A/B/C, plan.md §27) is complete —
entirely offline, no web dependency.

**New & modified files**

```text
backend/analysis/advisor.py      interpretation/improvement rule matching (§18)
backend/analysis/writer.py       LLM call 2 (streamed) + skeleton fallback (§19.4)
backend/llm/review_prompts.py    + writer prompt (§19.2) and follow-up prompt (§9)
backend/analysis/followup.py     follow-up answerer + keyword fallback + no-LLM specials
backend/analysis/mode_detector.py    FOLLOWUP window rule active (needs reviews to exist)
backend/analysis/controller.py   v2: full stage order, budget pressure (skip research →
                                 truncate tasks), status complete|degraded, final SSE payload
frontend/src/components/Chat.tsx     render report_markdown (plain <pre> until Phase 16),
                                     follow-up suggestion chips
```

**Tasks**

```text
[ ] Advisor: decomposition-movement + concentration matching → bullets from playbook
    rules and metric interpretations
[ ] Writer prompt + streaming; skeleton fallback assembly
[ ] Follow-up: evidence-only LLM answer, needs_new_analysis escalation, keyword
    fallback, no-LLM specials (show SQL / show evidence / show chart)
[ ] Controller v2 with wall-clock budget and degradation statuses
[ ] final SSE payload: report_markdown, evidence, charts, follow_up_suggestions, caveats
```

**Done when**

```text
- Flow A: "Phân tích vì sao doanh thu tháng 3/2025 giảm?" → full 8-section report
- Flow B: top-10 → "phân tích sâu khách hàng top 1" → scoped report
- Flow C: "vì sao khu vực X giảm mạnh nhất?" answered from stored evidence, no new SQL;
  "cho xem SQL đã dùng" renders the stored task SQL without any LLM call
- Killing the LLM mid-review still returns the skeleton report with real tables
```

**Test plan:** `test_writer_fallback.py`, `test_followup_fallback.py`, `test_advisor.py`
(profile fixtures → expected bullets); manual A/B/C script against live llama.cpp.

---

## Phase 16 — Frontend: Analytic Report UI + Visualization + KB UX

**Goal.** Production-quality Vietnamese-first rendering (plan.md §23): formatted reports
with charts, progressive task progress, error isolation — and the easy-to-use KB editor
(structured playbook forms, templates, history, dry-run).

**New & modified files**

```text
frontend/package.json            + recharts, react-markdown, remark-gfm (the only new deps)
frontend/src/i18n.ts             (new) centralized VN label dictionary
frontend/src/components/AnalyticReport.tsx   markdown + interleaved tables/charts/sources
frontend/src/components/EvidenceTable.tsx    ResultTable styling + CSV + task status
frontend/src/components/ChartRenderer.tsx    recharts: grouped_bar/line/horizontal_bar/stacked_bar
frontend/src/components/ReviewProgress.tsx   task stepper from SSE task/research/write events
frontend/src/components/SourcesList.tsx      [n] → title/url/retrieved_at (data from Phase 17)
frontend/src/components/ErrorBoundary.tsx    per-tab + per-report
frontend/src/components/ResultTable.tsx      client-side pagination (50/page)
frontend/src/components/EntryForm.tsx        structured playbook editor: step list
                                             add/remove/reorder, metric/dimension dropdowns,
                                             "Tạo từ mẫu" templates
frontend/src/components/EntryList.tsx        history viewer polish
frontend/src/App.tsx, Chat.tsx, api.ts, types.ts   wiring + label migration to i18n.ts
```

**Tasks**

```text
[ ] AnalyticReport with react-markdown + section-anchored EvidenceTable/ChartRenderer
[ ] ChartRenderer for the 4 chart types, VND tick formatting, tooltips, legends
[ ] ReviewProgress progressive stepper; evidence tables appear as SSE events arrive
[ ] ErrorBoundary per tab and per report
[ ] ResultTable pagination replacing the silent 50-row cap
[ ] Reopen old conversation → reports re-render from GET /api/reviews/*
[ ] Playbook structured editor + create-from-template + "Chạy thử playbook" dry-run
    button (POST /api/analysis/plan)
[ ] Migrate all hardcoded labels to i18n.ts (Vietnamese-first)
```

**Done when**

```text
- Flow A renders: formatted report, ≥1 recharts chart, progressive evidence tables,
  follow-up chips — all labels Vietnamese
- A render error in one report shows an inline error card, not a blank app
- A non-technical user can duplicate a playbook from a template, edit steps in the
  structured form, dry-run it, and see it used by the next analysis
- npm run build passes; the built dist is served by scripts/start.ps1
```

**Test plan:** manual UX checklist (desktop + narrow window); vite build in CI script;
component smoke tests optional (explicitly not over-engineered).

---

## Phase 17 — Web Research via SearxNG (native tool-calling) (fulfils requirement: web-enriched analytics)

**Goal.** Enrich analytic reports with competitor/market/industry context via direct SearxNG
search driven by native OpenAI function-calling (plan.md §16): the backend hands the model one
`search_internet` tool, makes a **single** web-search-planner call (seeded with the executed
SQL findings) that emits search tool calls, executes each call against SearxNG (≤5 calls),
builds hard-provenance web evidence from the structured results, and passes `web_context` to the
writer — **the research model is not re-invoked**. Degrades to the full offline report when
search is off or down. This is the 2nd of the 3 LLM calls a web-enriched review makes
(planner → web-search planner → writer). No MCP.

**New & modified files**

```text
backend/requirements.txt         (no new dep — httpx already present; `mcp` NOT added)
backend/tools/__init__.py        (new)
backend/tools/search_internet.py (new) SearxNG call (httpx, SEARCH_* config, logger);
                                 returns SearchResult(text, results[]); never raises
backend/tools/registry.py        (new) SEARCH_TOOLS_SCHEMA + name→callable dispatch + strict
                                 arg validation (query non-empty str ≤200 chars)
backend/tools/cache.py           (new) research_cache (table from Phase 14), 24h TTL, normalized-query key
backend/analysis/research.py     (new) single-shot web-search planner: one LLM call emits
                                 tool_calls → backend executes (≤5) → source_type="web" evidence
                                 → web_context; NO re-invocation; offline skip
backend/llm/client.py            add tools/tool_choice params to chat(); parse
                                 message.tool_calls into LlmResult.tool_calls; still never raises
backend/llm/review_prompts.py    research-stage system+user prompt; writer web_context rules (§19.2)
backend/analysis/controller.py   research stage wiring (stage 5) + budget interplay
backend/api/analysis.py          + POST /api/research/test (run one query, return normalized results)
backend/api/health.py            rename existing mcp block → search block (SEARCH_ENABLED + SearxNG probe)
frontend/src/components/SourcesList.tsx   real rendering; StatusBar search light
```

**Config added:** `SEARCH_ENABLED=0`, `SEARXNG_URL=http://192.168.0.190:30192` (accepts legacy
`DATAMIND_SEARXNG_URL` alias), `SEARCH_TIMEOUT_SEC=10`, `SEARCH_MAX_RESULTS=5`,
`SEARCH_MAX_SNIPPET_CHARS=500`, `SEARCH_LANGUAGE=vi`, `SEARCH_MAX_CALLS_PER_REVIEW=5`,
`SEARCH_MAX_SOURCES_PER_QUERY=3`, `SEARCH_MAX_QUERY_CHARS=200`, `SEARCH_CACHE_TTL_HOURS=24`
(all default off/safe; flip `SEARCH_ENABLED=1` after live verification). MCP_* keys removed.

**Tasks**

```text
[ ] search_internet: port the reference impl to httpx + config + logger; return
    SearchResult(text, results[]); never raises; /search suffix + score-sort + truncation
[ ] registry: fixed OpenAI tools schema + dispatch + strict arg validation
[ ] cache: research_cache read/write, normalized-query key, 24h TTL
[ ] client.py: add tools/tool_choice params to chat(); parse tool_calls; still never raises
[ ] 🔒 research.py single-shot planner: seed prompt with SQL findings → ONE LLM call emits
    search tool_calls → backend validates + executes each (cache-first, ≤5) → build
    source_type="web" evidence from structured results → web_context. NO re-invocation of the model.
[ ] Writer integration: web_context separate key; "Bối cảnh thị trường" section with [n];
    web numbers never in database sections
[ ] Failure behavior: SEARCH_ENABLED=0 / SearxNG down / timeout / zero results / no tool calls
    → skip + SSE research skipped + one caveat line; SQL path never blocked
[ ] /api/research/test endpoint; health search block; SourcesList + StatusBar light
[ ] Document the llama.cpp --jinja requirement (Qwen tool templates) in README
```

**Done when**

```text
- SEARCH_ENABLED=1 + SearxNG up: a revenue-drop review includes "Bối cảnh thị trường"
  with ≥1 cited source, stored as web evidence and listed in SourcesList
- SEARCH_ENABLED=0 or SearxNG down: the identical question produces the full offline report
  with a one-line notice — never an error
- A malformed/hallucinated tool call is skipped without aborting the review; the remaining calls still run
- Repeated identical research query within 24h hits the cache (no SearxNG call)
- The model returns tool_calls in one response, the backend executes them and builds web evidence,
  and the writer produces the final report from web_context — the research model is not
  re-invoked (verified in test_research_planner)
```

**Test plan:** mocked-SearxNG units: `test_search_internet.py` (JSON→SearchResult: score-sort,
truncation, zero-results, timeout, exception), `test_research_cache.py`, `test_research_planner.py`
(fake LLM emits tool_calls in one response → asserts backend executes them, evidence built,
≤5 calls honored, malformed-call skipped, model NOT re-invoked, no-tool-calls → skip),
`test_research_degradation.py` (unreachable / timeout / zero results → skip + caveat, SQL path
intact); manual live test against the user's SearxNG; `POST /api/research/test` smoke.

---

## Phase 18 — Hardening + Golden Evaluation + Docs

**Goal.** Lock in quality: complete the test suite, add the golden question evaluation,
finish operational docs. Definition of production-ready for the single-user target.

**New & modified files**

```text
backend/tests/                   completion sweep to the full list in plan.md §25.5
golden/golden_questions.jsonl    (new) ~30 normal VN questions (expected tables /
                                 non-empty flags) + ~10 analytic (expected mode,
                                 playbook, task-count bounds)
scripts/golden_eval.py           (new) run the set, print pass/fail table;
                                 LLM-dependent assertions skippable offline
scripts/smoke.ps1                (new) start app → health → 3 chat turns → 1 review → exit code
README.md                        rewrite: quickstart, architecture pointer, ops
                                 (health, logs, .env), llama.cpp --jinja + SearxNG setup notes
.env.example                     final audit: every config.py key present
```

**Tasks**

```text
[ ] pytest suite green with EMBEDDER=hashing (no GPU required)
[ ] Golden question set authored with a domain pass over VN phrasing
[ ] golden_eval.py with per-question diagnosis output
[ ] smoke.ps1 end-to-end gate
[ ] README + config audit; log-rotation note (LOG_FILE by size/date)
```

**Done when**

```text
- pytest green offline; golden eval ≥90% on normal questions; analytic goldens produce
  valid (complete or degraded, never failed) reports
- Fresh clone → first answer in ≤3 documented commands
- smoke.ps1 exit code gates any future change
```

**Test plan:** the phase IS the test plan; final manual run of plan.md §27 flows A/B/C +
KB flow + research flow.

---

## Later / V2 (not scheduled)

```text
⬜ Multi-round review decider (evidence sufficiency → focused round 2, MAX_ROUNDS=2)
⬜ User-selectable review depth (quick 3 tasks / standard 5–6 / deep ≤12 multi-round)
⬜ Async task execution + repeated-task result caching
⬜ Scheduled value auto-sync from sales.db
⬜ Report export (PDF/DOCX)
⬜ Multi-user/auth layer (only if the deployment target changes)
```

---

## Core Principle

Do not treat analytic mode as "better SQL generation" — it is a business investigation
pipeline made of deterministic scaffolding:

```text
Editable knowledge (ONE system: schema + metrics + playbooks + caveats + chart rules)
↓
safe validated SQL tasks            ← the LLM plans, the backend verifies everything
↓
profiled evidence (sql | web, provenance enforced structurally)
↓
tables and charts (deterministic specs)
↓
explanation + improvement advice (rules × numbers)
↓
report (the LLM narrates; its absence degrades quality, never availability)
↓
review memory (every claim answerable afterward)
```

The LLM proposes. The backend validates, executes, profiles, visualizes, remembers —
and now researches and hot-reloads its own knowledge.


