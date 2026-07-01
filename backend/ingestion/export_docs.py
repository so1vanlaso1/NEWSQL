"""Export Phase-2 artifacts from the knowledge store.

- embedding_docs.jsonl : one {id, text, metadata} per embeddable entry (table,
  column, metric, join_path, value). This is exactly what gets embedded.
- metadata.json        : build info (per-type counts, target model/dim, timestamp).

These are derived from the store so they always match knowledge.db.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from backend import config
from backend.knowledge import embedding_text as et
from backend.store.repository import Repository


def build_docs(repo: Repository | None = None) -> list[dict]:
    repo = repo or Repository()
    docs: list[dict] = []
    for e in repo.all():
        if not et.is_embeddable(e["type"]) or not e.get("enabled", True):
            continue
        text = e.get("embedding_text") or et.build_embedding_text(e)
        docs.append({"id": e["id"], "text": text, "metadata": et.build_metadata(e)})
    return docs


def export(
    repo: Repository | None = None,
    docs_path: Path | None = None,
    metadata_path: Path | None = None,
) -> dict:
    repo = repo or Repository()
    docs = build_docs(repo)
    docs_path = Path(docs_path or config.EMBEDDING_DOCS_PATH)
    metadata_path = Path(metadata_path or config.METADATA_PATH)
    docs_path.parent.mkdir(parents=True, exist_ok=True)

    with docs_path.open("w", encoding="utf-8") as fh:
        for d in docs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")

    by_type: dict[str, int] = {}
    for d in docs:
        t = d["metadata"]["type"]
        by_type[t] = by_type.get(t, 0) + 1

    metadata = {
        "target_model": config.EMBED_MODEL,
        "target_dim": 2560,
        "dialect": config.SQL_DIALECT,
        "doc_count": len(docs),
        "by_type": by_type,
        "entry_counts": repo.counts_by_type(),
        "source_db": str(config.DB_PATH),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"docs_path": str(docs_path), "metadata_path": str(metadata_path),
            "doc_count": len(docs), "by_type": by_type}


if __name__ == "__main__":
    res = export()
    print(f"[export] {res['doc_count']} docs {res['by_type']} -> {res['docs_path']}")
