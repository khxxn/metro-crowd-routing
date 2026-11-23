#!/usr/bin/env python3
"""경로 탐색 CLI (8단계).

  python route.py 선릉 수서 weekday 08:30 --algo astar --mode A
  python route.py 개화 중앙보훈병원 weekday 18:00 --algo larac --slack 300
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if sys.version_info < (3, 10):
    sys.exit("Python 3.10 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치하세요.")

import graph as g  # noqa: E402
from algorithms.common import INFEASIBLE, NO_ROUTE, NO_SERVICE, NOT_IMPLEMENTED, OK  # noqa: E402
from routelib import ALGO_LABELS, ALGOS, describe, hhmm, run_algo  # noqa: E402


def print_result(G, r, algo, day, depart, cfg, out=None):
    out = out or sys.stdout  # 호출 시점의 stdout (기본 인자로 두면 정의 시점에 묶인다)
    p = lambda *a: print(*a, file=out)  # noqa: E731
    label = ALGO_LABELS.get(algo, algo)
    if r.status == NOT_IMPLEMENTED:
        p(f"[{label}] 아직 구현되지 않았습니다: {r.extra.get('message', '')}")
        return
    if r.status == NO_SERVICE:
        p(f"[{label}] 차가 없어요 (첫차·막차 또는 환승 불가로 막힘)")
        return
    if r.status == NO_ROUTE:
        p(f"[{label}] 경로가 없습니다")
        return
    if r.status == INFEASIBLE:
        p(f"[{label}] 허용시간 안에 도착하는 경로가 없습니다")
        return
    d = describe(G, r, day, depart, cfg)
    p(f"[{label}] 방식 {cfg.mode}")
    for s in d["segments"]:
        if s["type"] == "ride":
            cong = "혼잡도 데이터 없음(0 가정)" if s["estimated"] else f"최대 혼잡 {s['max_cong']:.0f}%"
            p(f"  {s['at']}  {s['line_label']}  {' → '.join(s['stations'])}  ({s['minutes']}분, {cong})")
        else:
            est = ", 추정" if s["estimated"] else ""
            p(f"  {s['at']}  환승  {s['station']}  {s['from_label']} → {s['to_label']}  ({s['minutes']}분{est})")
    p(f"  출발 {hhmm(depart)}  도착 {d['arrive']}  정거장 {d['stations']}개  환승 {d['transfers']}회")
    p(f"  점수 {r.cost / 60:.1f}분  |  실제 {r.real / 60:.1f}분")
    if d["estimated"]:
        p("  ※ 이 경로는 팀 데이터에 없는 수도권 노선을 포함합니다. 혼잡도 0, 첫차·막차 05:30~24:00, 환승 시간은 ETL 추정값입니다.")
    ex = r.extra
    bits = [f"확장 {r.expanded}", f"반복 {r.iterations}", f"{r.runtime_ms:.1f}ms"]
    if "T" in ex and ex["T"] is not None:
        bits.append(f"허용시간 T={ex['T'] / 60:.1f}분")
    if "bound_active" in ex:
        bits.append(f"제약 활성={ex['bound_active']}")
    if "lambda" in ex:
        bits.append(f"λ={ex['lambda']:.4f}")
    if r.feasible is not None:
        bits.append(f"feasible={r.feasible}")
    p("  " + "  ".join(bits))


def main(argv=None):
    ap = argparse.ArgumentParser(description="혼잡도 경로 탐색")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("day", choices=g.DAYS)
    ap.add_argument("time", help="HH:MM (24:30처럼 24시 이후도 가능)")
    ap.add_argument("--algo", choices=ALGOS, default="astar")
    ap.add_argument("--mode", choices=("A", "B"), default="A")
    ap.add_argument("--weight", choices=("cost", "real"), default="cost", help="dijkstra 전용")
    ap.add_argument("--slack", type=int, default=300)
    ap.add_argument("--alpha", type=float, default=0.00631)
    ap.add_argument("--c0", type=float, default=33.75)
    ap.add_argument("--transfer-mult", type=float, default=None)
    ap.add_argument("--data", default=str(Path(__file__).resolve().parent / "data"))
    ap.add_argument("--base", action="store_true", help="수도권 확장 없이 팀 데이터(서울 1~9호선)만 사용")
    a = ap.parse_args(argv)

    G = g.load_graph(a.data, extended=not a.base)
    cfg = g.Config(alpha=a.alpha, c0=a.c0, transfer_mult=a.transfer_mult, slack_sec=a.slack, mode=a.mode)
    try:
        r = run_algo(G, a.algo, a.src, a.dst, a.day, g.parse_hhmm(a.time), cfg, a.weight)
    except KeyError as e:
        print(e, file=sys.stderr)
        return 2
    print_result(G, r, a.algo, a.day, g.parse_hhmm(a.time), cfg)
    return 0 if r.status in (OK, NOT_IMPLEMENTED) else 1


if __name__ == "__main__":
    sys.exit(main())
