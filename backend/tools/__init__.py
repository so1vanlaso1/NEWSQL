"""Backend tools the model may call via native function-calling (Phase 17).

Exactly one tool is exposed to the model — ``search_internet`` — and the backend brokers
every invocation (validated, cached, logged, turned into hard-provenance evidence). There
is no MCP host and no tool discovery; see plan §16.
"""
