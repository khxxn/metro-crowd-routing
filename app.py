#!/usr/bin/env python3
"""혼잡도 길찾기 UI 프로그램 (표준 라이브러리만 사용).

  python app.py            →  http://127.0.0.1:8765 를 브라우저로 연다
  python app.py --port 9000 --open
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
if sys.version_info < (3, 10):
    sys.exit("Python 3.10 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치하세요.")

import graph as g  # noqa: E402
from algorithms.common import OK  # noqa: E402
from algorithms.astar import astar  # noqa: E402
from algorithms.larac import larac  # noqa: E402
import experiments.run as xrun  # noqa: E402
from experiments.evaluate import evaluate  # noqa: E402
from routelib import ALGO_LABELS, describe, line_label, result_summary, run_algo, with_config  # noqa: E402

UI = ROOT / "ui"
RESULTS = ROOT / "results"
LOCK = threading.Lock()  # 휴리스틱 캐시 등 공유 상태를 보호
G: g.Graph | None = None
GEO: dict | None = None

CONTENT = {".html": "text/html; charset=utf-8", ".png": "image/png", ".csv": "text/csv; charset=utf-8",
           ".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".js": "text/javascript",
           ".css": "text/css", ".json": "application/json"}
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def load_geo():
    nodes = []
    files = [ROOT / "data" / "station_nodes.csv"]   # 서울 1~9호선만
    for path in files:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    lat, lon = float(r["lat"]), float(r["lon"])
                except (ValueError, KeyError):
                    continue
                nodes.append({"id": r["id"], "name": r["station_name"], "line": r["line_key"],
                              "lat": lat, "lon": lon, "ext": False})
    ids = {n["id"] for n in nodes}
    edges = [[e.u, e.v] for e in G.ride_edges if e.u in ids and e.v in ids]
    return {"nodes": nodes, "edges": edges}


def parse_cfg(q):
    def f(key, default, cast=float):
        v = q.get(key, [None])[0]
        return default if v in (None, "") else cast(v)

    mode = q.get("mode", ["A"])[0]
    return g.Config(alpha=f("alpha", 0.00631), c0=f("c0", 33.75), slack_sec=int(f("slack", 300)), mode=mode)


def api_stations(_q):
    out = {}
    for nid, (name, line) in G.nodes.items():
        out.setdefault(name, set()).add(line)
    return [{"name": n, "lines": [line_label(x) for x in sorted(v)]} for n, v in sorted(out.items())]


def api_lines(_q):
    """호선별 역 목록. 역 순서는 데이터 파일의 노드 순서(노선을 따라 나열된 순서)다."""
    seq: dict[str, list[str]] = {}
    for _nid, (name, line) in G.nodes.items():
        names = seq.setdefault(line, [])
        if name not in names:
            names.append(name)
    keys = sorted(seq, key=lambda k: (0, int(k), "") if k.isdigit() else (1, 0, line_label(k)))
    return [{"key": k, "label": line_label(k), "stations": seq[k], "estimated": False} for k in keys]


def api_route(q):
    src, dst = q["src"][0].strip(), q["dst"][0].strip()
    day = q.get("day", ["weekday"])[0]
    hhmm = q.get("time", ["08:30"])[0]
    if day not in g.DAYS:
        raise ValueError("요일은 weekday, sat, sun 중 하나입니다")
    if not TIME_RE.match(hhmm):
        raise ValueError("시각은 HH:MM 형식입니다 (예: 08:30, 24:30)")
    cfg = parse_cfg(q)
    algos = [a for a in q.get("algos", ["astar,larac,ref"])[0].split(",") if a]
    dep = g.parse_hhmm(hhmm)
    out = []
    with LOCK:
        G.nodes_by_name(src)
        G.nodes_by_name(dst)
        for a in algos:
            r = run_algo(G, a, src, dst, day, dep, cfg)
            item = {"algo": a, "label": ALGO_LABELS.get(a, a), **result_summary(r)}
            if r.status == OK:
                item["detail"] = describe(G, r, day, dep, cfg)
                item["path"] = r.path
                ev = evaluate(G, r.edges, day, dep, cfg)
                item["cost_A_min"] = round(ev["cost_A"] / 60, 2)
                item["cost_B_min"] = round(ev["cost_B"] / 60, 2)
            out.append(item)
    return {"src": src, "dst": dst, "day": day, "time": hhmm, "mode": cfg.mode, "slack": cfg.slack_sec,
            "alpha": cfg.alpha, "results": out}


def api_curve(q):
    """출발 시각(39칸)별 점수: 점수 최소(A*)와, 허용 시간 안에서 고른 LARAC."""
    src, dst = q["src"][0].strip(), q["dst"][0].strip()
    day = q.get("day", ["weekday"])[0]
    cfg = with_config(parse_cfg(q), mode="A")
    pts = []
    with LOCK:
        G.nodes_by_name(src)
        G.nodes_by_name(dst)
        for k in range(g.N_SLOTS):
            dep = g.SLOT_BASE + k * g.SLOT_SEC
            cheap = astar(G, src, dst, day, dep, cfg)
            lar = larac(G, src, dst, day, dep, cfg)
            if cheap.status != OK or lar.status != OK:
                continue
            pts.append({"time": f"{dep // 3600 % 24:02d}:{dep % 3600 // 60:02d}",
                        "cost_cheapest": round(cheap.cost / 60, 2), "cost_larac": round(lar.cost / 60, 2),
                        "real_cheapest": round(cheap.real / 60, 2), "real_larac": round(lar.real / 60, 2)})
    return {"src": src, "dst": dst, "day": day, "points": pts}


EXP_ALGOS = ("astar", "larac", "ref")
_EXP_CACHE: dict = {}


def api_line_experiment(q):
    """선택한 호선(1~9 또는 all)에서 출발·도착이 모두 그 호선인 무작위 쌍을 같은 조건으로 돌려 비교한다."""
    line = q.get("line", ["all"])[0]
    if line != "all" and line not in {str(i) for i in range(1, 10)}:
        raise ValueError("호선은 1~9 또는 all 입니다")
    n = max(5, min(60, int(q.get("n", ["25"])[0])))
    day = q.get("day", ["weekday"])[0]
    hhmm = q.get("time", ["08:30"])[0]
    if day not in g.DAYS:
        raise ValueError("요일은 weekday, sat, sun 중 하나입니다")
    if not TIME_RE.match(hhmm):
        raise ValueError("시각은 HH:MM 형식입니다 (예: 08:30)")
    cfg = parse_cfg(q)
    key = (line, n, day, hhmm, cfg.mode, cfg.slack_sec, cfg.alpha, cfg.c0)
    if key in _EXP_CACHE:
        return _EXP_CACHE[key]
    dep = g.parse_hhmm(hhmm)
    with LOCK:
        ods = (xrun.sample_ods(G, n, 42, day, hhmm) if line == "all"
               else xrun.sample_line_ods(G, line, n, 42, day, hhmm))
        pairs, per = [], {a: [] for a in EXP_ALGOS}
        for s_name, d_name in ods:
            row = {"src": s_name, "dst": d_name}
            res = {}
            for a in EXP_ALGOS:
                r, ms = xrun._timed(G, a, s_name, d_name, day, dep, cfg, 3)
                res[a] = (r, ms)
                row[a] = None if r.status != OK else {"cost_min": round(r.cost / 60, 2), "real_min": round(r.real / 60, 2),
                                                      "feasible": r.feasible, "expanded": r.expanded, "runtime_ms": round(ms, 2)}
            ref = res["ref"][0]
            for a in EXP_ALGOS:
                r, ms = res[a]
                if r.status != OK or ref.status != OK:
                    continue
                gap = (r.cost - ref.cost) / ref.cost * 100 if ref.cost > 0 else 0.0
                per[a].append({"gap": gap, "ms": ms, "exp": r.expanded, "feas": r.feasible is not False,
                               "cost": r.cost / 60, "real": r.real / 60})
            pairs.append(row)
    stats = []
    for a in EXP_ALGOS:
        P = per[a]
        if not P:
            stats.append({"algo": a, "label": ALGO_LABELS[a], "n": 0})
            continue
        feas = [x for x in P if x["feas"]]
        gaps = sorted(x["gap"] for x in feas) or [0.0]
        ms = sorted(x["ms"] for x in P)
        stats.append({"algo": a, "label": ALGO_LABELS[a], "n": len(P), "feasible_pct": round(100 * len(feas) / len(P), 1),
                      "exact_pct": round(100 * sum(1 for x in feas if abs(x["gap"]) < 1e-6) / max(1, len(feas)), 1),
                      "gap_mean_pct": round(sum(gaps) / len(gaps), 3), "gap_max_pct": round(gaps[-1], 3),
                      "cost_mean_min": round(sum(x["cost"] for x in P) / len(P), 2),
                      "real_mean_min": round(sum(x["real"] for x in P) / len(P), 2),
                      "runtime_ms_median": round(ms[len(ms) // 2], 2), "runtime_ms_total": round(sum(ms), 1),
                      "expanded_mean": round(sum(x["exp"] for x in P) / len(P), 1)})
    out = {"line": line, "label": "전체 1~9호선" if line == "all" else f"{line}호선", "n_pairs": len(pairs), "day": day,
           "time": hhmm, "mode": cfg.mode, "slack": cfg.slack_sec, "alpha": cfg.alpha, "stats": stats, "pairs": pairs}
    _EXP_CACHE[key] = out
    return out


def _csv_rows(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def api_results(_q):
    figs = [{"file": p.name} for p in sorted(RESULTS.glob("fig_*.png"))]
    meta = (RESULTS / "meta.txt").read_text(encoding="utf-8") if (RESULTS / "meta.txt").exists() else ""
    pc = (RESULTS / "path_compare.md").read_text(encoding="utf-8") if (RESULTS / "path_compare.md").exists() else ""
    return {"summary": _csv_rows(RESULTS / "summary.csv"), "by_time": _csv_rows(RESULTS / "summary_by_time.csv"),
            "sensitivity": _csv_rows(RESULTS / "sensitivity.csv"), "figs": figs, "meta": meta, "path_compare": pc}


ROUTES = {"/api/stations": api_stations, "/api/lines": api_lines, "/api/route": api_route, "/api/curve": api_curve, "/api/line_experiment": api_line_experiment,
          "/api/results": api_results, "/api/geo": lambda _q: GEO, "/api/labels": lambda _q: ALGO_LABELS}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ROUTES:
                return self._json(200, ROUTES[u.path](q))
            if u.path in ("/", "/index.html"):
                return self._send(200, (UI / "index.html").read_bytes(), CONTENT[".html"])
            if u.path.startswith("/results/"):
                f = (RESULTS / u.path[len("/results/"):]).resolve()
                if RESULTS.resolve() in f.parents and f.is_file() and f.suffix in CONTENT:
                    return self._send(200, f.read_bytes(), CONTENT[f.suffix])
            return self._json(404, {"error": "not found"})
        except KeyError as e:
            return self._json(400, {"error": str(e.args[0]) if e.args else "역 이름을 찾을 수 없습니다"})
        except (ValueError, IndexError) as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):
        if "--quiet" not in sys.argv:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main(argv=None):
    global G, GEO
    ap = argparse.ArgumentParser(description="혼잡도 길찾기 UI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="브라우저를 자동으로 연다")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    G = g.load_graph(ROOT / "data", extended=False)   # 서울 1~9호선만
    GEO = load_geo()
    srv = None
    for port in range(a.port, a.port + 20):  # 포트가 이미 쓰이고 있으면 다음 포트를 쓴다
        try:
            srv = ThreadingHTTPServer((a.host, port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        sys.exit(f"포트 {a.port}~{a.port + 19}가 모두 사용 중입니다. --port 로 다른 포트를 지정하세요.")
    url = f"http://{a.host}:{srv.server_address[1]}"
    scope = f"서울 1~9호선 {len(G.by_name)}개 역"
    print(f"혼잡도 길찾기 UI: {url}   ({scope}, 끝내려면 Ctrl+C)", flush=True)
    if a.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    main()
