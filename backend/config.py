"""Central configuration for the SQLNEW Knowledge Storage backend.

Everything is overridable via environment variables / a `.env` file at the SQLNEW
root. The embedding block mirrors the old pipeline's defaults verbatim (already
"tuned for a 4 GB card (RTX 2050)").
"""
from __future__ import annotations

import os
import re
from pathlib import Path


# ---- Optional .env loading (no hard dependency on python-dotenv) -------------
def _load_dotenv() -> None:
    # backend/config.py -> SQLNEW/ is two parents up.
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Strip an inline comment ("  # ...") on unquoted values so a copied
        # .env.example line like `EMBED_BATCH_SIZE=8  # note` parses as "8".
        if not (val.startswith('"') or val.startswith("'")):
            val = re.sub(r"\s+#.*$", "", val).strip()
        val = val.strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv()


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes"}


# ---- Paths ------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent          # D:\SQL\SQLNEW
_REPO_PARENT = ROOT.parent                              # D:\SQL

# The knowledge/skill artifacts live under SQLNEW/skills/sales.
SKILL_PKG_DIR = Path(os.environ.get("SKILL_PKG_DIR", ROOT / "skills" / "sales"))
KNOWLEDGE_DB_PATH = Path(os.environ.get("KNOWLEDGE_DB_PATH", SKILL_PKG_DIR / "knowledge.db"))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", SKILL_PKG_DIR / "index"))
SKILL_MD_PATH = Path(os.environ.get("SKILL_MD_PATH", SKILL_PKG_DIR / "skill.md"))
SCHEMA_SNAPSHOT_PATH = Path(os.environ.get("SCHEMA_SNAPSHOT_PATH", SKILL_PKG_DIR / "schema_snapshot.json"))
EMBEDDING_DOCS_PATH = Path(os.environ.get("EMBEDDING_DOCS_PATH", SKILL_PKG_DIR / "embedding_docs.jsonl"))
METADATA_PATH = Path(os.environ.get("METADATA_PATH", SKILL_PKG_DIR / "metadata.json"))

# Source SQLite database (read-only). A copy is bundled inside this repo at
# ``SQLNEW/data/sales.db`` so a fresh clone is fully self-contained. If that copy is
# absent (e.g. someone deleted it during local dev) we fall back to the sibling
# old-pipeline DB. Override either with the DB_PATH env var.
_BUNDLED_DB = ROOT / "data" / "sales.db"
_SIBLING_DB = _REPO_PARENT / "SQL" / "data" / "sales.db"
_db_env = os.environ.get("DB_PATH")
DB_PATH = Path(_db_env) if _db_env else (_BUNDLED_DB if _BUNDLED_DB.exists() else _SIBLING_DB)

SKILL_PKG_DIR.mkdir(parents=True, exist_ok=True)
INDEX_DIR.mkdir(parents=True, exist_ok=True)

# ---- SQL dialect ------------------------------------------------------------
SQL_DIALECT = os.environ.get("SQL_DIALECT", "sqlite").lower()

# The synthetic sales.db only has data in this window; documented so relative-date
# questions ("this month") are not silently empty against date('now').
DATA_MIN_DATE = os.environ.get("DATA_MIN_DATE", "2024-01-01")
DATA_MAX_DATE = os.environ.get("DATA_MAX_DATE", "2025-06-24")

# ---- Embedding model (ported from the old pipeline) -------------------------
# "st"      -> force sentence-transformers (errors out if unavailable)
# "auto"    -> try sentence-transformers, else fall back to hashing (dim won't match!)
# "hashing" -> deterministic dependency-free fallback (UI/dev only)
EMBEDDER = os.environ.get("EMBEDDER", "st").lower()
EMBED_MODEL = os.environ.get("EMBED_MODEL", "unsloth/Qwen3-Embedding-4B")
EMBED_DEVICE = os.environ.get("EMBED_DEVICE", "cuda").lower()
# Load in 4-bit (bitsandbytes NF4). Needs a CUDA GPU + bitsandbytes/accelerate.
EMBED_LOAD_IN_4BIT = _flag("EMBED_LOAD_IN_4BIT", "1")
# Peak encode VRAM is model weights + one batch of activations. 8 is the old
# default tuned for a 4 GB RTX 2050; drop to 4/2 if you hit CUDA out-of-memory.
EMBED_BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "8"))
# Qwen3-Embedding is instruction-aware: the query side is prefixed
# "Instruct: {EMBED_QUERY_INSTRUCTION}\nQuery:{query}" while documents are raw.
EMBED_QUERY_INSTRUCTION = os.environ.get(
    "EMBED_QUERY_INSTRUCTION",
    "Given a Vietnamese question about a sales database, retrieve the schema tables and "
    "columns needed to answer it",
)

