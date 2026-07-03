# SQLNEW — Conversational One-LLM SQL Pipeline

Implementation of the plan in `plan.md` (architecture + contracts) and `phased.md`
(build order): a
Vietnamese FMCG text-to-SQL pipeline that answers new questions **and** back-and-forth
follow-ups with **one LLM call per turn**. The knowledge lives in **SQLite
`knowledge.db`** (source of truth); a **React UI + FastAPI backend** manage it, and
**every save re-embeds just that entry** with the reused **Qwen3-Embedding-4B** model
on the local **RTX 2050 (4 GB)** and upserts a **live vector index**. `skill.md` and
`embedding_docs.jsonl` are regenerable views.

**Current status: Phases 1–8 complete — the full conversational chat works end to
end.** The offline knowledge base, the query-time retrieval stack, conversation
memory, heuristic intent + retrieval planning, the compact-context serializer, the
single LLM call, and SQL validation + execution are all wired into `POST /api/chat`
and the **Chat** UI tab (the default). A turn runs plan → retrieve → build context →
one LLM call → validate → (one self-repair on failure) → execute → summarize, and is
inspectable via `/api/retrieve` and `/api/chat/plan` (Retrieval Tester + **Chat Plan**
tabs). The only external service is the OpenAI-compatible LLM endpoint (`LLM_BASE_URL`).

## Implementation checklist (plan phases)

Legend: ✅ done · 🟡 partial · ⬜ not started

### ✅ Phase 1 — Database documentation (`skill.md`)
- [x] Knowledge store `knowledge.db` (source of truth) — `store/db.py`, `models.py`, `repository.py`
- [x] Rules, metrics, tables, columns, joins, values seeded — `knowledge/seed.py`, `business_meta.py`
- [x] `skill.md` rendered from the store — `knowledge/skill_builder.py`
- [x] Schema pulled from the live `sales.db` — `ingestion/schema_loader.py`, `common/schema_def.py`
- [x] React UI to create/edit/delete entries + preview `skill.md` — `frontend/`, `api/entries.py`, `api/knowledge.py`

### ✅ Phase 2 — Embedding document builder
- [x] Type-tagged docs (table/column/metric/join_path/value) — `knowledge/embedding_text.py`, `ingestion/export_docs.py`
- [x] `embedding_docs.jsonl` export — `ingestion/export_docs.py`
- [x] Qwen3-Embedding-4B (4-bit) embedder on RTX 2050 — `embeddings/embedder.py`, `smoke_test.py`
- [x] Live upsertable vector index (re-embed one entry per save) — `embeddings/index_store.py`

### ✅ Phase 3 — Vector index + query-time retrieval
- [x] Per-type bucketed vector retrieval (top-k per type) — `retrieval/vector_retriever.py`
- [x] Vietnamese normalize + expand of the query — `retrieval/query_expander.py`, `common/vn_text.py`
- [x] Exact value/entity pinning (không-dấu alias lookup) — `retrieval/value_matcher.py`
- [x] Table resolution (weighted, pinned/entity keeps, budget) — `retrieval/table_resolver.py`
- [x] Join expansion from FK graph + curated join_path docs — `retrieval/join_expander.py`
- [x] Always-on global rules (dialect, data window, metric policy) — `retrieval/rules_provider.py`
- [x] `RetrievalService.retrieve()` → structured `ResolvedContext` — `retrieval/context_builder.py`, `models.py`
- [x] Debug endpoint `POST /api/retrieve` + Retrieval Tester UI — `api/retrieve.py`, `frontend/src/components/RetrievalTester.tsx`

### ✅ Phase 4 — Conversation memory
- [x] Separate `conversations.db` (conversations + turns) — `memory/db.py`
- [x] `Turn` shape (question, SQL, tables/cols/metrics/filters, result preview, entities, summary) — `memory/models.py`
- [x] `ConversationStore` CRUD (save SQL / non-SQL turns, load recent) — `memory/store.py`
- [x] Compact memory window (design §16, last SQL turn only) — `memory/memory_builder.py`
- [x] Deterministic result summary + entity extraction (no LLM) — `memory/result_summarizer.py`

