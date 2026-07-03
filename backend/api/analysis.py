"""Analytic testers + review read endpoints (plan §22.1).

``POST /api/analysis/plan`` — inspection surface: given a message, show the detected mode,
the resolved review seed (for previous-result references), and the assembled AnalyticContext.
``GET  /api/reviews/{review_id}`` — a persisted review + evidence + charts (re-render on
reopen). ``GET /api/conversations/{id}/reviews`` — the review list for a conversation.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.analysis import context_builder, mode_detector, review_target_resolver
from backend.analysis.models import AnalyticContext, ReviewRecord, ReviewSeed
from backend.analysis.review_store import get_review_store
from backend.api.state import get_retrieval_service
from backend.memory.store import get_conversation_store
from backend.retrieval.context_builder import RetrievalService

router = APIRouter(prefix="/analysis", tags=["analysis"])
reviews_router = APIRouter(tags=["reviews"])


class AnalysisPlanRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str


class AnalysisPlanResponse(BaseModel):
    conversation_id: str
    mode: str
    review_seed: Optional[ReviewSeed] = None
    analytic_context: Optional[AnalyticContext] = None
    note: str = ""


@router.post("/plan", response_model=AnalysisPlanResponse)
def analysis_plan(req: AnalysisPlanRequest, rsvc: RetrievalService = Depends(get_retrieval_service)):
    rsvc.ensure_fresh()
    store = get_conversation_store()
    conversation_id = req.conversation_id or store.create()
    turns = store.load_recent(conversation_id)

    # Review storage ships in Phase 14, so last_review is None here — the FOLLOWUP branch
    # stays dormant, exactly as designed for Phase 12.
    mode = mode_detector.detect_mode(req.message, turns)

    seed: Optional[ReviewSeed] = None
    if mode == mode_detector.ANALYTIC_FROM_PREVIOUS_RESULT:
        seed = review_target_resolver.resolve(req.message, turns)
        if not seed.ok:
            return AnalysisPlanResponse(
                conversation_id=conversation_id, mode=mode, review_seed=seed,
                note="Không phân giải được thực thể từ kết quả trước đó.")

    if mode in (mode_detector.ANALYTIC_MODE, mode_detector.ANALYTIC_FROM_PREVIOUS_RESULT):
        query = context_builder.build_retrieval_query(req.message, seed)
        pinned = list(seed.base_tables) if (seed and seed.ok) else []
        ctx = context_builder.build_analytic_context(
            rsvc, req.message, mode=mode, retrieval_query=query,
            pinned_tables=pinned, review_seed=seed, recent_turns=turns)
        return AnalysisPlanResponse(
            conversation_id=conversation_id, mode=mode, review_seed=seed, analytic_context=ctx)

    note = ("Câu hỏi này sẽ chạy pipeline SQL thường."
            if mode == mode_detector.NORMAL_SQL
            else "Câu hỏi tiếp nối một phân tích trước (sẽ xử lý ở Phase 15).")
    return AnalysisPlanResponse(conversation_id=conversation_id, mode=mode, note=note)


# ---- review read endpoints (plan §20.1, §22.1) -----------------------------
@reviews_router.get("/reviews/{review_id}", response_model=ReviewRecord)
def get_review(review_id: str):
    review = get_review_store().get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    return review


@reviews_router.get("/conversations/{conversation_id}/reviews")
def list_conversation_reviews(conversation_id: str):
    return {"conversation_id": conversation_id,
            "reviews": get_review_store().list_reviews(conversation_id)}