# ---- Knowledge-build knobs --------------------------------------------------
# Max distinct values embedded per value-source column (avoid row-level explosion).
# Raised from 30 in Phase 10 so on-demand value sync (/api/knowledge/sync-values)
# pulls a fuller set of nameable entities.
VALUE_SAMPLE_LIMIT = int(os.environ.get("VALUE_SAMPLE_LIMIT", "200"))
# Rows shown in each table's "common values" section of skill.md.
COMMON_VALUE_LIMIT = int(os.environ.get("COMMON_VALUE_LIMIT", "5"))

# ---- Analytic mode (Phase 12+) ----------------------------------------------
# Master gate for the analytic pipeline. Flipped to 1 in Phase 13 now that the review
# controller (planner -> task runner -> profiler -> evidence/charts -> persist) ships.
# With it off, every detected analytic turn still falls through to the normal SQL
# pipeline, so normal chat behavior is unchanged.
ANALYTIC_ENABLED = _flag("ANALYTIC_ENABLED", "1")
# Review budgets (plan §14, §21.2). At most ANALYTIC_MAX_TASKS validated SQL tasks per
# review; each task may self-repair at most ANALYTIC_MAX_REPAIRS_PER_TASK times; the whole
# review is bounded by ANALYTIC_TOTAL_BUDGET_SEC wall-clock (remaining tasks are skipped
# with a caveat once exceeded). ANALYTIC_EVIDENCE_MAX_ROWS caps rows stored per evidence
# item (Phase 14) — the full result is never persisted.
ANALYTIC_MAX_TASKS = int(os.environ.get("ANALYTIC_MAX_TASKS", "6"))
ANALYTIC_MAX_REPAIRS_PER_TASK = int(os.environ.get("ANALYTIC_MAX_REPAIRS_PER_TASK", "1"))
ANALYTIC_TOTAL_BUDGET_SEC = float(os.environ.get("ANALYTIC_TOTAL_BUDGET_SEC", "120"))
ANALYTIC_EVIDENCE_MAX_ROWS = int(os.environ.get("ANALYTIC_EVIDENCE_MAX_ROWS", "20"))
# Hard cap on chart data points (plan §17.2). Only aggregated/profiled rows are charted.
ANALYTIC_CHART_MAX_POINTS = int(os.environ.get("ANALYTIC_CHART_MAX_POINTS", "50"))

# ---- Knowledge-base live updates (Phase 10) ---------------------------------
# After every save/delete, re-render skill.md + embedding_docs.jsonl so the rendered
# views always match knowledge.db (set 0 to defer to the manual /rebuild/* endpoints).
KB_AUTO_RENDER = _flag("KB_AUTO_RENDER", "1")
# Save-time entry validation: strict = reject invalid entries (422); warn = attach
# warnings but save; off = skip validation entirely.
KB_VALIDATE_ON_SAVE = os.environ.get("KB_VALIDATE_ON_SAVE", "strict").lower()

