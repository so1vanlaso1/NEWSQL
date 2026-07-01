"""Exact entity/value pinning by normalized-alias lookup.

This is the RELIABLE path for entities the user names (companies, products,
categories, provinces, status codes). Value embedding docs only carry the full
normalized name as an alias, so semantic search is a weak entity-pinner; a direct
không-dấu alias lookup over the ``value`` entries is precise.

Matching runs on the RAW user message (not the expanded/embedding text) via
longest-span-wins sliding n-grams, so ``"công ty An Phát"`` pins ``cong_ty``
regardless of the concept vector's ranking.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend import config
from backend.common.vn_text import normalize_vietnamese_text
from backend.retrieval.models import MatchedValue

# Ceiling on the sliding-window span so a very long product name doesn't blow up
# the O(n * span) scan; real aliases here are well under this.
_MAX_SPAN_CAP = 8

# Generic head-nouns (không dấu). For dimension values we ALSO index the
# distinctive tail after stripping one of these leading prefixes, so a user typing
# only "An Phát" pins the company "Cong ty FMCG An Phat". Longest prefix wins.
_GENERIC_PREFIXES = (
    "cong ty co phan", "cong ty tnhh", "cong ty fmcg", "cong ty",
    "nha phan phoi", "sieu thi mini", "sieu thi", "cua hang tien loi",
    "cua hang", "tap hoa", "dai ly", "nuoc giai khat", "quan", "nha hang",
)


def _distinctive_tail(nval: str) -> str | None:
    """Strip a leading generic head-noun; return the remaining distinctive span."""
    for pref in _GENERIC_PREFIXES:
        if nval == pref:
            return None
        if nval.startswith(pref + " "):
            tail = nval[len(pref) + 1:].strip()
            toks = tail.split()
            if toks and (len(toks) >= 2 or len(toks[0]) >= 3):
                return tail
            return None
    return None


@dataclass
class ValueAliasIndex:
    by_alias: dict[str, list[dict]] = field(default_factory=dict)  # normalized -> value bodies
    max_span: int = 1


def _keep_alias(key: str, body: dict) -> bool:
    """Drop short/common single tokens that collide with ordinary Vietnamese words.

    A bare 3-char enum alias like ``"ban"`` (khách bận) normalizes the same as
    ``"bán"`` (to sell) and would false-match revenue questions. Dimension values
    (which carry an ``id_column``) are kept at length 3 so ``"sua"`` (Sữa) survives.
    """
    tokens = key.split()
    if len(tokens) == 1:
        tok = tokens[0]
        if len(tok) < 3:
            return False
        if len(tok) == 3 and not body.get("id_column"):
            return False
    return True


def build_value_alias_index(repo) -> ValueAliasIndex:
    by_alias: dict[str, list[dict]] = {}
    max_span = 1
    for e in repo.list(type_="value"):
        if not e.get("enabled", True):
            continue
        body = e.get("body", {}) or {}
        keys: set[str] = set()
        nval = normalize_vietnamese_text(body.get("value", ""))
        if nval:
            keys.add(nval)
            if body.get("id_column"):  # dimension value -> also index its distinctive tail
                tail = _distinctive_tail(nval)
                if tail:
                    keys.add(tail)
        for alias in body.get("aliases", []):
            na = normalize_vietnamese_text(alias)
            if na:
                keys.add(na)
        for key in keys:
            if not _keep_alias(key, body):
                continue
            max_span = max(max_span, len(key.split()))
            by_alias.setdefault(key, []).append(body)
    return ValueAliasIndex(by_alias=by_alias, max_span=max_span)


def match_values(user_message: object, vindex: ValueAliasIndex,
                 max_hits: int | None = None) -> list[MatchedValue]:
    max_hits = max_hits or config.RETRIEVAL_MAX_VALUE_MATCHES
    tokens = normalize_vietnamese_text(user_message).split()
    n = len(tokens)
    if n == 0:
        return []
    covered = [False] * n
    results: list[MatchedValue] = []
    seen: set[tuple[str, str, str]] = set()
    max_span = min(vindex.max_span, _MAX_SPAN_CAP)
    # Longest spans first so "cong ty fmcg an phat" wins over "an phat".
    for span in range(max_span, 0, -1):
        for i in range(0, n - span + 1):
            if any(covered[i:i + span]):
                continue
            phrase = " ".join(tokens[i:i + span])
            bodies = vindex.by_alias.get(phrase)
            if not bodies:
                continue
            matched_any = False
            for body in bodies:
                key = (body.get("table", ""), body.get("column", ""), body.get("value", ""))
                if key in seen:
                    continue
                seen.add(key)
                matched_any = True
                results.append(MatchedValue(
                    table=body.get("table", ""),
                    column=body.get("column", ""),
                    value=body.get("value", ""),
                    id_column=body.get("id_column", "") or "",
                    id_value=body.get("id_value", "") or "",
                    matched_alias=phrase,
                    match_kind="enum" if not body.get("id_column") else "exact",
                ))
            if matched_any:
                for j in range(i, i + span):
                    covered[j] = True
    return results[:max_hits]
