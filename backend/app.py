"""FastAPI app for the Knowledge Storage backend.

Startup builds a single KnowledgeService (loads the Qwen embedder + vector index)
and shares it with the routers. Run:

    uvicorn backend.app:app --port 8000          # from the SQLNEW directory

The React/Vite dev server proxies /api here.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import config
from backend.api import chat, entries, knowledge, retrieve, state
from backend.knowledge.service import KnowledgeService


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_embedder = os.environ.get("LOAD_EMBEDDER", "1").lower() in {"1", "true", "yes"}
    print(f"[app] building knowledge service (load_embedder={load_embedder}, embedder={config.EMBEDDER}) ...")
    svc = KnowledgeService.build(load_embedder=load_embedder)
    state.set_service(svc)
    # Phase 3: reuse the loaded embedder + index (no second model load).
    if svc.embedder is not None:
        from backend.retrieval.context_builder import RetrievalService
        state.set_retrieval_service(RetrievalService.from_knowledge_service(svc))
        print("[app] retrieval service ready.")
    else:
        print("[app] retrieval service NOT ready (no embedder; plumbing-only mode).")
    print(f"[app] ready. entries={svc.repo.counts_by_type()} index={len(svc.index)}")
    yield


app = FastAPI(title="SQLNEW Knowledge Storage", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local single-user admin tool
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(entries.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(retrieve.router, prefix="/api")
app.include_router(chat.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True, "dialect": config.SQL_DIALECT}
