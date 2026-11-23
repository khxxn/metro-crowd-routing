#!/usr/bin/env python3
"""7단계 실험 러너.

  python -m experiments.run --days weekday,sat --times 08:30,12:30,18:00,23:00 --pairs 300 --seed 42 --slack 300

산출물(results/): raw.csv, summary.csv, group_summary.csv, sensitivity.csv, time_curve.csv,
                  path_compare.md, 그림 PNG (experiments/plots.py)
"""
from __future__ import annotations

import argparse
import csv
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import graph as g  # noqa: E402
from algorithms.common import OK  # noqa: E402
from algorithms.dijkstra import dijkstra  # noqa: E402
from experiments.evaluate import evaluate  # noqa: E402
from routelib import run_algo  # noqa: E402

RESULTS = ROOT / "results"
ALGO_KEYS = ["dijkstra_real", "dijkstra_cost", "astar", "larac", "ga", "ref"]
GA_SEEDS = 30

_G = None


def _init(data_dir):
    global _G
    _G = g.load_graph(data_dir)


def line_share(G, edges, line):
    rides = [e for e in edges if e.kind == "ride"]
    if not rides:
        return 0.0
    return sum(G.nodes[e.u][1] == line for e in rides) / len(rides)


# ----------------------------------------------------------------------------- OD 선정
def sample_ods(G, n, seed, day="weekday", hhmm="08:30", min_real=600, pool=None, max_tries=20000):
    """역명 풀에서 무작위 쌍(시드 고정). 실제시간 10분 미만 쌍은 제외."""
    rng = random.Random(seed)
    names = sorted(pool or G.by_name)
    dep = g.parse_hhmm(hhmm)
    cfg = g.Config()
    out, seen, tries = [], set(), 0
    while len(out) < n and tries < max_tries:
        tries += 1
        s, d = rng.sample(names, 2)
        if (s, d) in seen:
            continue
        seen.add((s, d))
        r = dijkstra(G, s, d, day, dep, cfg, "real")
        if r.status == OK and r.real >= min_real:
            out.append((s, d))
    return out


def sample_line_ods(G, line, n, seed, day="weekday", hhmm="08:30", min_share=0.8, min_real=600, max_tries=40000):
    """line호선 위주 OD: 출·도착역 모두 그 호선 노드가 있고, 점수 최소 경로의 탑승 간선 중 min_share 이상이 그 호선."""
    rng = random.Random(seed * 1000 + int(line))
    pool = sorted({G.nodes[nid][0] for nid in G.nodes if G.nodes[nid][1] == line})
    dep = g.parse_hhmm(hhmm)
    cfg = g.Config()
    out, seen, tries = [], set(), 0
    while len(out) < n and tries < max_tries:
        tries += 1
        s, d = rng.sample(pool, 2)
        if (s, d) in seen:
            continue
        seen.add((s, d))
        r = dijkstra(G, s, d, day, dep, cfg, "cost")
        if r.status == OK and r.real >= min_real and line_share(G, r.edges, line) >= min_share:
            out.append((s, d))
    return out


# ----------------------------------------------------------------------------- 한 질의 측정
def _timed(G, algo, s, d, day, dep, cfg, repeats):
    """5회 중앙값 실행시간(ms). 휴리스틱 캐시를 매번 비워 '처음 질의' 시간을 잰다."""
    times, r = [], None
    for _ in range(repeats):
        G.__dict__.pop("_astar_cache", None)
        r = run_algo(G, algo, s, d, day, dep, cfg)
        times.append(r.runtime_ms)
    return r, statistics.median(times)


