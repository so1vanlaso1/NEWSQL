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

# The geolocation tool the model may call inside an analytic review (Phase 19). It returns
# market-penetration context for an area; the backend resolves the area → coords and calls
# Google Places (see backend/analysis/geo_research.py). Kept a separate schema so the
# web-research stage (SEARCH_TOOLS_SCHEMA) is unaffected.
GEO_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "find_nearby_stores",
            "description": (
                "Tìm cửa hàng bán lẻ gần một khu vực trên Google Maps để đánh giá độ phủ thị "
                "trường: có bao nhiêu cửa hàng, đã là khách hàng bao nhiêu, còn tiềm năng bao "
                "nhiêu. Dùng khi phân tích doanh thu/độ phủ theo khu vực, quận, tỉnh."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "description": ("Tên khu vực/quận/tỉnh (VD: 'Quận 7'), mã khách hàng "
                                        "(VD: 'KH_005') hoặc nhân viên (VD: 'NV_003') để lấy "
                                        "toạ độ trung tâm."),
                    },
                    "radius_m": {
                        "type": "integer",
                        "description": "Bán kính tìm kiếm (mét), mặc định theo cấu hình.",
                    },
                },
                "required": ["area"],
            },
        },
    }
]

_KNOWN = {"search_internet", "find_nearby_stores"}


def validate_tool_call(name: str, arguments) -> Tuple[bool, str, dict]:
    """Return ``(ok, reason, clean_args)``. clean_args holds validated args for the named tool."""
    name = (name or "").strip()
    if name not in _KNOWN:
        return False, f"unknown tool: {name!r}", {}
    if not isinstance(arguments, dict):
        return False, "arguments is not an object", {}

    if name == "find_nearby_stores":
        area = arguments.get("area")
        if not isinstance(area, str) or not area.strip():
            return False, "missing/empty area", {}
        clean: dict = {"area": area.strip()[: config.SEARCH_MAX_QUERY_CHARS]}
        radius = arguments.get("radius_m")
        if radius is not None:
            try:
                clean["radius_m"] = max(1, min(int(radius), config.GEO_MAX_RADIUS_M))
            except (TypeError, ValueError):
                pass  # ignore a bad radius; the resolver falls back to the default
        return True, "", clean

    # search_internet (default)
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
