"""공통 평가기: 어떤 알고리즘이 낸 경로든 방식 A와 B 점수·실제시간을 다시 계산한다.

경로 자체는 바꾸지 않는다. 입력은 간선 리스트(Result.edges) 또는 노드 id 리스트다.
"""
from __future__ import annotations

from algorithms.dijkstra import can_board
from graph import Config, Edge, Graph, slot_of


def edges_from_path(G: Graph, path: list[str]) -> list[Edge]:
    """노드 id 순서만 받은 경로(예: GA 결과)를 간선 리스트로 바꾼다.

    같은 (u, v)에 탑승과 환승이 함께 있으면 탑승을 우선한다(데이터에는 없다).
    """
    out = []
    for u, v in zip(path, path[1:]):
        cands = [e for e in G.adj[u] if e.v == v]
        if not cands:
            raise ValueError(f"간선 없음: {u}->{v}")
        out.append(sorted(cands, key=lambda e: e.kind != "ride")[0])
    return out


def walk(G: Graph, edges: list[Edge], day: str, depart: float, cfg: Config, mode: str):
    """(cost, real, valid). valid=False면 NO_SERVICE 환승이나 첫차·막차 위반이 있다."""
    cost = real = 0.0
    by_ride = False
    valid = True
    slot_a = slot_of(depart)
    for e in edges:
        now = depart + real
        slot = slot_a if mode == "A" else slot_of(now)
        if e.kind == "ride":
            if not by_ride and not can_board(e, day, now):
                valid = False
            d_real, d_cost = G.edge_values(e, day, slot, cfg)
            by_ride = True
        else:
            v = G.edge_values(e, day, slot, cfg)
            if v is None:
                valid = False
                by_ride = False
                continue
            d_real, d_cost = v
            by_ride = False
        cost += d_cost
        real += d_real
    return cost, real, valid


def evaluate(G: Graph, path_or_edges, day: str, depart: float, cfg: Config | None = None) -> dict:
    cfg = cfg or Config()
    edges = path_or_edges
    if edges and isinstance(edges[0], str):
        edges = edges_from_path(G, edges)
    ca, ra, va = walk(G, edges, day, depart, cfg, "A")
    cb, rb, vb = walk(G, edges, day, depart, cfg, "B")
    return {"cost_A": ca, "real_A": ra, "valid_A": va, "cost_B": cb, "real_B": rb, "valid_B": vb}


def evaluate_B(G: Graph, edges: list[Edge], day: str, depart: float, cfg: Config | None = None):
    """방식 B로 다시 평가한 (cost, real, valid)."""
    return walk(G, edges, day, depart, cfg or Config(), "B")
