"""Synthesize the minimal allowed joins connecting the final table set.

The authoritative join source is the FK graph (``schema_def.all_foreign_keys``).
We build an undirected graph, pick a hub anchor, and connect every other table
into the growing component by shortest FK path -- intermediate (bridge) tables
like ``don_hang_ban`` / ``chi_tiet_don_hang_ban`` enter here, never fabricated.

Curated ``join_path`` docs (hand-authored, minimal) are cross-checked: when a
retrieved path's tables are all present, its conditions are authoritative and
override the FK-derived duplicate (cleaner provenance).
"""
from __future__ import annotations

from collections import defaultdict, deque

from backend.common import schema_def
from backend.retrieval.models import ResolvedJoin

# Preferred anchors: the fact-table hub yields natural revenue join shapes.
_ANCHOR_PRIORITY = ("chi_tiet_don_hang_ban", "don_hang_ban")


def build_fk_graph() -> dict[str, list[dict]]:
    graph: dict[str, list[dict]] = defaultdict(list)
    for fk in schema_def.all_foreign_keys():
        ft, fc = fk["from_table"], fk["from_column"]
        tt, tc = fk["to_table"], fk["to_column"]
        graph[ft].append({"to": tt, "from_col": fc, "to_col": tc})
        graph[tt].append({"to": ft, "from_col": tc, "to_col": fc})
    return graph


def _pick_anchor(tables: list[str], graph: dict[str, list[dict]]) -> str:
    for pref in _ANCHOR_PRIORITY:
        if pref in tables:
            return pref
    # else the highest FK-degree table among the set
    return max(tables, key=lambda t: (len(graph.get(t, [])), t))


def _bfs_into_component(graph, source, component):
    """Shortest FK path (list of directed edge dicts) from *source* into any node
    already in *component*. Returns [] if source is already in the component, or
    None if unreachable."""
    if source in component:
        return []
    prev: dict[str, tuple] = {source: None}
    q = deque([source])
    while q:
        cur = q.popleft()
        for e in graph.get(cur, []):
            nxt = e["to"]
            if nxt in prev:
                continue
            edge = {"from": cur, "from_col": e["from_col"], "to": nxt, "to_col": e["to_col"]}
            prev[nxt] = (cur, edge)
            if nxt in component:
                edges = []
                node = nxt
                while prev[node] is not None:
                    parent, used_edge = prev[node]
                    edges.append(used_edge)
                    node = parent
                edges.reverse()
                return edges
            q.append(nxt)
    return None


def _join_key(lt, lc, rt, rc) -> frozenset:
    return frozenset({f"{lt}.{lc}", f"{rt}.{rc}"})


def _edge_to_join(edge: dict, source: str) -> ResolvedJoin:
    lt, lc, rt, rc = edge["from"], edge["from_col"], edge["to"], edge["to_col"]
    return ResolvedJoin(left_table=lt, left_column=lc, right_table=rt, right_column=rc,
                        condition=f"{lt}.{lc} = {rt}.{rc}", source=source)


def _parse_condition(cond: str):
    left, sep, right = str(cond).partition("=")
    if not sep:
        return None
    lt, _, lc = left.strip().partition(".")
    rt, _, rc = right.strip().partition(".")
    if lt and lc and rt and rc:
        return lt.strip(), lc.strip(), rt.strip(), rc.strip()
    return None


def expand_joins(final_tables, curated_paths=None):
    """Connect *final_tables* via FK shortest paths, then overlay curated joins.

    Returns ``(joins: list[ResolvedJoin], used_tables: list[str], unreachable: list[str])``.
    ``used_tables`` includes any bridge tables added to connect the set.
    """
    graph = build_fk_graph()
    tables: list[str] = []
    for t in final_tables:  # de-dupe, keep order
        if t not in tables:
            tables.append(t)

    if not tables:
        return [], [], []
    if len(tables) == 1:
        return [], list(tables), []

    anchor = _pick_anchor(tables, graph)
    component = {anchor}
    used = set(tables)
    chosen: dict[frozenset, ResolvedJoin] = {}
    unreachable: list[str] = []

    for t in tables:
        if t == anchor:
            continue
        edges = _bfs_into_component(graph, t, component)
        if edges is None:
            unreachable.append(t)
            continue
        for e in edges:
            component.add(e["from"])
            component.add(e["to"])
            used.add(e["from"])
            used.add(e["to"])
            rj = _edge_to_join(e, source="fk_graph")
            chosen.setdefault(_join_key(rj.left_table, rj.left_column,
                                        rj.right_table, rj.right_column), rj)

    # Curated cross-check: a fully-present path's conditions are authoritative.
    for path in (curated_paths or []):
        p_tables = set(path.get("tables", []))
        if not p_tables or not p_tables <= used:
            continue
        name = path.get("name", "")
        for cond in path.get("joins", []):
            parsed = _parse_condition(cond)
            if not parsed:
                continue
            lt, lc, rt, rc = parsed
            rj = ResolvedJoin(left_table=lt, left_column=lc, right_table=rt, right_column=rc,
                              condition=f"{lt}.{lc} = {rt}.{rc}", source=f"join_path:{name}")
            chosen[_join_key(lt, lc, rt, rc)] = rj  # override FK-derived duplicate

    # Stable order: keep tables' discovery order roughly by listing joins as found.
    joins = list(chosen.values())
    used_order = [t for t in tables] + [t for t in sorted(used) if t not in tables]
    return joins, used_order, unreachable
