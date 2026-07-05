"""Shared pytest setup for the SQLNEW backend suite.

Runs entirely with EMBEDDER=hashing (dependency-free, no GPU) against temp databases so
the suite is CI-friendly. Env vars are set at import time — BEFORE any backend module is
imported — so backend.config picks them up (config reads env once at import).
"""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sqlnew_test_"))

os.environ["EMBEDDER"] = "hashing"
os.environ["EMBED_LOAD_IN_4BIT"] = "0"
os.environ["KNOWLEDGE_DB_PATH"] = str(_TMP / "knowledge.db")
os.environ["INDEX_DIR"] = str(_TMP / "index")
os.environ["SKILL_MD_PATH"] = str(_TMP / "skill.md")
os.environ["EMBEDDING_DOCS_PATH"] = str(_TMP / "docs.jsonl")
os.environ["METADATA_PATH"] = str(_TMP / "metadata.json")
os.environ["CONV_DB_PATH"] = str(_TMP / "conversations.db")
os.environ["SCHEMA_SNAPSHOT_PATH"] = str(_TMP / "schema_snapshot.json")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:1/v1")  # never reach a real LLM
# Web research off by default in the suite (no live SearxNG probe); tests that exercise the
# research stage set config.SEARCH_ENABLED=True explicitly via monkeypatch.
os.environ["SEARCH_ENABLED"] = "0"
os.environ.setdefault("SEARXNG_URL", "http://127.0.0.1:1")  # never reach a real SearxNG
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402


@pytest.fixture
def kb(tmp_path):
    """A fresh KnowledgeService (hashing embedder, isolated knowledge.db + index)."""
    from backend.embeddings.embedder import get_embedder
    from backend.embeddings.index_store import IndexStore
    from backend.knowledge.service import KnowledgeService
    from backend.store.repository import Repository

    emb = get_embedder()  # hashing (shared, harmless)
    idx = IndexStore(dim=emb.dim, model_name=emb.model_name)
    return KnowledgeService(Repository(path=tmp_path / "knowledge.db"), emb, idx)


@pytest.fixture
def kb_plumbing(tmp_path):
    """A KnowledgeService with NO embedder (simulates the embedder being down)."""
    from backend.embeddings.index_store import IndexStore
    from backend.knowledge.service import KnowledgeService
    from backend.store.repository import Repository

    return KnowledgeService(Repository(path=tmp_path / "knowledge.db"), None,
                            IndexStore(dim=0, model_name="none"))
