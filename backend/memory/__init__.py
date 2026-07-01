"""Phase 4: conversation memory.

Persists each turn (question, resolved SQL/schema, result preview/summary/entities)
in a dedicated SQLite file (never the read-only sales.db), and exposes a compact
memory window plus a pre-LLM retrieval plan so follow-ups (refine / drill-down /
ask-about-previous) work. No LLM here -- the real intent classification is Phase 7.
"""
