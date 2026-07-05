"""Deep health check (Phase 9): GET /api/health.

Reports one block per subsystem — db, knowledge, index, embedder, llm, mcp — so the
frontend StatusBar can show traffic lights and an operator can diagnose a bad deploy in
one request. The LLM probe hits the network, so the whole result is cached for
``HEALTH_CACHE_SEC`` (default 30s). Every block is guarded: a broken subsystem reports
``ok: false`` instead of failing the endpoint.
"""
from __future__ import annotations

import sqlite3
import time

import httpx
from fastapi import APIRouter

from backend import config
from backend.api import state
from backend.common.logging import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)

_cache: dict | None = None
_cache_at: float = 0.0


def _check_db() -> dict:
    path = config.DB_PATH
    block = {"ok": False, "path": str(path)}
    if not path.exists():
        block["error"] = "database file not found"
        return block
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            con.execute("SELECT 1").fetchone()
        finally:
            con.close()
        block["ok"] = True
    except sqlite3.Error as exc:
        block["error"] = str(exc)
    return block


def _check_knowledge_index_embedder() -> tuple[dict, dict, dict]:
    knowledge = {"ok": False}
    index = {"ok": False}
    embedder = {"ok": False}
    try:
        svc = state.get_service()
    except Exception as exc:  # service not ready
        knowledge["error"] = index["error"] = embedder["error"] = str(exc)
        return knowledge, index, embedder

    try:
        by_type = svc.repo.counts_by_type()
        by_status = svc.repo.counts_by_status()
        knowledge = {
            "ok": True,
            "entries": sum(by_type.values()),
            "kb_version": svc.repo.get_kb_version(),
            "pending_embeds": int(by_status.get("pending", 0)),
            "embed_errors": int(by_status.get("error", 0)),
        }
    except Exception as exc:  # noqa: BLE001
        knowledge["error"] = str(exc)

    try:
        index = {"ok": True, "vectors": len(svc.index), "dim": svc.index.dim}
    except Exception as exc:  # noqa: BLE001
        index["error"] = str(exc)

    emb = svc.embedder
    embedder = {
        "ok": emb is not None,
        "model": getattr(emb, "model_name", None),
        "device": getattr(emb, "device", None),
        "dim": getattr(emb, "dim", None),
    }
    if emb is None:
        embedder["error"] = "no embedder loaded (plumbing-only mode)"
    return knowledge, index, embedder


def _check_llm() -> dict:
    base = config.LLM_BASE_URL
    block = {"reachable": False, "base_url": base, "model": None, "latency_ms": None}
    headers = {"Accept": "application/json"}
    if config.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"
    if config.LLM_NGROK_SKIP_WARNING:
        headers["ngrok-skip-browser-warning"] = "true"
    started = time.time()
    try:
        with httpx.Client(timeout=min(config.LLM_TIMEOUT, 8.0), follow_redirects=True) as c:
            r = c.get(f"{base}/models", headers=headers)
        block["latency_ms"] = int((time.time() - started) * 1000)
        text = (r.text or "").lstrip()
        if r.status_code == 200 and not (text.startswith("<") or text.startswith("<!DOCTYPE")):
            block["reachable"] = True
            try:
                items = (r.json() or {}).get("data") or []
                if items and isinstance(items[0], dict):
                    block["model"] = items[0].get("id")
            except ValueError:
                pass
        else:
            block["error"] = f"HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        block["latency_ms"] = int((time.time() - started) * 1000)
        block["error"] = f"{exc.__class__.__name__}: {exc}"
    if not block["model"]:
        block["model"] = config.LLM_MODEL or config.LLM_MODEL_FALLBACK
    return block


def _check_search() -> dict:
    """SearxNG web-research probe (Phase 17). ``reachable`` is None when search is disabled
    (a neutral light); when enabled, a short probe reports up/down. Never raises."""
    enabled = bool(config.SEARCH_ENABLED)
    url = config.SEARXNG_URL
    block = {"enabled": enabled, "reachable": None, "url": url}
    if not enabled:
        return block
    base = url.rstrip("/")
    probe = base if base.endswith("/search") else f"{base}/search"
    headers = {"Accept": "application/json"}
    if config.LLM_NGROK_SKIP_WARNING:
        headers["ngrok-skip-browser-warning"] = "true"
    try:
        with httpx.Client(timeout=min(config.SEARCH_TIMEOUT_SEC, 8.0), follow_redirects=True) as c:
            r = c.get(probe, params={"q": "ping", "format": "json"}, headers=headers)
        text = (r.text or "").lstrip()
        block["reachable"] = (r.status_code == 200
                              and not (text.startswith("<") or text.startswith("<!DOCTYPE")))
        if not block["reachable"]:
            block["error"] = f"HTTP {r.status_code}"
    except Exception as exc:  # noqa: BLE001 - health must never 500
        block["reachable"] = False
        block["error"] = f"{exc.__class__.__name__}: {exc}"
    return block


def _build_health() -> dict:
    knowledge, index, embedder = _check_knowledge_index_embedder()
    return {
        "db": _check_db(),
        "knowledge": knowledge,
        "index": index,
        "embedder": embedder,
        "llm": _check_llm(),
        "search": _check_search(),
        "dialect": config.SQL_DIALECT,
    }


@router.get("/health")
def health(fresh: bool = False):
    """Deep health, cached for HEALTH_CACHE_SEC. ``?fresh=1`` bypasses the cache."""
    global _cache, _cache_at
    now = time.monotonic()
    if not fresh and _cache is not None and (now - _cache_at) < config.HEALTH_CACHE_SEC:
        return {**_cache, "cached": True}
    try:
        _cache = _build_health()
    except Exception as exc:  # noqa: BLE001 - health must never 500
        log.exception("health check failed")
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
    _cache_at = now
    return {**_cache, "cached": False}
