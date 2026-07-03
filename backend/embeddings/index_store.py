"""Upsertable, persistent vector index (cosine over L2-normalized vectors).

Extends the old pipeline's numpy VectorStore with per-id ``upsert`` and ``delete`` so
the Knowledge Storage app can re-embed a single entry on save instead of rebuilding
the whole index. For a few-hundred-row knowledge base a numpy matrix is faster and
simpler than FAISS/Chroma, and the (add/upsert/delete/search/save/load) interface
mirrors a real vector DB so it can be swapped later.

Persistence: <INDEX_DIR>/vectors.npy + <INDEX_DIR>/meta.json
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from backend.common.logging import get_logger

log = get_logger(__name__)


@dataclass
class Hit:
    score: float
    doc_id: str
    document: str
    metadata: dict


@dataclass
class IndexStore:
    dim: int
    model_name: str = "unknown"
    ids: List[str] = field(default_factory=list)
    documents: List[str] = field(default_factory=list)
    metadatas: List[dict] = field(default_factory=list)
    _vectors: Optional[np.ndarray] = None
    _row_of: Dict[str, int] = field(default_factory=dict)
    # A single instance is SHARED between KnowledgeService (writes) and RetrievalService
    # (reads). FastAPI runs sync endpoints in a threadpool, so an entry edit can mutate
    # this index while a chat/retrieve turn reads it. The mutations reassign several
    # fields non-atomically (and numpy ops release the GIL), so search() reading mid-write
    # could crash or pair a score with the wrong doc. This re-entrant lock serializes the
    # whole read/write critical sections. (repr/compare excluded so dataclass stays sane.)
    _lock: "threading.RLock" = field(default_factory=threading.RLock, repr=False, compare=False)

    # ---- write ----
    def upsert(self, doc_id: str, vector: np.ndarray, document: str, metadata: dict) -> None:
        vec = np.asarray(vector, dtype=np.float32).reshape(-1)
        if vec.shape[0] != self.dim:
            raise ValueError(f"vector dim {vec.shape[0]} != index dim {self.dim}")
        with self._lock:
            if doc_id in self._row_of:
                row = self._row_of[doc_id]
                self._vectors[row] = vec
                self.documents[row] = document
                self.metadatas[row] = metadata
            else:
                row = len(self.ids)
                self.ids.append(doc_id)
                self.documents.append(document)
                self.metadatas.append(metadata)
                self._row_of[doc_id] = row
                self._vectors = (
                    vec[None, :] if self._vectors is None else np.vstack([self._vectors, vec[None, :]])
                )

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            row = self._row_of.get(doc_id)
            if row is None:
                return False
            keep = [i for i in range(len(self.ids)) if i != row]
            self.ids = [self.ids[i] for i in keep]
            self.documents = [self.documents[i] for i in keep]
            self.metadatas = [self.metadatas[i] for i in keep]
            self._vectors = self._vectors[keep] if self._vectors is not None and keep else (
                None if not keep else self._vectors
            )
            if not keep:
                self._vectors = None
            self._row_of = {doc: i for i, doc in enumerate(self.ids)}
            return True

    def contains(self, doc_id: str) -> bool:
        return doc_id in self._row_of

    def __len__(self) -> int:
        return len(self.ids)

    # ---- read ----
    def search(self, query_vector: np.ndarray, k: int = 10) -> List[Hit]:
        with self._lock:
            if self._vectors is None or not self.ids:
                return []
            q = np.asarray(query_vector, dtype=np.float32).reshape(-1)
            sims = self._vectors @ q  # normalized -> dot == cosine
            k = min(k, len(self.ids))
            top = np.argpartition(-sims, k - 1)[:k]
            top = top[np.argsort(-sims[top])]
            return [Hit(float(sims[i]), self.ids[i], self.documents[i], self.metadatas[i]) for i in top]

    def type_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for meta in self.metadatas:
            t = str(meta.get("type", "unknown"))
            counts[t] = counts.get(t, 0) + 1
        return counts

    # ---- persistence ----
    def save(self, index_dir: Path | None = None) -> Path:
        from backend import config

        index_dir = Path(index_dir or config.INDEX_DIR)
        index_dir.mkdir(parents=True, exist_ok=True)
        # Snapshot under the lock so a concurrent upsert/delete can't tear the persisted
        # vectors/ids/documents/metadatas out of sync with each other.
        with self._lock:
            vectors = (self._vectors.copy() if self._vectors is not None
                       else np.zeros((0, self.dim), np.float32))
            meta = {
                "dim": self.dim,
                "model_name": self.model_name,
                "ids": list(self.ids),
                "documents": list(self.documents),
                "metadatas": list(self.metadatas),
            }
        np.save(index_dir / "vectors.npy", vectors)
        (index_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return index_dir

    @classmethod
    def load(cls, index_dir: Path | None = None) -> "IndexStore":
        from backend import config

        index_dir = Path(index_dir or config.INDEX_DIR)
        meta = json.loads((index_dir / "meta.json").read_text(encoding="utf-8"))
        store = cls(dim=int(meta["dim"]), model_name=meta.get("model_name", "unknown"))
        store.ids = list(meta["ids"])
        store.documents = list(meta["documents"])
        store.metadatas = list(meta["metadatas"])
        vectors = np.load(index_dir / "vectors.npy")
        store._vectors = vectors if len(store.ids) else None
        store._row_of = {doc: i for i, doc in enumerate(store.ids)}
        return store

    @staticmethod
    def exists(index_dir: Path | None = None) -> bool:
        from backend import config

        index_dir = Path(index_dir or config.INDEX_DIR)
        return (index_dir / "meta.json").exists() and (index_dir / "vectors.npy").exists()

    @classmethod
    def load_or_create(cls, dim: int, model_name: str, index_dir: Path | None = None) -> "IndexStore":
        """Load an existing index if its dim matches, else start an empty one.

        A dim mismatch (e.g. an old hashing index vs the Qwen 2560-dim model) is not
        loaded -- the caller should re-embed from the knowledge store.
        """
        if cls.exists(index_dir):
            store = cls.load(index_dir)
            if store.dim == dim:
                return store
            log.warning(
                "existing index dim %d != embedder dim %d; starting a fresh index "
                "(re-embed the knowledge store).", store.dim, dim,
            )
        return cls(dim=dim, model_name=model_name)