### ✅ Phase 5 — Intent + retrieval decision
- [x] Full heuristic intent classifier — all 7 intents (NEW / REFINE / ASK_SQL / ASK_RESULT / DRILL_DOWN / EXPLAIN / INSUFFICIENT), with a `reason` cue — `memory/intent_classifier.py`
- [x] Pre-LLM retrieval plan: needs_retrieval / retrieval_query / pinned_tables (design §39) — `memory/retrieval_planner.py`
- [x] Intent → retrieval-mode mapping (§19–20), incl. the drill-down query built from prior result entities + filters — `memory/retrieval_planner.py`
- [x] Follow-up + ask-SQL / ask-result cues — `memory/memory_builder.py`, `intent_classifier.py`
- [x] Preview endpoint `POST /api/chat/plan` (plan + memory window + resolved context + serialized skill context) — `api/retrieve.py`
- [x] Heuristic pre-LLM; the single LLM call (Phase 7) remains the **authoritative** classifier (`intent_hint` carries the heuristic guess)

### ✅ Phase 6 — Context builder (compact LLM skill context)
- [x] Structured facts assembled: tables, columns, metrics, joins, matched values, global rules — `retrieval/context_builder.py`, `models.py`
- [x] **`build_llm_skill_context()` text serializer** (design §27: DIALECT / GLOBAL RULES / DATA WINDOW / METRIC POLICY / NORMALIZATION / MEMORY / MESSAGE / METRIC RULES / TABLES / COLUMNS / JOINS / VALUES) — `retrieval/skill_context.py`
- [x] Renders the real **SQLite** dialect + rules (not the plan doc's stale MySQL example); rules fall back to always-on globals on no-retrieval turns
- [x] Context-size guardrails via `ContextLimits` (per-table column picker + caps on focus columns / metrics / aliases / normalization; oversized table sets flagged, never trimmed — join bridges are load-bearing) — `retrieval/skill_context.py`, `config.py` (`SKILL_CTX_*`)

### ✅ Phase 7 — One LLM call
- [x] `llm/client.py` — defensive OpenAI-compatible client (model auto-discovery, JSON mode w/ fallback, never raises)
- [x] `llm/prompt_builder.py` — assembles the design §30 prompt (SQLite-pinned) around the skill context
- [x] `llm/response_parser.py` — parses the structured JSON (intent, needs_sql, standalone_question, sql, answer_from_memory, memory_update)
- [x] Turn handler wired (design §38): plan → retrieve → build context → LLM → branch on needs_sql

### ✅ Phase 8 — SQL validation, execution, memory update
- [x] `validation/sql_validator.py` — SELECT/WITH-only, no DDL/DML, allow-list vs schema, diacritic-identifier reject, auto-LIMIT, read-only EXPLAIN (sqlglot)
- [x] `execution/query_runner.py` — read-only run against `sales.db`, progress-handler timeout, truncation, JSON-safe rows
- [x] Persists the executed turn (SQL + result preview + entities + summary) via `ConversationStore.save_sql_turn`
- [x] End-to-end chat endpoint (`POST /api/chat`) + **Chat** UI tab

### ✅ Phase 9 — Platform foundation
- [x] LLM config cleanup: real `LLM_BASE_URL` default + per-call `LLM_*_SQL`/`LLM_*_WRITER` params — `config.py`, `llm/client.py`
- [x] Structured logging (console + rotating JSON-lines file) + request-id middleware — `common/logging.py`, `app.py`
- [x] Deep, cached `GET /api/health` (db/knowledge/index/embedder/llm/mcp) — `api/health.py`
- [x] One-command start + dev scripts; static `frontend/dist` mount — `scripts/start.ps1`, `scripts/dev.ps1`, `app.py`

### ✅ Phase 10 — KB live updates (editable anytime, no restart)
- [x] `meta.kb_version` + `entry_history` + per-turn `ensure_fresh()` hot-reload — `store/db.py`, `store/repository.py`, `retrieval/context_builder.py`
- [x] Save-time validation (schema + SQL dialect), field-level 422s — `knowledge/entry_validator.py`, `api/entries.py`
- [x] History + restore, auto-render of skill.md/docs, embedder-down → pending — `knowledge/service.py`
- [x] `GET /api/kb/version`, `POST /api/knowledge/sync-values`, `POST /api/embed-pending`; UI history viewer + KB badge

> The offline knowledge app and the query-time retrieval/memory/planning stack are the
> foundation the remaining LLM + analytic phases consume.

## Layout
```
backend/
  common/        schema_def.py, vn_text.py          (copied from the old pipeline)
  store/         db.py, models.py, repository.py     (knowledge.db + CRUD + validation)
  knowledge/     business_meta.py, embedding_text.py, skill_builder.py, seed.py, service.py
  ingestion/     schema_loader.py, export_docs.py
  embeddings/    embedder.py, index_store.py, smoke_test.py   (reused Qwen 4-bit + upsertable index)
  retrieval/     vector_retriever, query_expander, value_matcher, table_resolver,   [Phase 3/6]
                 join_expander, rules_provider, context_builder, models,
                 skill_context (§27 serializer + ContextLimits), smoke_test,
                 skill_context_smoke_test
  memory/        db, models, store, memory_builder, retrieval_planner,              [Phase 4/5]
                 intent_classifier (7-intent heuristic), result_summarizer,
                 smoke_test   (separate conversations.db)
  llm/           client.py, prompt_builder.py, response_parser.py, smoke_test.py   [Phase 7]
  validation/    sql_validator.py                                                   [Phase 8]
  execution/     query_runner.py                                                    [Phase 8]
  api/           chat.py, entries.py, knowledge.py, retrieve.py, state.py
  app.py, config.py, requirements.txt
frontend/        Vite + React + TypeScript UI (Chat tab [default] + entries editor
                 + RetrievalTester + ChatPlanTester tabs)
data/            sales.db          (bundled read-only source database)
skills/sales/    committed: knowledge.db (source of truth), skill.md, schema_snapshot.json,
                 embedding_docs.jsonl, metadata.json, index/ (prebuilt vectors)
```

## Prerequisites
This repo is **self-contained** — the database (`data/sales.db`), the pre-seeded
knowledge base (`skills/sales/knowledge.db`), and the prebuilt vector index
(`skills/sales/index/`) are all committed, so there is nothing to build after cloning.
You need:
- **Python 3.10+** and, for the real semantic embedder, a **CUDA GPU** (developed on an
  RTX 2050 4 GB; the Qwen3-Embedding-4B model is downloaded from Hugging Face on first
  run and cached under `~/.cache/huggingface`). No GPU? set `EMBEDDER=hashing` in `.env`
  for a non-semantic dev fallback.
- **Node 18+** for the frontend.
- An **OpenAI-compatible LLM endpoint** for the chat turn — set `LLM_BASE_URL` in `.env`.

```powershell
# from the repo root, after cloning
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install a CUDA torch FIRST (GPU wheels), then the rest:
pip install torch --index-url https://download.pytorch.org/whl/cu128   # RTX 20xx+/CUDA 12.x
pip install -r backend\requirements.txt

cd frontend; npm install; cd ..

copy .env.example .env          # then set LLM_BASE_URL (and DB_PATH only if you moved the DB)
```

## Run

**One command (production):** serves the built UI *and* the API from one process.
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1     # http://localhost:8000/
```
`start.ps1` checks the venv, builds `frontend/dist` if missing, then runs uvicorn with the
SPA mounted. `scripts\dev.ps1` instead starts the API with autoreload and the Vite dev
server (hot reload) — open the Vite URL (http://localhost:5173/).

**Manual:**
```powershell
# from the repo root, with the venv active
$env:PYTHONPATH = "$PWD"; $env:PYTHONIOENCODING = "utf-8"

# 1) Start the backend (loads the embedder + the committed index once) ...
python -m uvicorn backend.app:app --port 8000
# 2) ... and the UI (proxies /api to :8000); the Chat tab is the default.
cd frontend; npm run dev        # http://localhost:5173
```

**Ops:** `GET /api/health` returns a deep, 30s-cached status (db / knowledge+kb_version /
index / embedder / llm / mcp) — the StatusBar reads it. Structured logs go to the console
and, when `LOG_FILE` is set, a rotating JSON-lines file (`LOG_LEVEL`, `LOG_FORMAT` in `.env`).
Every knowledge edit is **live on the next question, no restart** (a `kb_version` bump +
per-turn freshness check); invalid entries are rejected at save with a field-level message,
and every change is audited and restorable from the entry's **History**.

The knowledge base and index ship ready to use. You only need to rebuild them if you
**edit** the knowledge (also doable live from the UI top bar):
```powershell
python -m backend.knowledge.seed --reset      # reseed knowledge.db + re-embed (add --no-embed to skip the GPU)
python -m backend.knowledge.skill_builder     # re-render skill.md
python -m backend.ingestion.export_docs       # re-export embedding_docs.jsonl + metadata.json
```

In the UI: chat in the **Chat** tab (ask a question, get an answer + result table /
chart + collapsible SQL); browse/filter/search knowledge entries, edit a metric or
table and **Save & embed** (re-embeds just that entry), preview `skill.md`, use the
top bar to Seed / Rebuild embeddings / Write skill.md / Export docs, and use the
**Chat Plan** tab to inspect the classified intent, retrieval plan, memory window,
and the serialized LLM skill context for a message.

### Tests
```powershell
$env:PYTHONPATH = "$PWD"; $env:PYTHONIOENCODING = "utf-8"
# pytest suite (GPU-free: runs with EMBEDDER=hashing against temp DBs)
python -m pytest backend\tests -q
# GPU-free unit smoke tests
python -m backend.retrieval.skill_context_smoke_test   # Phase 6 serializer (synthetic context, guardrails)
python -m backend.memory.smoke_test                    # Phase 5 classifier + planner (all 7 intents)
python -m backend.llm.smoke_test                       # Phase 7/8 parser + validator + executor (offline)
# Live — needs the Qwen embedder + built index (prints each skill context)
python -m backend.retrieval.smoke_test
# Live full chat pipeline — needs a reachable LLM_BASE_URL; run with the uvicorn
# server STOPPED (a second embedder load OOMs a 4 GB card).
python -m backend.llm.smoke_test --live
```

## RTX 2050 (4 GB) notes
- Defaults reuse the old pipeline's config: `EMBED_LOAD_IN_4BIT=1`,
  `EMBED_BATCH_SIZE=8`, `EMBED_DEVICE=cuda`, model `unsloth/Qwen3-Embedding-4B`
  (2560-dim, cached under `~/.cache/huggingface`).
- On CUDA out-of-memory set `EMBED_BATCH_SIZE=4` or `2` (embedding is one short doc
  per save, so a small batch is fine).
- For UI/dev without a GPU set `EMBEDDER=hashing` (768-dim, non-semantic — rebuild
  the index if you switch back to `st`).

## Key facts encoded
- Dialect **SQLite**; date filters use `strftime`/`date('now')` (not MySQL `DATE_FORMAT`).
- Canonical `doanh_thu = SUM(chi_tiet_don_hang_ban.thanh_tien)` (net, after promo);
  gross `SUM(so_luong*don_gia)` and header `SUM(don_hang_ban.tong_tien)` documented.
- Real schema: `don_hang_ban` PK `don_hang_id`; city via `khach_hang.vi_tri_id → vi_tri.tinh_thanh`.
- Data window comes from the live DB (currently 2024-01-01 … 2025-06-21).
