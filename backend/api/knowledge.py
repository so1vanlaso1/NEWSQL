"""Knowledge-management endpoints: status, seed, skill.md, export, rebuild, schema meta."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.state import get_service
from backend.common import schema_def
from backend.ingestion import export_docs
from backend.knowledge import seed as seed_mod
from backend.knowledge import skill_builder
from backend.knowledge.service import KnowledgeService
from backend.store.models import ENTRY_TYPES

router = APIRouter(tags=["knowledge"])


@router.get("/status")
def status(svc: KnowledgeService = Depends(get_service)):
    return svc.status()


@router.get("/meta")
def meta():
    """Schema facts + type list to help the UI build forms and dropdowns."""
    tables = []
    for name in schema_def.all_table_names():
        t = schema_def.get_table(name)
        tables.append({
            "name": name,
            "primary_key": schema_def.primary_key(name),
            "columns": [{"name": c["name"], "type": c["type"]} for c in t["columns"]],
        })
    return {
        "entry_types": list(ENTRY_TYPES),
        "tables": tables,
        "foreign_keys": schema_def.all_foreign_keys(),
    }


@router.post("/seed")
def seed(reset: bool = False, embed: bool = True, svc: KnowledgeService = Depends(get_service)):
    return seed_mod.run(embed=embed, reset=reset, service=svc)


@router.get("/skill-md")
def get_skill_md(svc: KnowledgeService = Depends(get_service)):
    return {"markdown": skill_builder.render_skill_md(svc.repo)}


@router.post("/rebuild/skill-md")
def rebuild_skill_md(svc: KnowledgeService = Depends(get_service)):
    path = skill_builder.write_skill_md(repo=svc.repo)
    return {"path": str(path)}


@router.post("/export-docs")
def export_documents(svc: KnowledgeService = Depends(get_service)):
    return export_docs.export(repo=svc.repo)


@router.post("/rebuild/embeddings")
def rebuild_embeddings(svc: KnowledgeService = Depends(get_service)):
    return svc.reembed_all()
