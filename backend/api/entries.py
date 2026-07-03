"""CRUD endpoints for knowledge entries. Every create/update embeds synchronously."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.state import get_service
from backend.knowledge.service import KnowledgeService
from backend.store import models

router = APIRouter(prefix="/entries", tags=["entries"])


class RestoreRequest(BaseModel):
    history_id: int


def _out(entry: dict) -> models.EntryOut:
    return models.EntryOut(**entry)


def _save_result(result: dict) -> models.SaveResult:
    return models.SaveResult(
        entry=_out(result["entry"]),
        embedded=result["embedded"],
        embed_status=result["embed_status"],
        embed_error=result.get("embed_error", "") or "",
    )


@router.get("", response_model=list[models.EntryOut])
def list_entries(
    type: Optional[str] = None,
    q: Optional[str] = None,
    status: Optional[str] = None,
    svc: KnowledgeService = Depends(get_service),
):
    return [_out(e) for e in svc.repo.list(type_=type, query=q, status=status)]


# NOTE: these {id}/history and {id}/restore routes must be declared BEFORE the
# "/{entry_id:path}" catch-all below, or the path converter would swallow the suffix.
@router.get("/{entry_id:path}/history")
def entry_history(entry_id: str, svc: KnowledgeService = Depends(get_service)):
    """Audit trail for an entry (newest first). 404 only if the entry never existed and
    has no history."""
    history = svc.repo.list_history(entry_id)
    if not history and svc.repo.get(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"no history for: {entry_id}")
    return {"entry_id": entry_id, "history": history}


@router.post("/{entry_id:path}/restore", response_model=models.SaveResult)
def restore_entry(entry_id: str, payload: RestoreRequest,
                  svc: KnowledgeService = Depends(get_service)):
    try:
        result = svc.restore(entry_id, payload.history_id)
    except ValueError as exc:  # restored body fails current validation (strict mode)
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"history {payload.history_id} not found for entry {entry_id}")
    return _save_result(result)


@router.get("/{entry_id:path}", response_model=models.EntryOut)
def get_entry(entry_id: str, svc: KnowledgeService = Depends(get_service)):
    entry = svc.repo.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"entry not found: {entry_id}")
    return _out(entry)


@router.post("", response_model=models.SaveResult, status_code=201)
def create_entry(payload: models.EntryIn, svc: KnowledgeService = Depends(get_service)):
    try:
        result = svc.save(payload.type, payload.body, name=payload.name,
                          entry_id=payload.id, enabled=payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _save_result(result)


@router.put("/{entry_id:path}", response_model=models.SaveResult)
def update_entry(entry_id: str, payload: models.EntryIn, svc: KnowledgeService = Depends(get_service)):
    try:
        result = svc.save(payload.type, payload.body, name=payload.name,
                          entry_id=entry_id, enabled=payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _save_result(result)


@router.post("/{entry_id:path}/reembed", response_model=models.SaveResult)
def reembed_entry(entry_id: str, svc: KnowledgeService = Depends(get_service)):
    result = svc.reembed(entry_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"entry not found: {entry_id}")
    return _save_result(result)


@router.delete("/{entry_id:path}")
def delete_entry(entry_id: str, svc: KnowledgeService = Depends(get_service)):
    existed = svc.delete(entry_id)
    if not existed:
        raise HTTPException(status_code=404, detail=f"entry not found: {entry_id}")
    return {"deleted": True, "id": entry_id}
