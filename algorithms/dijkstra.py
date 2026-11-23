"""공통 탐색 뼈대 + Dijkstra (2단계).

라벨 = (key, cost, real, node, by_ride, parent).
  key      : 우선순위에 쓰는 누적 가중치 (weight 인자로 결정)
  cost     : 점수 누적 / real : 실제 소요시간 누적 (절대 섞지 않는다)
  by_ride  : 이 노드에 열차를 탄 채로 도착했는가 (False면 출발 직후 또는 환승 직후)

탐색 상태는 (node, by_ride). 첫차·막차 확인이 by_ride에 따라 달라지므로
노드당 라벨 1개로 합치면 방식 A에서도 정확하지 않을 수 있기 때문이다.
방식 B는 slot이 라벨마다 달라져서 상태당 라벨 1개 근사다.

day 인자는 '서비스 요일'이다. 00:00~03:59 출발은 호출하는 쪽에서 전날 day_type을
넘기고 depart를 24:00 이후(예: 24:30 = 88200)로 주면 된다.
"""
from __future__ import annotations

import heapq
import itertools
import time
from typing import Callable

from algorithms.common import NO_ROUTE, NO_SERVICE, OK, Result
from graph import Config, Edge, Graph, clock_of, slot_of

Weight = "str | Callable[[float, float], float]"


def make_weight(weight) -> Callable[[float, float], float]:
    """weight 인자를 f(real, cost) -> 간선 가중치 함수로 바꾼다."""
    if weight == "cost":
        return lambda real, cost: cost
    if weight == "real":
        return lambda real, cost: real
    if callable(weight):
        return weight
    raise ValueError(f"weight must be 'cost', 'real' or callable, got {weight!r}")


def can_board(e: Edge, day: str, now: float) -> bool:
    """새로 타는 탑승 간선의 첫차·막차 확인. 막차 정각은 탈 수 있다(막는 조건은 > last)."""
    c = clock_of(now)
    return e.first_dep(day) <= c <= e.last_dep(day)


def expand(G: Graph, node: str, by_ride: bool, real: float, day: str, depart: float,
           slot_a: int, cfg: Config):
    """한 라벨에서 나가는 간선을 펼친다.

    yield (edge, d_real, d_cost, new_by_ride) 와, 막혀서 건너뛴 간선이 있으면
    마지막에 (None, 0, 0, None)을 한 번 내보낸다(막힘 기록용).
    """
    now = depart + real
    slot = slot_a if cfg.mode == "A" else slot_of(now)
    blocked = False
    for e in G.adj[node]:
        if e.kind == "ride":
            if not by_ride and not can_board(e, day, now):
                blocked = True
                continue
            d_real, d_cost = G.edge_values(e, day, slot, cfg)
            yield e, d_real, d_cost, True
        else:
            v = G.edge_values(e, day, slot, cfg)
            if v is None:  # NO_SERVICE 환승
                blocked = True
                continue
            yield e, v[0], v[1], False
    if blocked:
        yield None, 0.0, 0.0, None


def dijkstra(G: Graph, src_name: str, dst_name: str, day: str, depart: float,
             cfg: Config | None = None, weight="cost",
             h: Callable[[str], float] | None = None) -> Result:
    """h(node)가 주어지면 우선순위를 f = g + h(node)로 쓰는 A*가 된다.

    h는 weight와 같은 단위의 하한이어야 한다(astar.py가 weight="cost"로 호출).
    h가 inf를 돌려주는 노드는 도착역에 닿을 수 없으므로 버린다.
    """
    cfg = cfg or Config()
    wf = make_weight(weight)
    t0 = time.perf_counter()

    srcs = G.nodes_by_name(src_name)
    dsts = set(G.nodes_by_name(dst_name))
    slot_a = slot_of(depart)
    hf = h or (lambda n: 0.0)

    counter = itertools.count()
    # 라벨: (f, tiebreak, g, cost, real, node, by_ride, parent)
    # g = 지금까지 누적 가중치, f = g + h. parent = (이전 라벨, 간선) 또는 None
    heap = []
    best: dict[tuple, float] = {}  # 상태별 최소 g
    for s in srcs:
        h0 = hf(s)
        if h0 == float("inf"):
            continue
        best[(s, False)] = 0.0
        heapq.heappush(heap, (h0, next(counter), 0.0, 0.0, 0.0, s, False, None))

    expanded = 0
    skipped = False
    found = None
    done: set[tuple] = set()

    while heap:
        label = heapq.heappop(heap)
        _, _, g, cost, real, node, by_ride, parent = label
        st = (node, by_ride)
        if st in done:
            continue
        done.add(st)
        if node in dsts:
            found = label
            break
        expanded += 1
        for e, d_real, d_cost, nb in expand(G, node, by_ride, real, day, depart, slot_a, cfg):
            if e is None:
                skipped = True
                continue
            nst = (e.v, nb)
            if nst in done:
                continue
            ng = g + wf(d_real, d_cost)
            if ng < best.get(nst, float("inf")):
                hv = hf(e.v)
                if hv == float("inf"):
                    continue
                best[nst] = ng
                heapq.heappush(heap, (ng + hv, next(counter), ng, cost + d_cost, real + d_real,
                                      e.v, nb, (label, e)))

    ms = (time.perf_counter() - t0) * 1000
    if found is None:
        # 막혀서 경로가 없고 건너뛴 간선이 있었으면 "차가 없어요"
        return Result([], [], float("inf"), float("inf"), None, expanded, 1, ms,
                      NO_SERVICE if skipped else NO_ROUTE)

    path, edges = [found[5]], []
    p = found[7]
    while p is not None:
        lab, e = p
        edges.append(e)
        path.append(lab[5])
        p = lab[7]
    path.reverse()
    edges.reverse()
    return Result(path, edges, found[3], found[4], None, expanded, 1, ms, OK)
