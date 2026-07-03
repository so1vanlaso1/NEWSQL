"""Phase 3/4 debug endpoints.

``POST /api/retrieve``   -> run query-time retrieval, return the ResolvedContext.
``POST /api/chat/plan``  -> memory-aware planning preview (pre-LLM): the retrieval
                            plan, the compact memory window, and the resolved context
                            if the plan calls for retrieval. No LLM / no SQL execution
                            (those are phases 7-8); this is an inspection surface.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.api.state import get_retrieval_service
from backend.memory.intent_classifier import REFINE_PREVIOUS_QUERY
from backend.memory.memory_builder import build_compact_memory
from backend.memory.retrieval_planner import RetrievalPlan, build_retrieval_plan
from backend.memory.store import get_conversation_store
from backend.retrieval.context_builder import RetrievalService
from backend.retrieval.models import ResolvedContext
from backend.retrieval.skill_context import build_llm_skill_context

router = APIRouter(tags=["retrieval"])


class RetrieveRequest(BaseModel):
    query: str
    pinned_tables: list[str] = Field(default_factory=list)


class ChatPlanRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str


class ChatPlanResponse(BaseModel):
    conversation_id: str
    retrieval_plan: RetrievalPlan
    memory_window: str
    resolved_context: Optional[ResolvedContext] = None
    llm_skill_context: Optional[str] = None


@router.post("/retrieve", response_model=ResolvedContext)
def retrieve(req: RetrieveRequest, rsvc: RetrievalService = Depends(get_retrieval_service)):
    return rsvc.retrieve(req.query, req.pinned_tables)


@router.post("/chat/plan", response_model=ChatPlanResponse)
def chat_plan(req: ChatPlanRequest, rsvc: RetrievalService = Depends(get_retrieval_service)):
    rsvc.ensure_fresh()  # Phase 10: reflect KB edits in the plan preview too
    store = get_conversation_store()
    conversation_id = req.conversation_id or store.create()
    turns = store.load_recent(conversation_id)
    plan = build_retrieval_plan(req.message, turns)
    resolved = None
    if plan.needs_retrieval and plan.retrieval_query:
        resolved = rsvc.retrieve(plan.retrieval_query, plan.pinned_tables)

    memory_window = build_compact_memory(turns)
    # Only a refinement yields a meaningful pre-LLM standalone rewrite; otherwise the
    # authoritative standalone question is the LLM's job (Phase 7), so leave it unset.
    standalone = (plan.retrieval_query
                  if plan.intent_hint == REFINE_PREVIOUS_QUERY
                  and plan.retrieval_query and plan.retrieval_query != req.message
                  else None)
    llm_skill_context = build_llm_skill_context(
        user_message=req.message,
        memory_window=memory_window,
        resolved=resolved,
        standalone_question=standalone,
        rules=rsvc.global_rules,  # fallback so no-retrieval turns keep dialect + rules
    )
    return ChatPlanResponse(
        conversation_id=conversation_id,
        retrieval_plan=plan,
        memory_window=memory_window,
        resolved_context=resolved,
        llm_skill_context=llm_skill_context,
    )
