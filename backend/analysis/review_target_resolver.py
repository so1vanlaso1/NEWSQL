"""Resolve a previous-result reference into a ReviewSeed (plan §8.2-8.3).

ANALYTIC_FROM_PREVIOUS_RESULT scopes an investigation to one entity from the last SQL turn
("phân tích sâu khách hàng top 1", "phân tích Cửa hàng 30"). This module reads that turn's
stored result (already persisted by the normal pipeline, §6.3) and pins the referenced
entity by rank position, explicit name, or single-entity default — refusing safely
(``ok=False`` + reason) when there is no previous result or no entity to analyze.
"""
from __future__ import annotations

import re
from typing import Optional

from backend.common import schema_def
from backend.common.vn_text import normalize_vietnamese_text
from backend.analysis.models import ReviewSeed, TargetEntity
from backend.memory.memory_builder import last_sql_turn
from backend.memory.models import ResultEntity, Turn

# Rank cues -> a 1-based position in the previous result. Anchored on a left non-letter
# boundary and restricted to unambiguous rank words: bare "hang"/"so"/"thu" are DROPPED
# because they collide with everyday Vietnamese ("cửa hàng 30", "tháng 5" -> "thang" contains
# "hang", "số", "thứ 2"=Monday) and would read a spurious rank out of a normal message.
# Explicit rank phrases ("dòng thứ 2", "hàng thứ 3", "xếp hạng 4") are still covered.
_RANK_NUM_RE = re.compile(
    r"(?<![a-z])(?:top thu|dong thu|hang thu|xep hang|vi tri|top|dong|row|rank|number)"
    r"\s*#?\s*(\d+)")
_HASH_RE = re.compile(r"#\s*(\d+)")
_ORDINAL_WORDS = {
    "dau tien": 1, "dau": 1, "first": 1, "nhat": 1, "cao nhat": 1, "lon nhat": 1,
    "top": 1, "top one": 1, "highest": 1, "the top": 1,
    "thu hai": 2, "second": 2, "nhi": 2,
    "thu ba": 3, "third": 3,
    "thu tu": 4, "fourth": 4,
    "thu nam": 5, "fifth": 5,
}
_LAST_CUES = ["cuoi cung", "cuoi", "thap nhat", "nho nhat", "it nhat", "bet nhat", "lowest", "last"]

_NO_PREV = "Chưa có kết quả truy vấn trước đó để phân tích sâu."
_NO_ENTITY = ("Kết quả trước đó không có thực thể (khách hàng/sản phẩm...) rõ ràng để phân "
              "tích sâu. Bạn hãy chạy một truy vấn có tên thực thể trước nhé.")
_OUT_OF_RANGE = "Không tìm thấy dòng được nhắc tới trong kết quả trước đó."


def _entity_type(id_column: str) -> str:
    base = id_column[:-3] if id_column.endswith("_id") else id_column
    names = set(schema_def.all_table_names())
    if base in names:
        return base
    owners = [t for t in schema_def.all_table_names() if id_column in schema_def.columns_of(t)]
    return owners[0] if len(owners) == 1 else base


def _entity_columns(turn: Turn) -> Optional[ResultEntity]:
    """The (type, id_column, name_column) structure for the previous result. Prefers the
    stored result_entities (populated by extract_entities); falls back to inferring from
    result_columns (*_id paired with ten_*)."""
    if turn.result_entities:
        return turn.result_entities[0]
    id_cols = [c for c in turn.result_columns if c.endswith("_id")]
    name_cols = [c for c in turn.result_columns if c.startswith("ten_")]
    if not id_cols:
        return None
    idc = id_cols[0]
    base = idc[:-3]
    name_col = next((n for n in name_cols if n == f"ten_{base}"),
                    name_cols[0] if len(name_cols) == 1 else "")
    return ResultEntity(type=_entity_type(idc), id_column=idc, name_column=name_col)


def _resolve_rank(norm: str, n_rows: int) -> Optional[int]:
    """A 1-based rank from the message, or None when no rank cue is present."""
    m = _RANK_NUM_RE.search(norm) or _HASH_RE.search(norm)
    if m:
        return int(m.group(1))
    padded = f" {norm} "
    if any(f" {c} " in padded for c in _LAST_CUES):
        return n_rows
    for word, rank in _ORDINAL_WORDS.items():
        if f" {word} " in padded:
            return rank
    return None


def _match_name(norm: str, rows: list[dict], name_column: str) -> Optional[int]:
    """Index of the first row whose (normalized) name appears in the message, else None.

    Matches on a token boundary (padded), not a raw substring, so "Cửa hàng 15" does not
    falsely match the "Cửa hàng 1" row. Prefers the longest matching name so a more specific
    name wins when several are boundary-substrings of the message.
    """
    if not name_column:
        return None
    padded = f" {norm} "
    best_idx, best_len = None, 0
    for i, row in enumerate(rows):
        nm = normalize_vietnamese_text(row.get(name_column, ""))
        if len(nm) >= 3 and f" {nm} " in padded and len(nm) > best_len:
            best_idx, best_len = i, len(nm)
    return best_idx


def resolve(user_message: str, turns: list[Turn]) -> ReviewSeed:
    last = last_sql_turn(turns or [])
    if last is None:
        return ReviewSeed(ok=False, reason=_NO_PREV)

    rows = last.display_rows or last.result_preview
    if not rows:
        return ReviewSeed(ok=False, reason=_NO_PREV)

    ent = _entity_columns(last)
    if ent is None or not ent.id_column:
        return ReviewSeed(ok=False, reason=_NO_ENTITY)

    norm = normalize_vietnamese_text(user_message)
    idx = _match_name(norm, rows, ent.name_column)
    if idx is None:
        # No explicit name: use the rank cue, or default to the top row (refs_prev already
        # fired upstream, so a bare "phân tích cái này" means the leading/only entity).
        rank = _resolve_rank(norm, len(rows))
        if rank is None:
            rank = 1
        idx = rank - 1

    if idx < 0 or idx >= len(rows):
        return ReviewSeed(ok=False, reason=_OUT_OF_RANGE)

    row = rows[idx]
    id_value = str(row.get(ent.id_column, "")) if ent.id_column else ""
    name_value = str(row.get(ent.name_column, "")) if ent.name_column else ""
    if not id_value:
        return ReviewSeed(ok=False, reason=_NO_ENTITY)

    target = TargetEntity(
        type=ent.type, rank=idx + 1,
        id_column=ent.id_column, id_value=id_value,
        name_column=ent.name_column, name_value=name_value,
    )
    label = name_value or id_value
    metric = (last.selected_metrics or ["doanh_thu"])[0]
    period = ", ".join(last.selected_filters) if last.selected_filters else "kỳ trước"
    base_fact = f"{label} xếp hạng #{idx + 1} theo {metric} trong kết quả trước đó ({period})."

    return ReviewSeed(
        ok=True,
        source_turn_id=last.turn_id,
        source_question=last.standalone_question or last.user_question,
        source_sql=last.generated_sql,
        target_entity=target,
        base_metrics=list(last.selected_metrics),
        base_filters=list(last.selected_filters),
        base_tables=list(last.selected_tables),
        base_fact=base_fact,
    )