def measure_query(args):
    group, s, d, day, hhmm, slack, alpha, c0, repeats = args
    G = _G
    cfg = g.Config(alpha=alpha, c0=c0, slack_sec=slack)
    dep = g.parse_hhmm(hhmm)
    rows = []
    ref, ref_ms = _timed(G, "ref", s, d, day, dep, cfg, repeats)
    cost_ref = ref.cost if ref.status == OK else None
    T = ref.extra.get("T")
    shortest = ref.extra.get("shortest_real")
    for algo in ALGO_KEYS:
        warm = None
        if algo == "ref":
            r, ms = ref, ref_ms
        elif algo == "ga":
            r = run_algo(G, "ga", s, d, day, dep, cfg, seed=0)
            ms = r.runtime_ms
        else:
            r, ms = _timed(G, algo, s, d, day, dep, cfg, repeats)
        if algo in ("astar", "ref"):  # 휴리스틱을 재사용할 때(같은 도착역·요일의 두 번째 질의부터)
            run_algo(G, algo, s, d, day, dep, cfg)  # 캐시 채우기
            warm = statistics.median(run_algo(G, algo, s, d, day, dep, cfg).runtime_ms for _ in range(repeats))
        row = {"group": group, "day": day, "time": hhmm, "src": s, "dst": d, "algo": algo,
               "status": r.status, "T_min": None, "shortest_real_min": None}
        if r.status == OK:
            ev = evaluate(G, r.edges, day, dep, cfg)
            feasible = (r.real <= T + 1e-6) if T is not None else None
            row.update({
                "cost_A": round(ev["cost_A"], 3), "cost_B": round(ev["cost_B"], 3),
                "real": round(r.real, 3), "real_B": round(ev["real_B"], 3),
                "valid_A": ev["valid_A"], "valid_B": ev["valid_B"],
                "feasible": feasible,
                "gap_vs_ref": None if not cost_ref else round((r.cost - cost_ref) / cost_ref * 100, 4),
                "transfers": sum(e.kind == "transfer" for e in r.edges), "n_edges": len(r.edges),
                "path": "|".join(r.path),
                "T_min": round(T / 60, 3) if T else None,
                "shortest_real_min": round(shortest / 60, 3) if shortest else None,
            })
        row.update({"runtime_ms": round(ms, 3), "runtime_warm_ms": None if warm is None else round(warm, 3),
                    "expanded": r.expanded, "iterations": r.iterations,
                    "bound_active": r.extra.get("bound_active")})
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------- 보조 산출물
def time_curve(G, s, d, day, slack, alpha, c0):
    cfg = g.Config(alpha=alpha, c0=c0, slack_sec=slack)
    rows = []
    for k in range(0, 39):
        dep = g.SLOT_BASE + k * g.SLOT_SEC
        cheap = dijkstra(G, s, d, day, dep, cfg, "cost")
        fast = dijkstra(G, s, d, day, dep, cfg, "real")
        if cheap.status != OK or fast.status != OK:
            continue
        ev = evaluate(G, fast.edges, day, dep, cfg)
        rows.append({"time": f"{dep // 3600 % 24:02d}:{dep % 3600 // 60:02d}", "cost_min_cost": cheap.cost / 60,
                     "cost_fastest_path": ev["cost_A"] / 60, "real_min_cost": cheap.real / 60,
                     "real_fastest": fast.real / 60})
    return rows


def sensitivity(G, ods, day, hhmm, alphas, slacks, c0):
    dep = g.parse_hhmm(hhmm)
    rows = []
    for alpha in alphas:
        for slack in slacks:
            cfg = g.Config(alpha=alpha, c0=c0, slack_sec=slack)
            n = diff_l = diff_c = active = 0
            for s, d in ods:
                fast = dijkstra(G, s, d, day, dep, cfg, "real")
                if fast.status != OK:
                    continue
                cheap = dijkstra(G, s, d, day, dep, cfg, "cost")
                lr = run_algo(G, "larac", s, d, day, dep, cfg)
                if lr.status != OK:
                    continue
                n += 1
                diff_l += lr.path != fast.path
                diff_c += cheap.path != fast.path
                active += bool(lr.extra.get("bound_active"))
            rows.append({"alpha": alpha, "slack_sec": slack, "n_od": n,
                         "larac_differs_pct": round(100 * diff_l / n, 2) if n else None,
                         "cost_min_differs_pct": round(100 * diff_c / n, 2) if n else None,
                         "bound_active_pct": round(100 * active / n, 2) if n else None})
    return rows


