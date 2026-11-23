"""라그랑주 완화 LARAC (4단계).

문제: real(P) <= T 를 만족하면서 cost(P) 최소.  T = 최단 실제시간 + slack_sec.
방식 A(정적 가중치)에서 동작한다. 방식 B 요청이 오면 경로 탐색은 방식 A 가중치로 하고,
결과 경로를 experiments.evaluate.evaluate_B로 재평가한다.

한계: 근사해다. (real, cost) 평면에서 가중합 cost + λ·real 을 최소로 하는 경로, 즉
볼록 껍질 위의 경로만 찾는다. 껍질 안쪽에 있는 최적해는 놓칠 수 있다(참고: 06-04 볼록 껍질).
"""
from __future__ import annotations

import dataclasses
import time

from algorithms.common import INFEASIBLE, NO_ROUTE, NO_SERVICE, OK, Result
from algorithms.dijkstra import dijkstra
from graph import Config, Graph

MAX_ITER = 200


def larac(G: Graph, src_name: str, dst_name: str, day: str, depart: float,
          cfg: Config | None = None, slack_sec: float | None = None) -> Result:
    cfg = cfg or Config()
    slack = cfg.slack_sec if slack_sec is None else slack_sec
    cfg_a = dataclasses.replace(cfg, mode="A")
    t0 = time.perf_counter()
    expanded = 0
    calls = 0

    def run(weight):
        nonlocal expanded, calls
        r = dijkstra(G, src_name, dst_name, day, depart, cfg_a, weight)
        expanded += r.expanded
        calls += 1
        return r

    def finish(r: Result, status: str, T, shortest, iters: int, extra: dict) -> Result:
        ms = (time.perf_counter() - t0) * 1000
        extra.update({"T": T, "shortest_real": shortest, "dijkstra_calls": calls,
                      "requested_mode": cfg.mode})
        if cfg.mode == "B" and r.edges:
            from experiments.evaluate import evaluate_B

            cb, rb, vb = evaluate_B(G, r.edges, day, depart, cfg)
            extra.update({"cost_B": cb, "real_B": rb, "valid_B": vb,
                          "note": "경로는 방식 A 가중치로 탐색, 방식 B로 재평가"})
        feasible = None if T is None else (r.real <= T + 1e-9)
        return Result(r.path, r.edges, r.cost, r.real, feasible, expanded, iters, ms, status, extra)

    # 최단 실제시간 → T
    P_r = run("real")
    if P_r.status != OK:
        return finish(P_r, P_r.status if P_r.status in (NO_ROUTE, NO_SERVICE) else NO_ROUTE,
                      None, None, 0, {"bound_active": False})
    shortest = P_r.real
    T = shortest + slack

    P_c = run("cost")
    if P_c.real <= T + 1e-9:  # 제약이 안 걸림
        return finish(P_c, OK, T, shortest, 0, {"lambda": 0.0, "LB": P_c.cost, "bound_active": False})

    # P_r.real = shortest <= T 이므로 해가 없는 경우는 없다. 방어적으로만 확인한다.
    if P_r.real > T + 1e-9:
        return finish(P_r, INFEASIBLE, T, shortest, 0, {"bound_active": True})

    it = 0
    lam = 0.0
    while it < MAX_ITER:
        it += 1
        lam = (P_r.cost - P_c.cost) / (P_c.real - P_r.real)
        P = run(lambda real, cost, lam=lam: cost + lam * real)
        L_P = P.cost + lam * P.real
        L_c = P_c.cost + lam * P_c.real
        if abs(L_P - L_c) < max(1e-9, 1e-12 * abs(L_c)):
            LB = L_P - lam * T
            return finish(P_r, OK, T, shortest, it,
                          {"lambda": lam, "LB": LB, "bound_active": True,
                           "gap_to_LB_pct": (P_r.cost - LB) / LB * 100 if LB > 0 else None})
        if P.real <= T + 1e-9:
            P_r = P
        else:
            P_c = P
    return finish(P_r, OK, T, shortest, it, {"lambda": lam, "bound_active": True, "hit_max_iter": True})
