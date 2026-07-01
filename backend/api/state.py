"""Holds the single KnowledgeService instance shared by the API routers.

The service (with the loaded embedder + vector index) is built once in the app
lifespan and stored here, so routers can depend on it without importing app.py
(which would be circular).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException

from backend.knowledge.service import KnowledgeService

if TYPE_CHECKING:  # avoid importing torch-touching modules at app import time
    from backend.retrieval.context_builder import RetrievalService

_service: Optional[KnowledgeService] = None
_retrieval: "Optional[RetrievalService]" = None


def set_service(svc: KnowledgeService) -> None:
    global _service
    _service = svc


def get_service() -> KnowledgeService:
    if _service is None:
        raise HTTPException(status_code=503, detail="knowledge service not ready")
    return _service


def set_retrieval_service(svc: "RetrievalService") -> None:
    global _retrieval
    _retrieval = svc


def get_retrieval_service() -> "RetrievalService":
    if _retrieval is None:
        raise HTTPException(status_code=503, detail="retrieval service not ready")
    return _retrieval
