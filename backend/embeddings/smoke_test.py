"""Smoke test: prove the reused Qwen3-Embedding-4B loads and encodes on the RTX 2050.

Run:  python -m backend.embeddings.smoke_test
Env:  EMBED_BATCH_SIZE=2 python -m backend.embeddings.smoke_test   # if CUDA OOM

It loads the embedder, encodes one document and one (instruction-prefixed) query,
asserts the dimension, and prints the cosine of a matching pair vs an unrelated
pair. No vector index is built.
"""
from __future__ import annotations

import numpy as np

from backend import config
from backend.embeddings.embedder import get_embedder


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a.reshape(-1), b.reshape(-1)))  # vectors are L2-normalized


def main() -> int:
    print(f"[smoke] EMBEDDER={config.EMBEDDER} MODEL={config.EMBED_MODEL} "
          f"4bit={config.EMBED_LOAD_IN_4BIT} device={config.EMBED_DEVICE} batch={config.EMBED_BATCH_SIZE}")
    emb = get_embedder()
    print(f"[smoke] loaded embedder: {emb.model_name} dim={emb.dim} device={getattr(emb, 'device', 'n/a')}")

    doc_relevant = (
        "TYPE: metric METRIC: doanh_thu ALIASES: doanh thu, doanh so, sales, revenue. "
        "FORMULA: SUM(chi_tiet_don_hang_ban.thanh_tien). "
        "USE_WHEN: user asks about revenue, sales amount, total money from orders."
    )
    doc_unrelated = (
        "TYPE: table TABLE: vi_tri MEANING: geographic location province district ward "
        "with latitude and longitude coordinates."
    )
    query = "Top 10 khach hang co doanh thu cao nhat"

    dvecs = emb.encode([doc_relevant, doc_unrelated], is_query=False)
    qvec = emb.encode([query], is_query=True)[0]

    print(f"[smoke] doc matrix shape={dvecs.shape} query shape={qvec.shape} dtype={qvec.dtype}")
    cos_rel = _cos(qvec, dvecs[0])
    cos_unrel = _cos(qvec, dvecs[1])
    print(f"[smoke] cosine(query, revenue-metric doc) = {cos_rel:.4f}")
    print(f"[smoke] cosine(query, unrelated location doc) = {cos_unrel:.4f}")

    ok = True
    if emb.dim != 2560:
        print(f"[smoke] WARNING: dim {emb.dim} != 2560 (are you on the hashing fallback?)")
        ok = ok and config.EMBEDDER != "st"
    if cos_rel <= cos_unrel:
        print("[smoke] WARNING: relevant doc did not outrank the unrelated doc.")
        ok = False
    print("[smoke] PASS" if ok else "[smoke] CHECK OUTPUT ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
