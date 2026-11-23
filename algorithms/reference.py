"""정답 기준: 이중 기준 라벨 설정법 (5단계, 비교 대상 아님).

방식 A(정확). 상태 (노드, by_ride)마다 (real, cost) 파레토 라벨을 둔다.
  - real > T 인 라벨은 버린다. T = 최단 실제시간 + slack_sec.
  - 같은 상태에 real, cost가 모두 더 작거나 같은 라벨이 있으면 버린다(지배).
출력은 real <= T 중 최소 cost, 즉 제약 하 최적해다. LARAC·GA의 오차율 계산에 쓴다.

가지치기에 알고리즘 3단계의 하한 h(n)을 쓴다. h는 cost와 real 모두의 하한이라
cost + h 순서로 꺼내면 같은 노드의 라벨은 cost 순서가 되고, 도착역에서 처음 꺼낸 라벨이 최적이다.
방식 B 요청이 오면 방식 A로 풀고 경로를 방식 B로 재평가한다.
"""
from __future__ import annotations

import dataclasses
import heapq
import itertools
import time

from algorithms.astar import get_heuristic
from algorithms.common import NO_ROUTE, NO_SERVICE, OK, Result
from algorithms.dijkstra import dijkstra, expand
from graph import Config, Graph, slot_of

EPS = 1e-9


def reference(G: Graph, src_name: str, dst_name: str, day: str, depart: float,
              cfg: Config | None = None, slack_sec: float | None = None,
              T: float | None = None) -> Result:
    cfg = cfg or Config()
    cfg_a = dataclasses.replace(cfg, mode="A")
    slack = cfg.slack_sec if slack_sec is None else slack_sec
    t0 = time.perf_counter()

    fast = dijkstra(G, src_name, dst_name, day, depart, cfg_a, "real")
    if fast.status != OK:
        ms = (time.perf_counter() - t0) * 1000
        return Result([], [], float("inf"), float("inf"), None, fast.expanded, 1, ms, fast.status,
                      {"bound_active": False})
    shortest = fast.real
    if T is None:
        T = shortest + slack

    h, _, _ = get_heuristic(G, dst_name, day, cfg_a)
    srcs = G.nodes_by_name(src_name)
    dsts = set(G.nodes_by_name(dst_name))
    slot_a = slot_of(depart)

    counter = itertools.count()
    heap = []
    settled: dict[tuple, list[tuple[float, float]]] = {}  # 상태 -> [(real, cost)]
    for s in srcs:
        hv = h(s)
        if hv < float("inf") and hv <= T:
            heapq.heappush(heap, (hv, next(counter), 0.0, 0.0, s, False, None))

    def dominated(st, real, cost):
        return any(r <= real + EPS and c <= cost + EPS for r, c in settled.get(st, ()))

    expanded = 0
    n_labels = 0
    found = None
    while heap:
        label = heapq.heappop(heap)
        _, _, cost, real, node, by_ride, parent = label
        st = (node, by_ride)
        if dominated(st, real, cost):
            continue
        settled.setdefault(st, []).append((real, cost))
        n_labels += 1
        if node in dsts:
            found = label
            break
        expanded += 1
        for e, d_real, d_cost, nb in expand(G, node, by_ride, real, day, depart, slot_a, cfg_a):
            if e is None:
                continue
            nreal, ncost = real + d_real, cost + d_cost
            hv = h(e.v)
            if hv == float("inf") or nreal + hv > T + EPS:
                continue
            if dominated((e.v, nb), nreal, ncost):
                continue
            heapq.heappush(heap, (ncost + hv, next(counter), ncost, nreal, e.v, nb, (label, e)))

    ms = (time.perf_counter() - t0) * 1000
    extra = {"T": T, "shortest_real": shortest, "pareto_labels": n_labels}
    if found is None:
        return Result([], [], float("inf"), float("inf"), None, expanded, 1, ms,
                      NO_SERVICE if fast.status == NO_SERVICE else NO_ROUTE, extra)
    path, edges = [found[4]], []
    p = found[6]
    while p is not None:
        lab, e = p
        edges.append(e)
        path.append(lab[4])
        p = lab[6]
    path.reverse()
    edges.reverse()
    if cfg.mode == "B":
        from experiments.evaluate import evaluate_B

        cb, rb, vb = evaluate_B(G, edges, day, depart, cfg)
        extra.update({"cost_B": cb, "real_B": rb, "valid_B": vb})
    return Result(path, edges, found[2], found[3], found[3] <= T + EPS, expanded, 1, ms, OK, extra)
