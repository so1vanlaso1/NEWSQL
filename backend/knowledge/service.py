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
import threading
from typing import Optional

from backend import config
from backend.common.logging import get_logger
from backend.embeddings.index_store import IndexStore
from backend.knowledge import embedding_text as et
from backend.knowledge import entry_validator
from backend.store import models
from backend.store.repository import Repository

log = get_logger(__name__)

# embed_status values
EMBEDDED = "embedded"
PENDING = "pending"
ERROR = "error"
DISABLED = "disabled"
NOT_EMBEDDABLE = "not_embeddable"

# Process-wide save lock (plan §12.2): the whole write path — validate, history, upsert,
# embed, render, version bump — runs under one lock so concurrent requests can't interleave
# a half-written entry with a version bump. Re-entrant so restore() can call save().
_SAVE_LOCK = threading.RLock()


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

    # ---- auto-render (Phase 10) ----
    def _render_and_export(self) -> None:
        """Re-render skill.md + embedding_docs.jsonl so the views match knowledge.db.

        Best-effort: a render failure logs but never breaks a save (KB_AUTO_RENDER=0
        defers this to the manual /rebuild/* endpoints).
        """
        if not config.KB_AUTO_RENDER:
            return
        try:
            from backend.ingestion import export_docs
            from backend.knowledge import skill_builder
            skill_builder.write_skill_md(repo=self.repo)
            export_docs.export(repo=self.repo)
        except Exception:  # noqa: BLE001
            log.exception("auto-render of skill.md / embedding_docs.jsonl failed")

    def _finalize_embedding(self, norm: dict, prev: Optional[dict], stored: dict,
                            embeddable: bool, enabled: bool) -> dict:
        """Apply the embedding side-effects for an already-upserted entry."""
        if not embeddable:
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
        if self.embedder is None:
            # Embedder down / plumbing-only: persist as pending so editing is never
            # blocked; startup + /reembed will embed it later (plan §12.5).
            self.repo.set_status(norm["id"], PENDING, "embedder unavailable at save time")
            return {"entry": self.repo.get(norm["id"]), "embedded": False,
                    "embed_status": PENDING, "embed_error": "embedder unavailable at save time"}
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
        embedded_entry = self._embed_one(norm)
        return {"entry": embedded_entry,
                "embedded": embedded_entry.get("embed_status") == EMBEDDED,
                "embed_status": embedded_entry.get("embed_status"),
                "embed_error": embedded_entry.get("embed_error", "")}

    # ---- public API ----
    def save(self, entry_type: str, body: dict, name: Optional[str] = None,
             entry_id: Optional[str] = None, enabled: bool = True,
             history_action: Optional[str] = None) -> dict:
        """Create or update an entry; embed synchronously when needed.

        Phase 10 write path (plan §12.2), all under one process lock:
          validate (schema + dialect) -> write history -> upsert -> embed-or-pending
          -> re-render skill.md + embedding_docs.jsonl -> bump kb_version.

        Raises ``ValueError`` (-> API 422) when KB_VALIDATE_ON_SAVE=strict and the entry
        fails validation. Returns {entry, embedded, embed_status, embed_error}.
        """
        with _SAVE_LOCK:
            norm = self._normalize(entry_type, body, name, entry_id, enabled)

            # Semantic validation (pydantic shape was already checked in _normalize).
            # Pass the repo so cross-entry checks (playbook -> metric/dimension refs,
            # dimension -> join_path) can run (plan §12.3).
            errors = entry_validator.validate_entry(entry_type, norm["body"], repo=self.repo)
            if errors:
                if config.KB_VALIDATE_ON_SAVE == "strict":
                    raise ValueError("; ".join(errors))
                if config.KB_VALIDATE_ON_SAVE == "warn":
                    log.warning("entry %s saved with validation warnings: %s",
                                norm["id"], "; ".join(errors))

            prev = self.repo.get(norm["id"])
            embeddable = et.is_embeddable(entry_type)
            if not embeddable:
                norm["embed_status"] = NOT_EMBEDDABLE
            elif not enabled:
                norm["embed_status"] = DISABLED
            else:
                norm["embed_status"] = PENDING

            # Audit the transition BEFORE the row changes (plan §12.4).
            self.repo.record_history(
                norm["id"], history_action or ("update" if prev else "create"),
                old_body=(prev or {}).get("body"), new_body=norm["body"])
            stored = self.repo.upsert(norm)

            result = self._finalize_embedding(norm, prev, stored, embeddable, enabled)

            # Keep rendered views + version in lockstep with knowledge.db.
            self._render_and_export()
            self.repo.bump_kb_version()
            return result

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
        with _SAVE_LOCK:
            prev = self.repo.get(entry_id)
            existed = self.repo.delete(entry_id)
            if existed:
                self.repo.record_history(entry_id, "delete",
                                         old_body=(prev or {}).get("body"), new_body=None)
            if self.index.contains(entry_id):
                self.index.delete(entry_id)
                self.index.save()
            if existed:
                self._render_and_export()
                self.repo.bump_kb_version()
            return existed

    def restore(self, entry_id: str, history_id: int) -> Optional[dict]:
        """Restore an entry to the body captured in a history row (plan §12.4).

        Returns None if the history row is missing, belongs to another entry, or has no
        restorable body. Re-runs the full save pipeline (validate/embed/render/version).
        """
        with _SAVE_LOCK:
            row = self.repo.get_history_row(history_id)
            if row is None or row.get("entry_id") != entry_id:
                return None
            body = row.get("new_body")
            if body is None:
                body = row.get("old_body")
            if body is None:
                return None
            entry_type = entry_id.split(":", 1)[0]
            if entry_type not in models.ENTRY_TYPES:
                return None
            return self.save(entry_type, body, entry_id=entry_id, history_action="restore")

    def sync_values(self) -> dict:
        """Re-sample distinct entity values from sales.db into `value` entries (plan §12.6).

        Bulk operation: stages values (no per-entry history), embeds pending, re-renders,
        and bumps the version once. Existing curated non-value entries are untouched.
        """
        with _SAVE_LOCK:
            from backend.common.vn_text import normalize_vietnamese_text
            from backend.ingestion import schema_loader
            from backend.knowledge import business_meta as bm

            staged = 0
            for row in schema_loader.collect_value_rows(bm.VALUE_SOURCES):
                val = row["value"]
                norm = normalize_vietnamese_text(val)
                aliases = [norm] if norm and norm != val.lower() else []
                self.stage("value", {
                    "table": row["table"], "column": row["column"],
                    "id_column": row["id_column"], "id_value": row["id_value"],
                    "value": val, "aliases": aliases, "use_when": f"user mentions {val}",
                })
                staged += 1
            for ev in bm.ENUM_VALUES:
                code = ev["value"]
                aliases = list(ev.get("aliases", [])) + [code.lower()]
                self.stage("value", {
                    "table": ev["table"], "column": ev["column"], "id_column": "",
                    "id_value": code, "value": code, "aliases": aliases,
                    "use_when": ev.get("use_when", f"user mentions {code}"),
                })
                staged += 1

            embed = (self.embed_pending() if self.embedder is not None
                     else {"embedded": 0, "errors": 0, "index_size": len(self.index)})
            self._render_and_export()
            version = self.repo.bump_kb_version()
            return {"staged": staged, "embed": embed, "kb_version": version}

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
