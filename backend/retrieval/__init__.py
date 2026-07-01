"""Phase 3: query-time retrieval.

Turns a (Vietnamese) user message + optional pinned tables into a compact,
structured ``ResolvedContext`` (relevant tables/columns, metric formulas, allowed
joins, matched entity values, global rules) by consuming the Phase 1/2 knowledge
store and vector index. No LLM here -- this is the context that a later phase
serializes into the single LLM prompt.
"""
