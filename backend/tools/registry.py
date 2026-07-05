"""Tool registry: the fixed OpenAI ``tools`` schema + strict arg validation + dispatch.

The schema is a constant — the planner and writer never touch it. Every tool call the model
emits is validated here before execution (name known; args parse to a dict; ``query`` a
non-empty string within ``SEARCH_MAX_QUERY_CHARS``), so a malformed/hallucinated call from a
9B model is rejected rather than executed (plan §16.3-16.4).
"""
from __future__ import annotations

from typing import Tuple

from backend import config
from backend.tools.search_internet import SearchResult, search_internet

# The one and only tool exposed to the model (plan §16.3, kept literal for stability).
SEARCH_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_internet",
            "description": (
                "Tìm kiếm thông tin trên Internet (SearxNG). Chỉ dùng khi cần thông tin "
                "thị trường/đối thủ/ngành bên ngoài hệ thống nội bộ."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Câu truy vấn tìm kiếm bằng tiếng Việt.",
                    }
                },
                "required": ["query"],
            },
        },
    }
]

_KNOWN = {"search_internet"}


def validate_tool_call(name: str, arguments) -> Tuple[bool, str, dict]:
    """Return ``(ok, reason, clean_args)``. ``clean_args`` holds a validated ``query``."""
    name = (name or "").strip()
    if name not in _KNOWN:
        return False, f"unknown tool: {name!r}", {}
    if not isinstance(arguments, dict):
        return False, "arguments is not an object", {}
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return False, "missing/empty query", {}
    query = query.strip()
    if len(query) > config.SEARCH_MAX_QUERY_CHARS:
        query = query[: config.SEARCH_MAX_QUERY_CHARS]
    return True, "", {"query": query}


def dispatch(name: str, args: dict) -> SearchResult:
    """Execute a validated tool call. Only ``search_internet`` is registered."""
    if name == "search_internet":
        return search_internet(args["query"])
    raise KeyError(name)  # unreachable: validate_tool_call gates the name first
