"""Semantic retrieval over the shared vector index, bucketed per document type.

``IndexStore.search`` is a single global cosine ranking, so we retrieve the whole
ranked list once (the index is only a few hundred docs) and slice each type's
top-k afterwards -- this guarantees every type gets its budget even when schema
docs dominate the head of the ranking.
"""
from __future__ import annotations

from backend import config
from backend.embeddings.index_store import Hit, IndexStore


def default_topk() -> dict[str, int]:
    return {
        "table": config.RETRIEVAL_TOPK_TABLE,
        "column": config.RETRIEVAL_TOPK_COLUMN,
        "metric": config.RETRIEVAL_TOPK_METRIC,
        "join_path": config.RETRIEVAL_TOPK_JOIN_PATH,
        "value": config.RETRIEVAL_TOPK_VALUE,
    }


def analytic_topk() -> dict[str, int]:
    """Buckets for the analytic context builder (Phase 11/12): playbooks, caveats, and
    dimensions. Kept separate so a normal-SQL turn never pays for analytic retrieval."""
    return {
        "playbook": config.RETRIEVAL_TOPK_PLAYBOOK,
        "caveat": config.RETRIEVAL_TOPK_CAVEAT,
        "dimension": config.RETRIEVAL_TOPK_DIMENSION,
    }


def retrieve_buckets(embedder, index: IndexStore, query_text: str,
                     topk: dict[str, int] | None = None) -> dict[str, list[Hit]]:
    topk = topk or default_topk()
    buckets: dict[str, list[Hit]] = {t: [] for t in topk}
    if embedder is None:
        raise RuntimeError("Retrieval needs the Qwen embedder (started in plumbing-only mode).")
    if len(index) == 0:
        return buckets
    qv = embedder.encode([query_text], is_query=True)[0]
    for h in index.search(qv, k=len(index)):  # global ranked list; bucket after
        t = str(h.metadata.get("type", ""))
        bucket = buckets.get(t)
        if bucket is not None and len(bucket) < topk[t]:
            bucket.append(h)
    return buckets
