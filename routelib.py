"""CLI(route.py), UI(app.py), 실험(experiments/run.py)이 같이 쓰는 공통 함수."""
from __future__ import annotations

import dataclasses

from algorithms import genetic
from algorithms.astar import astar
from algorithms.common import NOT_IMPLEMENTED, OK, Result
from algorithms.dijkstra import dijkstra
from algorithms.larac import larac
from algorithms.reference import reference
from graph import Config, Graph, slot_of

ALGOS = ("dijkstra", "astar", "larac", "ga", "ref")
ALGO_LABELS = {
    "dijkstra_cost": "Dijkstra (점수 최소)",
    "dijkstra_real": "Dijkstra (시간 최단)",
    "astar": "혼잡 가중 A*",
    "larac": "라그랑주 완화 LARAC",
    "ga": "유전 알고리즘",
    "ref": "정답 기준(Reference)",
}


def run_algo(G: Graph, algo: str, src: str, dst: str, day: str, depart: float,
             cfg: Config, weight: str = "cost", seed: int = 0) -> Result:
    """algo: dijkstra | dijkstra_cost | dijkstra_real | astar | larac | ga | ref"""
    if algo in ("dijkstra", "dijkstra_cost", "dijkstra_real"):
        w = "real" if algo == "dijkstra_real" else ("real" if (algo == "dijkstra" and weight == "real") else "cost")
        return dijkstra(G, src, dst, day, depart, cfg, w)
    if algo == "astar":
        return astar(G, src, dst, day, depart, cfg)
    if algo == "larac":
        return larac(G, src, dst, day, depart, cfg)
    if algo == "ref":
        return reference(G, src, dst, day, depart, cfg)
    if algo == "ga":
        try:
            return genetic.run(G, src, dst, day, depart, cfg, seed)
        except NotImplementedError as e:
            return Result([], [], float("inf"), float("inf"), None, 0, 0, 0.0, NOT_IMPLEMENTED,
                          {"message": str(e)})
    raise ValueError(f"unknown algo {algo!r}")


def edge_trace(G: Graph, edges, day: str, depart: float, cfg: Config) -> list[dict]:
    """경로의 간선마다 (real, cost, slot, 혼잡도)를 다시 계산한다."""
    out, real = [], 0.0
    slot_a = slot_of(depart)
    for e in edges:
        slot = slot_a if cfg.mode == "A" else slot_of(depart + real)
        v = G.edge_values(e, day, slot, cfg)
        if v is None:
            out.append({"kind": e.kind, "real": 0.0, "cost": 0.0, "slot": slot, "cong": None, "blocked": True})
            continue
        cong = G.cong.get(e.key, {}).get(day, [0.0] * 39)[slot] if e.kind == "ride" else None
        out.append({"kind": e.kind, "real": v[0], "cost": v[1], "slot": slot, "cong": cong, "blocked": False})
        real += v[0]
    return out


LINE_SHORT = {
    "경의·중앙선": "경의중앙선", "수인분당선": "수인분당선", "신분당선": "신분당선", "인천국제공항선": "공항철도",
    "인천지하철 1호선": "인천1호선", "인천지하철 2호선": "인천2호선", "경춘선": "경춘선", "서해선": "서해선",
    "경강선": "경강선", "의정부": "의정부경전철", "김포도시철도": "김포골드라인", "GTX-A": "GTX-A",
    "자기부상철도": "자기부상철도", "수도권 경량도시철도 에버라인": "에버라인",
    "수도권 경량도시철도 우이신설선": "우이신설선", "수도권 경량도시철도 신림선": "신림선",
}


def line_label(line: str) -> str:
    """'2' → '2호선', '신분당선' → '신분당선'"""
    return f"{line}호선" if line.isdigit() else LINE_SHORT.get(line, line)


def hhmm(sec: float) -> str:
    sec = int(round(sec))
    days, sec = divmod(sec, 86400)
    s = f"{sec // 3600:02d}:{sec % 3600 // 60:02d}"
    return s + (f" (+{days}일)" if days else "")


def describe(G: Graph, r: Result, day: str, depart: float, cfg: Config) -> dict:
    """결과를 사람이 읽는 구간 목록으로 바꾼다 (역·노선·환승)."""
    if r.status != OK or not r.edges:
        return {"segments": [], "arrive": None, "transfers": 0, "stations": 0}
    trace = edge_trace(G, r.edges, day, depart, cfg)
    segs, transfers = [], 0
    now = depart
    for e, t in zip(r.edges, trace):
        if e.kind == "transfer":
            transfers += 1
            segs.append({"type": "transfer", "station": G.nodes[e.u][0],
                         "from_line": G.nodes[e.u][1], "to_line": G.nodes[e.v][1],
                         "from_label": line_label(G.nodes[e.u][1]), "to_label": line_label(G.nodes[e.v][1]),
                         "estimated": e.u in G.ext_nodes or e.v in G.ext_nodes,
                         "minutes": round(t["real"] / 60, 1), "at": hhmm(now)})
        else:
            line = G.nodes[e.u][1]
            est = e.u in G.ext_nodes
            last = segs[-1] if segs else None
            if last and last["type"] == "ride" and last["line"] == line:
                last["stations"].append(G.nodes[e.v][0])
                last["minutes"] = round(last["minutes"] + t["real"] / 60, 1)
                last["max_cong"] = max(last["max_cong"], t["cong"] or 0)
            else:
                segs.append({"type": "ride", "line": line, "line_label": line_label(line), "estimated": est,
                             "stations": [G.nodes[e.u][0], G.nodes[e.v][0]],
                             "minutes": round(t["real"] / 60, 1), "at": hhmm(now),
                             "max_cong": t["cong"] or 0})
        now += t["real"]
    n_st = sum(len(s["stations"]) - 1 for s in segs if s["type"] == "ride") + 1
    return {"segments": segs, "arrive": hhmm(depart + r.real), "transfers": transfers, "stations": n_st,
            "estimated": any(s["estimated"] for s in segs)}


def result_summary(r: Result) -> dict:
    """JSON으로 내보내기 좋은 요약."""
    ex = {k: v for k, v in r.extra.items() if isinstance(v, (int, float, str, bool, type(None)))}
    return {"status": r.status, "cost_min": None if r.cost == float("inf") else round(r.cost / 60, 2),
            "real_min": None if r.real == float("inf") else round(r.real / 60, 2),
            "feasible": r.feasible, "expanded": r.expanded, "iterations": r.iterations,
            "runtime_ms": round(r.runtime_ms, 2), "extra": ex}


def with_config(cfg: Config, **kw) -> Config:
    return dataclasses.replace(cfg, **kw)
