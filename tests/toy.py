"""정점 5~6개 장난감 그래프와 전수 탐색 (reference/LARAC 검증용)."""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import graph as g  # noqa: E402

DEPART = 8 * 3600 + 30 * 60  # 08:30 → slot 6
SLOT = g.slot_of(DEPART)
DAY = "weekday"
WIDE = 10**6


def make_toy(seed: int, n: int | None = None) -> g.Graph:
    rng = random.Random(seed)
    n = n or rng.choice([5, 6])
    ids = [f"T{i}" for i in range(n)]
    nodes = {i: (chr(ord("A") + k), "1") for k, i in enumerate(ids)}
    by_name = {nodes[i][0]: [i] for i in ids}
    ride, trans = [], []
    cong, trv = {}, {}
    for u in ids:
        for v in ids:
            if u == v or rng.random() > 0.55:
                continue
            if rng.random() < 0.2:  # 환승 간선
                e = g.Edge(u, v, "transfer")
                walk, wait = rng.randint(60, 200), rng.randint(0, 400)
                mult = rng.uniform(1.0, 3.0)
                trv[(u, v)] = {d: [(walk, float(wait), mult * (walk + wait), True)] * g.N_SLOTS for d in g.DAYS}
                trans.append(e)
            else:
                e = g.Edge(u, v, "ride", seconds=rng.randint(60, 600), first=(0, 0, 0), last=(WIDE,) * 3)
                cong[(u, v)] = {d: [rng.uniform(0, 160) for _ in range(g.N_SLOTS)] for d in g.DAYS}
                ride.append(e)
    adj = {i: [] for i in ids}
    for e in ride + trans:
        adj[e.u].append(e)
    return g.Graph(nodes, by_name, ride, trans, adj, cong, trv)


def brute_force(G: g.Graph, src: str, dst: str, cfg: g.Config, T: float):
    """src→dst 모든 단순 경로 중 real <= T 인 최소 cost. 없으면 None."""
    s, d = G.by_name[src][0], G.by_name[dst][0]
    best = None
    fastest = None

    def dfs(u, seen, cost, real):
        nonlocal best, fastest
        if u == d:
            fastest = real if fastest is None else min(fastest, real)
            if real <= T + 1e-9 and (best is None or cost < best):
                best = cost
            return
        for e in G.adj[u]:
            if e.v in seen:
                continue
            v = G.edge_values(e, DAY, SLOT, cfg)
            dfs(e.v, seen | {e.v}, cost + v[1], real + v[0])

    dfs(s, {s}, 0.0, 0.0)
    return best, fastest