def write_csv(path, rows):
    if not rows:
        return
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def summarize(rows, key_fields):
    groups = {}
    for r in rows:
        groups.setdefault(tuple(r.get(k) for k in key_fields), []).append(r)
    out = []
    for key, rs in groups.items():
        ok = [r for r in rs if r["status"] == OK]
        row = dict(zip(key_fields, key))
        row["n"] = len(rs)
        row["n_ok"] = len(ok)
        if ok:
            feas = [r for r in ok if r.get("feasible")]
            gaps = [r["gap_vs_ref"] for r in feas if _num(r.get("gap_vs_ref")) is not None]
            row["feasible_pct"] = round(100 * len(feas) / len(ok), 2) if any(r.get("feasible") is not None for r in ok) else None
            row["gap_mean_pct"] = round(statistics.mean(gaps), 4) if gaps else None
            row["gap_median_pct"] = round(statistics.median(gaps), 4) if gaps else None
            row["gap_max_pct"] = round(max(gaps), 4) if gaps else None
            row["exact_pct"] = round(100 * sum(abs(x) < 1e-6 for x in gaps) / len(gaps), 2) if gaps else None
            row["cost_A_mean_min"] = round(statistics.mean(r["cost_A"] for r in ok) / 60, 3)
            row["cost_B_mean_min"] = round(statistics.mean(r["cost_B"] for r in ok) / 60, 3)
            row["real_mean_min"] = round(statistics.mean(r["real"] for r in ok) / 60, 3)
            row["runtime_ms_median"] = round(statistics.median(r["runtime_ms"] for r in ok), 3)
            warm = [r["runtime_warm_ms"] for r in ok if _num(r.get("runtime_warm_ms")) is not None]
            row["runtime_warm_ms_median"] = round(statistics.median(warm), 3) if warm else None
            row["expanded_mean"] = round(statistics.mean(r["expanded"] for r in ok), 1)
            row["iterations_mean"] = round(statistics.mean(r["iterations"] for r in ok), 2)
            ba = [r["bound_active"] for r in ok if r.get("bound_active") is not None]
            row["bound_active_pct"] = round(100 * sum(map(bool, ba)) / len(ba), 2) if ba else None
        out.append(row)
    return out


def path_compare(G, rows, n=2):
    """대표 OD 2개(점수 최소 경로와 시간 최단 경로가 다른 질의)의 역 목록 비교."""
    by_q = {}
    for r in rows:
        if r["status"] == OK:
            by_q.setdefault((r["group"], r["day"], r["time"], r["src"], r["dst"]), {})[r["algo"]] = r
    picked = [q for q, a in by_q.items() if "dijkstra_real" in a and "dijkstra_cost" in a
              and a["dijkstra_real"]["path"] != a["dijkstra_cost"]["path"] and q[0] == "random" and q[2] == "08:30"
              and a["dijkstra_cost"]["real"] - a["dijkstra_real"]["real"] > 60]
    picked = picked[:n]
    lines = ["# 대표 OD 경로 비교", ""]
    for q in picked:
        group, day, t, s, d = q
        lines += [f"## {s} → {d} ({day} {t})", "",
                  "| 알고리즘 | 점수(분) | 실제(분) | 환승 | 경로(역) |", "|---|---|---|---|---|"]
        for algo in ["dijkstra_real", "dijkstra_cost", "astar", "larac", "ref"]:
            r = by_q[q].get(algo)
            if not r:
                continue
            names = []
            for nid in r["path"].split("|"):
                nm = G.nodes[nid][0]
                if not names or names[-1] != nm:
                    names.append(nm)
            lines.append(f"| {algo} | {r['cost_A'] / 60:.1f} | {r['real'] / 60:.1f} | {r['transfers']} | {' → '.join(names)} |")
        lines.append("")
    return "\n".join(lines), picked


