"""Phase 7: the single remote LLM call per turn.

- ``client``          -> defensive OpenAI-compatible HTTP client (never raises).
- ``prompt_builder``  -> the SQLite-pinned system/user prompt + JSON contract.
- ``response_parser`` -> robust JSON -> ``LlmDecision`` (never raises).
"""