# ---- Query-time retrieval knobs (Phase 3) -----------------------------------
# The vector index search is global; results are bucketed per document type and
# capped to these top-k. Bigger buckets = more recall, more context to compress.
RETRIEVAL_TOPK_TABLE = int(os.environ.get("RETRIEVAL_TOPK_TABLE", "5"))
RETRIEVAL_TOPK_COLUMN = int(os.environ.get("RETRIEVAL_TOPK_COLUMN", "10"))
RETRIEVAL_TOPK_METRIC = int(os.environ.get("RETRIEVAL_TOPK_METRIC", "3"))
RETRIEVAL_TOPK_JOIN_PATH = int(os.environ.get("RETRIEVAL_TOPK_JOIN_PATH", "3"))
RETRIEVAL_TOPK_VALUE = int(os.environ.get("RETRIEVAL_TOPK_VALUE", "5"))
# Final table budget from the resolver (3-6 preferred). Bridge tables needed to
# connect the set are added afterward by the join expander, so core coverage is
# unaffected by this cap; it only trims tangential dimension tables.
RETRIEVAL_MAX_TABLES = int(os.environ.get("RETRIEVAL_MAX_TABLES", "6"))
# Max exact entity/value matches pinned from a single message.
RETRIEVAL_MAX_VALUE_MATCHES = int(os.environ.get("RETRIEVAL_MAX_VALUE_MATCHES", "5"))
# Analytic retrieval buckets (Phase 11): playbooks, caveats, dimensions. Same shared
# index + bucketed search; only consumed by the analytic context builder (§11.1).
RETRIEVAL_TOPK_PLAYBOOK = int(os.environ.get("RETRIEVAL_TOPK_PLAYBOOK", "2"))
RETRIEVAL_TOPK_CAVEAT = int(os.environ.get("RETRIEVAL_TOPK_CAVEAT", "3"))
RETRIEVAL_TOPK_DIMENSION = int(os.environ.get("RETRIEVAL_TOPK_DIMENSION", "4"))

# ---- Conversation memory knobs (Phase 4) ------------------------------------
# A SEPARATE SQLite file (never the read-only sales.db sys_chat_* tables).
CONV_DB_PATH = Path(os.environ.get("CONV_DB_PATH", SKILL_PKG_DIR / "conversations.db"))
# How many recent turns load_recent returns for the compact memory window.
MEMORY_RECENT_TURNS = int(os.environ.get("MEMORY_RECENT_TURNS", "6"))
# Rows kept in a turn's result preview (stored in memory + shown in §16 window).
RESULT_PREVIEW_ROWS = int(os.environ.get("RESULT_PREVIEW_ROWS", "5"))
# Rows persisted per SQL turn for RE-DISPLAY when an old conversation is reopened
# (kept separate from RESULT_PREVIEW_ROWS so the LLM memory window stays compact).
HISTORY_DISPLAY_ROWS = int(os.environ.get("HISTORY_DISPLAY_ROWS", "60"))

# ---- LLM skill-context knobs (Phase 6) --------------------------------------
# Caps for the compact context serialized for the single LLM call (design §42-43).
# Tables themselves are never dropped (join bridges are required); these bound the
# per-table columns, focus columns, metrics, aliases, and normalization lines so the
# prompt stays small. matched-value count reuses RETRIEVAL_MAX_VALUE_MATCHES above.
SKILL_CTX_MAX_COLUMNS_PER_TABLE = int(os.environ.get("SKILL_CTX_MAX_COLUMNS_PER_TABLE", "14"))
SKILL_CTX_MAX_FOCUS_COLUMNS = int(os.environ.get("SKILL_CTX_MAX_FOCUS_COLUMNS", "40"))
SKILL_CTX_MAX_METRICS = int(os.environ.get("SKILL_CTX_MAX_METRICS", "5"))
SKILL_CTX_MAX_ALIASES_PER_METRIC = int(os.environ.get("SKILL_CTX_MAX_ALIASES_PER_METRIC", "6"))
SKILL_CTX_MAX_NORMALIZATION_ITEMS = int(os.environ.get("SKILL_CTX_MAX_NORMALIZATION_ITEMS", "14"))
# Above this many tables, the context builder emits a "large context" note (no trim).
SKILL_CTX_TABLE_WARN = int(os.environ.get("SKILL_CTX_TABLE_WARN", "8"))

