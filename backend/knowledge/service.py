"""Knowledge service: the glue between the store, the embedder, and the vector index.

Save flow (synchronous embed-on-save):
  validate body -> derive id/name -> compute embedding_text + content_hash
  -> repo.upsert -> if embeddable & enabled & hash changed/new: embed one and
     index.upsert (persist) -> set embed_status.

Also supports delete (store + index), force re-embed, and batch embed of pending
entries (used by the seeder so ~450 short docs embed efficiently).
"""
from __future__ import annotations

import hashlib
from typing import Optional

from backend import config
from backend.embeddings.index_store import IndexStore
from backend.knowledge import embedding_text as et
from backend.store import models
from backend.store.repository import Repository

# embed_status values
EMBEDDED = "embedded"
PENDING = "pending"
ERROR = "error"
DISABLED = "disabled"
NOT_EMBEDDABLE = "not_embeddable"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class KnowledgeService:
    def __init__(self, repo: Repository, embedder, index: IndexStore):
        self.repo = repo
        self.embedder = embedder
        self.index = index

    # ---- construction ----
    @classmethod
    def build(cls, load_embedder: bool = True) -> "KnowledgeService":
        repo = Repository()
        embedder = None
        if load_embedder:
            from backend.embeddings.embedder import get_embedder
            embedder = get_embedder()
            index = IndexStore.load_or_create(embedder.dim, embedder.model_name)
        else:
            # Plumbing-only mode (no torch): reuse an existing index if present.
            index = IndexStore.load() if IndexStore.exists() else IndexStore(dim=0, model_name="none")
        return cls(repo, embedder, index)

    # ---- helpers ----
    def _normalize(self, entry_type: str, body: dict, name: Optional[str], entry_id: Optional[str],
                   enabled: bool) -> dict:
        vbody = models.validate_body(entry_type, body)
        eid = entry_id or models.derive_id(entry_type, vbody)
        ename = name or models.default_name(entry_type, vbody)
        text = et.build_embedding_text({"id": eid, "type": entry_type, "body": vbody})
        return {
            "id": eid,
            "type": entry_type,
            "name": ename,
            "body": vbody,
            "enabled": enabled,
            "embedding_text": text,
            "content_hash": _hash(text) if text else "",
        }

    def _embed_one(self, entry: dict) -> dict:
        """Embed a single entry and upsert into the index. Returns the stored entry."""
        if self.embedder is None:
            raise RuntimeError("No embedder loaded (started in plumbing-only mode).")
        try:
            vec = self.embedder.encode([entry["embedding_text"]], is_query=False)[0]
            self.index.upsert(entry["id"], vec, entry["embedding_text"], et.build_metadata(entry))
            self.index.save()
            self.repo.set_status(entry["id"], EMBEDDED, "")
        except Exception as exc:  # noqa: BLE001
            self.repo.set_status(entry["id"], ERROR, f"{exc.__class__.__name__}: {exc}")
        return self.repo.get(entry["id"])  # type: ignore[return-value]

    # ---- public API ----
    def save(self, entry_type: str, body: dict, name: Optional[str] = None,
             entry_id: Optional[str] = None, enabled: bool = True) -> dict:
        """Create or update an entry; embed synchronously when needed.

        Returns {entry, embedded: bool, embed_status, embed_error}.
        """
        norm = self._normalize(entry_type, body, name, entry_id, enabled)
        prev = self.repo.get(norm["id"])
        embeddable = et.is_embeddable(entry_type)

        if not embeddable:
            norm["embed_status"] = NOT_EMBEDDABLE
        elif not enabled:
            norm["embed_status"] = DISABLED
        else:
            norm["embed_status"] = PENDING
        stored = self.repo.upsert(norm)

        embedded = False
        if not embeddable:
            # keep NOT_EMBEDDABLE; make sure it isn't lingering in the index
            if self.index.contains(norm["id"]):
                self.index.delete(norm["id"])
                self.index.save()
            return {"entry": stored, "embedded": False,
                    "embed_status": NOT_EMBEDDABLE, "embed_error": ""}
        if not enabled:
            if self.index.contains(norm["id"]):
                self.index.delete(norm["id"])
                self.index.save()
            return {"entry": stored, "embedded": False, "embed_status": DISABLED, "embed_error": ""}

        unchanged = (
            prev is not None
            and prev.get("content_hash") == norm["content_hash"]
            and prev.get("embed_status") == EMBEDDED
            and self.index.contains(norm["id"])
        )
        if unchanged:
            self.repo.set_status(norm["id"], EMBEDDED, "")
            return {"entry": self.repo.get(norm["id"]), "embedded": False,
                    "embed_status": EMBEDDED, "embed_error": ""}

        stored = self._embed_one(norm)
        embedded = stored.get("embed_status") == EMBEDDED
        return {"entry": stored, "embedded": embedded,
                "embed_status": stored.get("embed_status"), "embed_error": stored.get("embed_error", "")}

    def stage(self, entry_type: str, body: dict, name: Optional[str] = None,
              entry_id: Optional[str] = None, enabled: bool = True) -> dict:
        """Persist an entry as pending WITHOUT embedding (used by the seeder)."""
        norm = self._normalize(entry_type, body, name, entry_id, enabled)
        if not et.is_embeddable(entry_type):
            norm["embed_status"] = NOT_EMBEDDABLE
        elif not enabled:
            norm["embed_status"] = DISABLED
        else:
            norm["embed_status"] = PENDING
        return self.repo.upsert(norm)

    def delete(self, entry_id: str) -> bool:
        existed = self.repo.delete(entry_id)
        if self.index.contains(entry_id):
            self.index.delete(entry_id)
            self.index.save()
        return existed

    def reembed(self, entry_id: str) -> Optional[dict]:
        entry = self.repo.get(entry_id)
        if entry is None:
            return None
        if not et.is_embeddable(entry["type"]) or not entry.get("enabled", True):
            return {"entry": entry, "embedded": False,
                    "embed_status": entry.get("embed_status"), "embed_error": ""}
        # recompute embedding_text in case rendering logic changed
        norm = self._normalize(entry["type"], entry["body"], entry.get("name"), entry_id,
                               entry.get("enabled", True))
        norm["embed_status"] = PENDING
        self.repo.upsert(norm)
        stored = self._embed_one(norm)
        return {"entry": stored, "embedded": stored.get("embed_status") == EMBEDDED,
                "embed_status": stored.get("embed_status"), "embed_error": stored.get("embed_error", "")}

    def embed_pending(self, batch_size: Optional[int] = None) -> dict:
        """Batch-embed all pending, embeddable, enabled entries. Returns counts."""
        if self.embedder is None:
            raise RuntimeError("No embedder loaded (started in plumbing-only mode).")
        batch = batch_size or config.EMBED_BATCH_SIZE
        pending = [
            e for e in self.repo.all()
            if e.get("embed_status") == PENDING and et.is_embeddable(e["type"]) and e.get("enabled", True)
            and e.get("embedding_text")
        ]
        ok = err = 0
        for start in range(0, len(pending), batch):
            chunk = pending[start:start + batch]
            try:
                vecs = self.embedder.encode([e["embedding_text"] for e in chunk], is_query=False)
            except Exception as exc:  # noqa: BLE001
                for e in chunk:
                    self.repo.set_status(e["id"], ERROR, f"{exc.__class__.__name__}: {exc}")
                err += len(chunk)
                continue
            for e, vec in zip(chunk, vecs):
                try:
                    self.index.upsert(e["id"], vec, e["embedding_text"], et.build_metadata(e))
                    self.repo.set_status(e["id"], EMBEDDED, "")
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    self.repo.set_status(e["id"], ERROR, f"{exc.__class__.__name__}: {exc}")
                    err += 1
        self.index.save()
        return {"embedded": ok, "errors": err, "index_size": len(self.index)}

    def reembed_all(self) -> dict:
        """Mark every embeddable+enabled entry pending, then batch-embed."""
        for e in self.repo.all():
            if et.is_embeddable(e["type"]) and e.get("enabled", True):
                self.repo.set_status(e["id"], PENDING, "")
        return self.embed_pending()

    def status(self) -> dict:
        emb = self.embedder
        return {
            "embedder": {
                "model_name": getattr(emb, "model_name", "none"),
                "dim": getattr(emb, "dim", 0),
                "device": getattr(emb, "device", "n/a"),
                "loaded": emb is not None,
            },
            "index": {"size": len(self.index), "dim": self.index.dim,
                      "by_type": self.index.type_counts()},
            "entries": {"by_type": self.repo.counts_by_type(),
                        "by_status": self.repo.counts_by_status()},
            "dialect": config.SQL_DIALECT,
        }
