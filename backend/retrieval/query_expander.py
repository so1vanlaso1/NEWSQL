"""Normalize + expand a Vietnamese user message for embedding retrieval (design §21).

User messages arrive in Vietnamese có dấu; DB identifiers are snake_case không dấu.
The expanded query keeps the original (the Qwen embedder is instruction-tuned for
Vietnamese) and adds the không-dấu form plus any snake_case identifier hints implied
by the ``rule:normalization`` mapping, so concept terms bridge to schema tokens.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.common.vn_text import normalize_vietnamese_text


@dataclass
class ExpandedQuery:
    raw: str
    normalized: str
    identifier_hints: list[str] = field(default_factory=list)
    text: str = ""


def load_normalization_map(repo) -> dict[str, str]:
    """{normalized_vn_phrase -> snake_case identifier} from ``rule:normalization`` items.

    Items look like ``"công ty -> cong_ty"`` or ``"tỉnh thành / thành phố -> vi_tri.tinh_thanh"``.
    The left side may hold several ``/``-separated aliases; the right side is a table,
    column, or ``table.column`` identifier.
    """
    mapping: dict[str, str] = {}
    for e in repo.list(type_="rule"):
        body = e.get("body", {}) or {}
        if body.get("section") != "normalization":
            continue
        for item in body.get("items", []):
            left, sep, right = str(item).partition("->")
            if not sep:
                continue
            ident = right.strip()
            if not ident:
                continue
            for alias in left.split("/"):
                key = normalize_vietnamese_text(alias)
                if key:
                    mapping[key] = ident
    return mapping


def _identifier_tokens(ident: str) -> list[str]:
    """"vi_tri.tinh_thanh" -> ["vi_tri", "tinh_thanh"]; "cong_ty" -> ["cong_ty"]."""
    return [seg for seg in ident.split(".") if seg]


def _phrase_present(normalized_msg: str, phrase: str) -> bool:
    """Token-boundary containment over the space-joined không-dấu forms."""
    if not phrase:
        return False
    return f" {phrase} " in f" {normalized_msg} "


def expand_query(user_message: object, norm_map: dict[str, str] | None = None) -> ExpandedQuery:
    raw = str(user_message or "").strip()
    normalized = normalize_vietnamese_text(raw)
    hints: list[str] = []
    if norm_map:
        for phrase, ident in norm_map.items():
            if _phrase_present(normalized, phrase):
                for tok in _identifier_tokens(ident):
                    if tok not in hints:
                        hints.append(tok)
    parts = [raw]
    if normalized and normalized != raw.lower():
        parts.append(normalized)
    if hints:
        parts.append(" ".join(hints))
    return ExpandedQuery(raw=raw, normalized=normalized, identifier_hints=hints,
                         text="\n".join(p for p in parts if p))
