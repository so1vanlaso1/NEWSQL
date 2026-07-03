"""FastAPI app for the SQLNEW backend.

Startup builds a single KnowledgeService (loads the Qwen embedder + vector index),
shares it with the routers, and — when a built ``frontend/dist`` exists — serves the UI
from the same process so ``scripts/start.ps1`` is one command.

    uvicorn backend.app:app --port 8000          # from the SQLNEW directory

In dev the React/Vite server proxies /api here instead.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from backend import config
from backend.api import analysis, chat, conversations, entries, health, knowledge, retrieve, state
from backend.common.logging import get_logger, new_request_id, pop_request_id, push_request_id, setup_logging
from backend.knowledge.service import KnowledgeService

setup_logging()
log = get_logger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign/propagate an X-Request-ID and bind it to the logging context per request."""

    async def dispatch(self, request, call_next):
        rid = request.headers.get("X-Request-ID") or new_request_id()
        token = push_request_id(rid)
        try:
            response = await call_next(request)
        finally:
            pop_request_id(token)
        response.headers["X-Request-ID"] = rid
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_embedder = os.environ.get("LOAD_EMBEDDER", "1").lower() in {"1", "true", "yes"}
    log.info("building knowledge service (load_embedder=%s, embedder=%s) ...",
             load_embedder, config.EMBEDDER)
    svc = KnowledgeService.build(load_embedder=load_embedder)
    state.set_service(svc)
    # Phase 3: reuse the loaded embedder + index (no second model load).
    if svc.embedder is not None:
        from backend.retrieval.context_builder import RetrievalService
        state.set_retrieval_service(RetrievalService.from_knowledge_service(svc))
        log.info("retrieval service ready.")
    else:
        log.warning("retrieval service NOT ready (no embedder; plumbing-only mode).")
    # Phase 10 resilience: embed anything left pending (e.g. saved while the embedder
    # was down) so the index catches up on the next start.
    if svc.embedder is not None:
        try:
            pending = svc.repo.counts_by_status().get("pending", 0)
            if pending:
                log.info("embedding %d pending entr%s on startup ...",
                         pending, "y" if pending == 1 else "ies")
                svc.embed_pending()
        except Exception:  # noqa: BLE001 - startup must not crash on a retry
            log.exception("startup embed_pending failed")
    log.info("ready. entries=%s index=%d", svc.repo.counts_by_type(), len(svc.index))
    yield


app = FastAPI(title="SQLNEW", version="0.2.0", lifespan=lifespan)

app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local single-user admin tool
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(entries.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(retrieve.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(analysis.reviews_router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")

# Serve the built frontend from the same process when present (added last so the /api
# routes above always take precedence over the SPA catch-all).
_DIST = config.ROOT / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
    log.info("serving frontend from %s", _DIST)
else:
    log.info("no built frontend at %s (run the vite dev server, or npm run build)", _DIST)

    @app.get("/")
    def _root():
        return {"ok": True, "service": "SQLNEW", "ui": "not built — see scripts/dev.ps1"}
