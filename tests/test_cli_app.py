"""8단계 검증: CLI(route.py)와 UI 서버(app.py)의 API 함수."""
import io
import sys
from pathlib import Path
from urllib.parse import parse_qs

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app  # noqa: E402
import graph as g  # noqa: E402
import route  # noqa: E402
from experiments.evaluate import edges_from_path, evaluate  # noqa: E402
from routelib import hhmm, run_algo  # noqa: E402


@pytest.fixture(scope="module")
def G():
    G = g.load_graph(ROOT / "data")
    app.G = G
    app.GEO = app.load_geo()
    return G


def cli(argv, capsys):
    # 팀 기준값(CLAUDE.md)은 서울 1~9호선 데이터 기준이라 --base로 확장 노선을 뺀다
    rc = route.main(argv + ["--base"])
    return rc, capsys.readouterr().out


@pytest.mark.parametrize("algo", ["dijkstra", "astar", "larac", "ref"])
def test_cli_reference_query(algo, capsys, G):
    rc, out = cli(["선릉", "수서", "weekday", "08:30", "--algo", algo], capsys)
    assert rc == 0
    assert "점수 33.2분" in out and "실제 27.6분" in out
    assert "선릉" in out and "수서" in out and "환승 1회" in out


def test_cli_no_service(capsys, G):
    rc, out = cli(["선릉", "수서", "sat", "24:30", "--algo", "astar"], capsys)
    assert rc == 1 and "차가 없어요" in out


def test_cli_ga_not_implemented(capsys, G):
    rc, out = cli(["선릉", "수서", "weekday", "08:30", "--algo", "ga"], capsys)
    assert rc == 0 and "구현되지 않았습니다" in out


def test_cli_unknown_station(capsys, G):
    assert route.main(["없는역", "수서", "weekday", "08:30", "--base"]) == 2


def test_cli_mode_b_and_weight_real(capsys, G):
    rc, out = cli(["개화", "중앙보훈병원", "weekday", "18:00", "--algo", "astar", "--mode", "B"], capsys)
    assert rc == 0 and "방식 B" in out and "점수 97.3분" in out
    rc, out = cli(["선릉", "수서", "weekday", "08:30", "--algo", "dijkstra", "--weight", "real"], capsys)
    assert "실제 25.4분" in out


def test_hhmm():
    assert hhmm(30600) == "08:30"
    assert hhmm(88200) == "00:30 (+1일)"


def test_api_route(G):
    q = parse_qs("src=선릉&dst=수서&day=weekday&time=08:30&algos=dijkstra_cost,astar,larac,ref,ga")
    d = app.api_route(q)
    by = {r["algo"]: r for r in d["results"]}
    assert by["astar"]["cost_min"] == pytest.approx(33.23, abs=0.01)
    assert by["ga"]["status"] == "not_implemented"
    assert by["larac"]["extra"]["bound_active"] is False
    assert by["ref"]["detail"]["transfers"] == 1 and by["ref"]["path"][0] in G.nodes
    assert by["astar"]["cost_A_min"] == pytest.approx(by["astar"]["cost_min"], abs=0.01)


def test_api_route_validation(G):
    with pytest.raises(ValueError):
        app.api_route(parse_qs("src=선릉&dst=수서&day=mon&time=08:30"))
    with pytest.raises(ValueError):
        app.api_route(parse_qs("src=선릉&dst=수서&day=weekday&time=8시"))
    with pytest.raises(KeyError):
        app.api_route(parse_qs("src=없는역&dst=수서"))


def test_api_line_experiment(G):
    d = app.api_line_experiment(parse_qs("line=2&n=5&day=weekday&time=08:30"))
    assert d["label"] == "2호선" and d["n_pairs"] == 5
    assert [s["algo"] for s in d["stats"]] == ["astar", "larac", "ref"]
    ref = d["stats"][2]
    assert ref["n"] == 5 and ref["gap_max_pct"] == 0 and ref["exact_pct"] == 100.0
    assert all(s["gap_mean_pct"] >= -1e-9 for s in d["stats"])      # 정답 기준보다 점수가 낮을 수는 없다
    with pytest.raises(ValueError):
        app.api_line_experiment(parse_qs("line=12"))


def test_api_curve_stations_geo_results(G):
    c = app.api_curve(parse_qs("src=선릉&dst=수서&day=weekday"))
    assert len(c["points"]) >= 30 and all(p["cost_cheapest"] <= p["cost_larac"] + 1e-6 for p in c["points"])
    st = app.api_stations({})
    assert len(st) == 404 and {"name": "가락시장", "lines": ["3호선", "8호선"]} in st
    assert len(app.GEO["nodes"]) == 458 and len(app.GEO["edges"]) == 896
    r = app.api_results({})
    assert set(r) >= {"summary", "figs", "sensitivity", "meta", "path_compare"}


def test_api_lines_base(G):
    lines = app.api_lines({})
    assert [x["label"] for x in lines] == [f"{i}호선" for i in range(1, 10)]
    assert all(x["stations"] and not x["estimated"] for x in lines)
    assert "선릉" in next(x for x in lines if x["key"] == "2")["stations"]
    assert len({n for x in lines for n in x["stations"]}) == 404  # 전체 합집합 = 역 이름 404개
    for x in lines:
        assert len(x["stations"]) == len(set(x["stations"]))


def test_evaluate_path_roundtrip(G):
    cfg = g.Config()
    dep = g.parse_hhmm("08:30")
    r = run_algo(G, "astar", "선릉", "수서", "weekday", dep, cfg)
    ev = evaluate(G, r.edges, "weekday", dep, cfg)
    assert ev["cost_A"] == pytest.approx(r.cost) and ev["valid_A"] and ev["valid_B"]
    ev2 = evaluate(G, edges_from_path(G, r.path), "weekday", dep, cfg)  # 노드 순서만으로도 같은 평가
    assert ev2["cost_A"] == pytest.approx(ev["cost_A"])
    assert ev["cost_B"] == pytest.approx(33.2 * 60, abs=6)  # B 기준값 33.2분


def test_server_handler_serves_files(G):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        html = urllib.request.urlopen(base + "/").read().decode("utf-8")
        assert "혼잡도 길찾기" in html
        import json
        d = json.load(urllib.request.urlopen(base + "/api/labels"))
        assert "astar" in d
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(base + "/api/route?src=%EC%97%86%EB%8A%94%EC%97%AD&dst=x")
        assert ei.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(base + "/results/..%2Fapp.py")
        assert ei.value.code == 404
    finally:
        srv.shutdown()