# ---- LLM (Phase 7 + 9) ------------------------------------------------------
# The remote LLM call(s) per turn. OpenAI-compatible /chat/completions. The default
# points at the local llama.cpp server; override via LLM_BASE_URL in .env.
LLM_BASE_URL = os.environ.get(
    "LLM_BASE_URL", "http://192.168.0.5:30187/v1"
).rstrip("/")
# Blank => auto-discover the served id via GET {base}/models (falls back to LLM_MODEL_FALLBACK).
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_MODEL_FALLBACK = os.environ.get("LLM_MODEL_FALLBACK", "default")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")  # optional; Authorization omitted if blank
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "120"))
# Per-call generation params (Phase 9). SQL/planner calls are deterministic + short; the
# analytic writer (Phase 15) is warmer + longer. The client applies these per call, so a
# single client instance serves both without global temperature/max_token drift.
LLM_TEMPERATURE_SQL = float(os.environ.get("LLM_TEMPERATURE_SQL", "0"))
LLM_MAX_TOKENS_SQL = int(os.environ.get("LLM_MAX_TOKENS_SQL", "1200"))
LLM_TEMPERATURE_WRITER = float(os.environ.get("LLM_TEMPERATURE_WRITER", "0.4"))
LLM_MAX_TOKENS_WRITER = int(os.environ.get("LLM_MAX_TOKENS_WRITER", "4000"))
# Legacy: send the ngrok interstitial-skip header. Harmless for llama.cpp; kept so an
# ngrok tunnel still works when LLM_BASE_URL is pointed at one.
LLM_NGROK_SKIP_WARNING = _flag("LLM_NGROK_SKIP_WARNING", "1")
# Try response_format=json_object first; fall back automatically if the server rejects it.
LLM_TRY_JSON_OBJECT = _flag("LLM_TRY_JSON_OBJECT", "1")
# On a validation/execution failure, do ONE repair round-trip (2nd call only on failure).
LLM_SELF_REPAIR = _flag("LLM_SELF_REPAIR", "1")
# Stream the model's tokens over SSE for the /api/chat/stream endpoint (falls back to a
# single blocking call automatically if the server rejects stream=true).
LLM_STREAM = _flag("LLM_STREAM", "1")

# ---- SQL validation + execution (Phase 8) -----------------------------------
# Hard fetch cap AND the ceiling any explicit LIMIT may not exceed.
MAX_RESULT_ROWS = int(os.environ.get("MAX_RESULT_ROWS", "500"))
# LIMIT ceiling specifically for raw (non-aggregate) row SELECTs.
RAW_SELECT_LIMIT = int(os.environ.get("RAW_SELECT_LIMIT", "100"))
# EXPLAIN QUERY PLAN scan above this many rows -> a WARNING only (the fact tables are
# small, so all-time aggregates legitimately scan; never a hard fail).
EXPLAIN_MAX_SCAN_ROWS = int(os.environ.get("EXPLAIN_MAX_SCAN_ROWS", "500000"))
# LIMIT auto-injected when a raw SELECT arrives without one (capped by MAX_RESULT_ROWS).
AUTO_LIMIT = int(os.environ.get("AUTO_LIMIT", "200"))
# Per-query wall-clock budget enforced via sqlite3 progress handler.
QUERY_TIMEOUT_SEC = float(os.environ.get("QUERY_TIMEOUT_SEC", "10"))

# ---- Logging (Phase 9) ------------------------------------------------------
# LOG_LEVEL   standard levels (DEBUG/INFO/WARNING/ERROR).
# LOG_FORMAT  console = human-readable; json = one JSON object per line (for LOG_FILE).
# LOG_FILE    relative to ROOT; blank disables the file handler (console only).
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.environ.get("LOG_FORMAT", "console").lower()
_log_file = os.environ.get("LOG_FILE", "logs/app.jsonl").strip()
LOG_FILE = (Path(_log_file) if Path(_log_file).is_absolute() else (ROOT / _log_file)) if _log_file else None

# ---- Health check (Phase 9) -------------------------------------------------
# GET /api/health is expensive (touches the LLM), so its result is cached this long.
HEALTH_CACHE_SEC = float(os.environ.get("HEALTH_CACHE_SEC", "30"))
