"""Phase 9: deep health check (backend/api/health.py), with the LLM mocked/offline."""
from backend.api import health, state


def test_health_blocks_present_with_offline_llm(kb, monkeypatch):
    # Point health at our isolated KnowledgeService.
    monkeypatch.setattr(state, "_service", kb, raising=False)

    h = health._build_health()
    for block in ("db", "knowledge", "index", "embedder", "llm", "search"):
        assert block in h, f"missing health block: {block}"

    # LLM is unreachable in tests -> reported cleanly, never raised.
    assert h["llm"]["reachable"] is False
    assert h["llm"]["latency_ms"] is not None

    # knowledge/index/embedder read from our service.
    assert isinstance(h["knowledge"]["kb_version"], int)
    assert h["embedder"]["ok"] is True          # hashing embedder is loaded
    assert h["index"]["dim"] == kb.embedder.dim
    # Search is disabled in the suite -> neutral light (reachable None), no network probe.
    assert h["search"]["enabled"] is False
    assert h["search"]["reachable"] is None


def test_health_llm_probe_uses_mocked_endpoint(kb, monkeypatch):
    monkeypatch.setattr(state, "_service", kb, raising=False)

    class _FakeResp:
        status_code = 200
        text = '{"data":[{"id":"qwen-test"}]}'

        def json(self):
            return {"data": [{"id": "qwen-test"}]}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _FakeResp()

    monkeypatch.setattr(health.httpx, "Client", _FakeClient)
    block = health._check_llm()
    assert block["reachable"] is True
    assert block["model"] == "qwen-test"


def test_db_check_reports_missing(monkeypatch, tmp_path):
    from backend import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "nope.db")
    block = health._check_db()
    assert block["ok"] is False