# ----------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="weekday,sat")
    ap.add_argument("--times", default="08:30,12:30,18:00,23:00")
    ap.add_argument("--pairs", type=int, default=300)
    ap.add_argument("--line-pairs", type=int, default=25, help="2호선·5호선 위주 OD 각각의 수(최소 20)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--slack", type=int, default=300)
    ap.add_argument("--alpha", type=float, default=0.00631)
    ap.add_argument("--c0", type=float, default=33.75)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--sens-pairs", type=int, default=100)
    ap.add_argument("--out", default=str(RESULTS))
    ap.add_argument("--data", default=str(ROOT / "data"))
    a = ap.parse_args(argv)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    G = g.load_graph(a.data)
    days, times = a.days.split(","), a.times.split(",")

    ods = sample_ods(G, a.pairs, a.seed)
    line2 = sample_line_ods(G, "2", a.line_pairs, a.seed)
    line5 = sample_line_ods(G, "5", a.line_pairs, a.seed)
    print(f"OD: 무작위 {len(ods)}쌍, 2호선 위주 {len(line2)}쌍, 5호선 위주 {len(line5)}쌍")
    if len(line2) < 20 or len(line5) < 20:
        print("경고: 노선 위주 OD가 20쌍 미만입니다", file=sys.stderr)

    tasks = []
    for (s, d) in ods:
        for day in days:
            for hhmm in times:
                tasks.append(("random", s, d, day, hhmm, a.slack, a.alpha, a.c0, a.repeats))
    for grp, lst in (("line2", line2), ("line5", line5)):
        for (s, d) in lst:  # 노선 특성 실험은 평일 08:30 고정
            tasks.append((grp, s, d, "weekday", "08:30", a.slack, a.alpha, a.c0, a.repeats))
    print(f"측정 {len(tasks)}건 × 알고리즘 {len(ALGO_KEYS)}개 × 반복 {a.repeats}회")

    rows = []
    with ProcessPoolExecutor(a.workers, initializer=_init, initargs=(a.data,)) as ex:
        for i, rs in enumerate(ex.map(measure_query, tasks, chunksize=8)):
            rows.extend(rs)
            if (i + 1) % 500 == 0:
                print(f"  {i + 1}/{len(tasks)}  {time.time() - t0:.0f}s")
    write_csv(out / "raw.csv", rows)
    summ = summarize(rows, ["group", "algo"])
    write_csv(out / "summary.csv", summ)
    write_csv(out / "summary_by_time.csv", summarize([r for r in rows if r["group"] == "random"],
                                                     ["algo", "day", "time"]))

    # 대표 OD, 출발 시각별 점수 곡선, 민감도
    md, picked = path_compare(G, rows)
    (out / "path_compare.md").write_text(md, encoding="utf-8")
    curve_od = picked[0][3:5] if picked else ods[0]
    write_csv(out / "time_curve.csv", [{"src": curve_od[0], "dst": curve_od[1], **r}
                                       for r in time_curve(G, curve_od[0], curve_od[1], "weekday", a.slack, a.alpha, a.c0)])
    write_csv(out / "sensitivity.csv", sensitivity(G, ods[:a.sens_pairs], "weekday", "08:30",
                                                   [0.004, 0.00631, 0.009], [180, 300, 600], a.c0))
    (out / "meta.txt").write_text(
        f"pairs={len(ods)} line2={len(line2)} line5={len(line5)} days={a.days} times={a.times} seed={a.seed} "
        f"slack={a.slack} alpha={a.alpha} c0={a.c0} repeats={a.repeats} rows={len(rows)} seconds={time.time() - t0:.0f}\n"
        f"line_od_rule=both stations on the line, cost-optimal path has >=80% ride edges on that line, real>=10min\n"
        f"runtime=cold heuristic cache each repeat, median of {a.repeats}\n", encoding="utf-8")

    from experiments import plots

    plots.make_all(out, G)
    print(f"완료 {time.time() - t0:.0f}s → {out}")


if __name__ == "__main__":
    main()
