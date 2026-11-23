"""혼잡 가중 A* (3단계).

목적함수는 점수 cost의 합(팀 확정 식). 우선순위 f = g_cost + h(n).

h(n) = n에서 도착역 노드 중 하나까지의 '간선별 하한 가중치'의 최단 거리.
  - 도착역 노드들을 출발점으로 한 역방향 Dijkstra로 사전 계산한다.
  - 탑승 간선의 하한: seconds              (cost = seconds * m(c), m >= 1 이라 cost >= seconds)
  - 환승 간선의 하한: 그 요일 전 시간대(NO_SERVICE 제외)에서 min(walk+wait, cost)의 최솟값
    기본 설정에서는 cost >= walk+wait 이므로 walk+wait의 최솟값이다.
    transfer_mult < 1 같은 설정에서도 하한이 되도록 cost와의 min을 취한다.
  - 대기(wait)를 시간대마다 달라지는 값으로 두지 않고 전 시간대 최솟값을 쓰므로 방식 A·B 모두에 하한이다.

근거(README 참고): 어떤 간선이든 cost >= (그 간선의 하한 가중치) 이므로 h는 admissible이고,
역방향 최단 거리라서 h(u) <= 하한(u,v) + h(v) 를 만족하므로 consistent 하다.
따라서 방식 A(정적 가중치)에서는 Dijkstra와 같은 최적 점수를 낸다.
방식 B는 Dijkstra와 마찬가지로 상태당 라벨 1개 근사다. 허용시간은 탐색에 넣지 않는다.
"""
from __future__ import annotations

import heapq
import itertools
import time

from algorithms.common import Result
from algorithms.dijkstra import dijkstra
from graph import DAYS, N_SLOTS, Config, Edge, Graph

INF = float("inf")


def _cache(G: Graph) -> dict:
    return G.__dict__.setdefault("_astar_cache", {})


def _reverse_adj(G: Graph) -> dict[str, list[Edge]]:
    c = _cache(G)
    if "radj" not in c:
        radj: dict[str, list[Edge]] = {n: [] for n in G.nodes}
        for edges in G.adj.values():
            for e in edges:
                radj[e.v].append(e)
        c["radj"] = radj
    return c["radj"]


def edge_lower_bound(G: Graph, e: Edge, day: str, cfg: Config) -> float:
    """간선 하나의 하한 가중치(초). 쓸 수 없는 환승(전 시간대 NO_SERVICE)은 inf."""
    if e.kind == "ride":
        return float(e.seconds)  # cost >= seconds (alpha >= 0)
    key = ("tlb", e.key, day, cfg.transfer_mult)
    c = _cache(G)
    if key not in c:
        best = INF
        for slot in range(N_SLOTS):
            v = G.edge_values(e, day, slot, cfg)
            if v is not None:
                best = min(best, v[0], v[1])  # min(real, cost)
        c[key] = best
    return c[key]


def build_heuristic(G: Graph, dst_name: str, day: str, cfg: Config) -> dict[str, float]:
    """도착역 노드들에서 시작하는 역방향 Dijkstra. 반환: 노드 -> h (닿을 수 없으면 키 없음)."""
    if cfg.alpha < 0:
        raise ValueError("alpha must be >= 0 for the heuristic to be admissible")
    radj = _reverse_adj(G)
    counter = itertools.count()
    dist: dict[str, float] = {}
    heap = []
    for d in G.nodes_by_name(dst_name):
        dist[d] = 0.0
        heapq.heappush(heap, (0.0, next(counter), d))
    done = set()
    while heap:
        dv, _, v = heapq.heappop(heap)
        if v in done:
            continue
        done.add(v)
        for e in radj[v]:
            w = edge_lower_bound(G, e, day, cfg)
            if w == INF:
                continue
            nd = dv + w
            if nd < dist.get(e.u, INF):
                dist[e.u] = nd
                heapq.heappush(heap, (nd, next(counter), e.u))
    return dist


def get_heuristic(G: Graph, dst_name: str, day: str, cfg: Config):
    """(h 함수, 새로 만들었는지, 만드는 데 걸린 ms). 같은 (도착역, 요일, 환승배율)이면 재사용."""
    key = ("h", dst_name, day, cfg.transfer_mult)
    c = _cache(G)
    built, ms = False, 0.0
    if key not in c:
        t0 = time.perf_counter()
        c[key] = build_heuristic(G, dst_name, day, cfg)
        ms = (time.perf_counter() - t0) * 1000
        built = True
    table = c[key]
    return (lambda n: table.get(n, INF)), built, ms


def astar(G: Graph, src_name: str, dst_name: str, day: str, depart: float,
          cfg: Config | None = None) -> Result:
    cfg = cfg or Config()
    t0 = time.perf_counter()
    h, built, h_ms = get_heuristic(G, dst_name, day, cfg)
    r = dijkstra(G, src_name, dst_name, day, depart, cfg, "cost", h=h)
    r.runtime_ms = (time.perf_counter() - t0) * 1000  # 휴리스틱을 새로 만들었다면 그 시간 포함
    srcs = G.nodes_by_name(src_name)
    r.extra.update({
        "h_start": min((h(s) for s in srcs), default=INF),
        "h_built": built,
        "h_build_ms": h_ms,
        "mode": cfg.mode,
    })
    return r
