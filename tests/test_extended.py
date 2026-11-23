"""수도권 확장(data/ext) 검증. 팀 기준값 검증은 확장 없는 그래프(기본값)로 따로 한다."""
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import graph as g  # noqa: E402
from algorithms.astar import astar  # noqa: E402
from algorithms.common import OK  # noqa: E402
from algorithms.dijkstra import dijkstra  # noqa: E402
from algorithms.larac import larac  # noqa: E402
from algorithms.reference import reference  # noqa: E402
from routelib import describe, line_label  # noqa: E402


@pytest.fixture(scope="module")
def GE():
    return g.load_graph(ROOT / "data", extended=True)


@pytest.fixture(scope="module")
def GB():
    return g.load_graph(ROOT / "data")


def test_counts(GE, GB):
    assert GB.counts == g.EXPECTED and not GB.ext_nodes  # 기본값은 팀 데이터 그대로
    assert len(GE.nodes) == 801 and len(GE.by_name) == 658
    assert len(GE.ext_nodes) == 343
    assert len(GE.ride_edges) == 896 + 652 and len(GE.transfer_edges) == 114 + 232
    assert GE.counts["total_nodes"] == 801 and GE.counts["total_station_names"] == 658
    # 팀 데이터는 바뀌지 않는다
    assert GE.cong == GB.cong
    assert all(GE.trv[k] == v for k, v in GB.trv.items())


def test_ext_values(GE):
    # 서울(0150) → 9031 환승: ETL 767초 - 페널티 180 = 587초, 2.204배
    e = next(e for e in GE.adj["0150"] if e.v == "9031")
    for day in g.DAYS:
        for slot in (0, 6, 38):
            real, cost = GE.edge_values(e, day, slot)
            assert real == 587 and cost == pytest.approx(2.204 * 587, abs=0.01)
    assert GE.edge_values(e, "weekday", 6, g.Config(transfer_mult=3.0))[1] == pytest.approx(3.0 * 587)
    # 탑승: 점수 = 실제 시간(혼잡도 0), 05:30~24:00 운행 가정
    r = next(e for e in GE.adj["9001"] if e.v == "9004")
    assert GE.edge_values(r, "sat", 12) == (335.0, 335.0)
    assert r.first_dep("sun") == 19800 and r.last_dep("weekday") == 86400


def test_ext_ride_cost_equals_real_and_cost_ge_real(GE):
    for e in GE.ride_edges[896:]:
        real, cost = GE.edge_values(e, "weekday", 6)
        assert real == cost == e.seconds
    for e in GE.ride_edges + GE.transfer_edges:
        v = GE.edge_values(e, "weekday", 6)
        assert v is None or v[1] >= v[0] > 0


def test_every_new_node_has_edges(GE):
    has_in = {e.v for edges in GE.adj.values() for e in edges}
    for n in GE.ext_nodes:
        assert GE.adj[n] and n in has_in


def test_all_lines_present(GE):
    lines = {GE.nodes[n][1] for n in GE.nodes}
    assert len(lines) == 9 + 16
    assert {"신분당선", "수인분당선", "경의·중앙선", "인천지하철 1호선", "GTX-A"} <= lines


def test_everything_reachable_from_seoul(GE):
    rng = random.Random(5)
    names = sorted(GE.by_name)
    bad = []
    for d in rng.sample(names, 120):
        r = dijkstra(GE, "서울", d, "weekday", g.parse_hhmm("12:00"), g.Config(), "real")
        if d != "서울" and r.status != OK:
            bad.append(d)
    assert not bad, bad


def test_extension_never_makes_things_worse(GE, GB):
    """간선만 더했으니 기존 역 사이 최적 점수는 늘 수 없다."""
    rng = random.Random(9)
    names = sorted(GB.by_name)
    improved = 0
    for _ in range(60):
        s, d = rng.sample(names, 2)
        t = g.parse_hhmm(rng.choice(["08:30", "12:30", "18:00"]))
        a = dijkstra(GB, s, d, "weekday", t, g.Config(), "cost")
        b = dijkstra(GE, s, d, "weekday", t, g.Config(), "cost")
        if a.status == OK:
            assert b.status == OK and b.cost <= a.cost + 1e-6
            improved += b.cost < a.cost - 1e-6
    assert improved >= 1  # 새 노선 덕에 실제로 빨라지는 사례가 있다


def test_astar_ref_larac_on_extended(GE):
    rng = random.Random(21)
    names = sorted(GE.by_name)
    n_ok = 0
    for _ in range(80):
        s, d = rng.sample(names, 2)
        day, t = rng.choice(["weekday", "sat", "sun"]), g.parse_hhmm(rng.choice(["08:30", "12:30", "18:00"]))
        cfg = g.Config()
        a = astar(GE, s, d, day, t, cfg)
        b = dijkstra(GE, s, d, day, t, cfg, "cost")
        assert a.status == b.status
        if b.status != OK:
            continue
        n_ok += 1
        assert a.cost == pytest.approx(b.cost, abs=1e-6)
        ref = reference(GE, s, d, day, t, cfg)
        lr = larac(GE, s, d, day, t, cfg)
        assert ref.status == lr.status == OK
        assert b.cost - 1e-6 <= ref.cost <= lr.cost + 1e-6
        assert lr.real <= lr.extra["T"] + 1e-6
    assert n_ok >= 70


def test_api_lines_and_stations_extended(GE, monkeypatch):
    import app

    monkeypatch.setattr(app, "G", GE)
    lines = app.api_lines({})
    assert len(lines) == 25
    assert [x["label"] for x in lines[:9]] == [f"{i}호선" for i in range(1, 10)]
    # 화면은 1~9호선만 쓰므로 '추정' 표시는 더 이상 쓰지 않는다 (확장 그래프를 직접 넣어도 False)
    assert not any(x["estimated"] for x in lines)
    by = {x["label"]: x["stations"] for x in lines}
    assert "판교" in by["신분당선"] and "선릉" in by["수인분당선"] and "선릉" in by["2호선"]
    assert len({n for x in lines for n in x["stations"]}) == 658
    st = app.api_stations({})
    assert len(st) == 658
    assert {"name": "판교", "lines": ["경강선", "신분당선"]} in st


def test_describe_marks_estimated(GE):
    r = dijkstra(GE, "선릉", "수서", "weekday", g.parse_hhmm("08:30"), g.Config(), "cost")
    d = describe(GE, r, "weekday", g.parse_hhmm("08:30"), g.Config())
    assert d["estimated"] and any(s["estimated"] for s in d["segments"])
    assert any(s["type"] == "ride" and s["line_label"] == "수인분당선" for s in d["segments"])
    assert line_label("2") == "2호선" and line_label("신분당선") == "신분당선"


def test_late_night_on_ext_lines(GE):
    # 05:30~24:00 가정: 새 노선에만 있는 역에서 24:30에 출발하면 첫 탑승이 막힌다
    new_only = sorted({GE.nodes[n][0] for n in GE.ext_nodes} - {GE.nodes[n][0] for n in GE.nodes if n not in GE.ext_nodes})
    assert len(new_only) == 254
    s, d = new_only[0], "서울"
    r = dijkstra(GE, s, d, "weekday", g.parse_hhmm("24:30"), g.Config(), "real")
    assert r.status == "no_service"
