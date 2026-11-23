"""4·5단계 검증: reference(정확) = 전수 탐색, LARAC은 reference 이상이고 실행 가능하며 LB가 유효."""
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import graph as g  # noqa: E402
from algorithms.common import NO_ROUTE, NO_SERVICE, OK  # noqa: E402
from algorithms.dijkstra import dijkstra  # noqa: E402
from algorithms.larac import larac  # noqa: E402
from algorithms.reference import reference  # noqa: E402
from toy import DAY, DEPART, brute_force, make_toy  # noqa: E402


def toy_cases(n=150):
    rng = random.Random(1)
    for seed in range(n):
        yield seed, rng.choice([0, 30, 120, 300, 900])


@pytest.mark.parametrize("seed,slack", list(toy_cases()))
def test_reference_matches_brute_force(seed, slack):
    G = make_toy(seed)
    cfg = g.Config(slack_sec=slack)
    r = reference(G, "A", "E", DAY, DEPART, cfg)
    shortest = dijkstra(G, "A", "E", DAY, DEPART, cfg, "real")
    if shortest.status != OK:
        assert r.status in (NO_ROUTE, NO_SERVICE)
        return
    best, fastest = brute_force(G, "A", "E", cfg, shortest.real + slack)
    assert fastest == pytest.approx(shortest.real, abs=1e-6)
    assert r.status == OK
    assert r.cost == pytest.approx(best, abs=1e-6)
    assert r.real <= shortest.real + slack + 1e-6


@pytest.mark.parametrize("seed,slack", list(toy_cases(80)))
def test_larac_on_toy(seed, slack):
    G = make_toy(seed)
    cfg = g.Config(slack_sec=slack)
    ref = reference(G, "A", "E", DAY, DEPART, cfg)
    r = larac(G, "A", "E", DAY, DEPART, cfg)
    if ref.status != OK:
        assert r.status != OK
        return
    assert r.status == OK and r.feasible
    assert r.cost >= ref.cost - 1e-6  # 제약 하 최적해보다 좋을 수 없다
    assert r.real <= r.extra["T"] + 1e-6
    if r.extra["bound_active"]:
        assert r.extra["LB"] <= ref.cost + 1e-6  # 라그랑주 하한은 유효한 하한
        assert r.extra["lambda"] > 0
    else:
        cheap = dijkstra(G, "A", "E", DAY, DEPART, cfg, "cost")
        assert r.cost == pytest.approx(cheap.cost, abs=1e-6)


@pytest.fixture(scope="module")
def G():
    return g.load_graph(ROOT / "data")


def real_cases(G, n, seed):
    rng = random.Random(seed)
    names = sorted(G.by_name)
    for _ in range(n):
        s, d = rng.sample(names, 2)
        yield s, d, rng.choice(["weekday", "sat", "sun"]), rng.choice(["08:30", "12:30", "18:00"])


def test_real_data_ordering(G):
    n_active = 0
    for s, d, day, t in real_cases(G, 60, 11):
        dep = g.parse_hhmm(t)
        cfg = g.Config()
        cheap = dijkstra(G, s, d, day, dep, cfg, "cost")
        fast = dijkstra(G, s, d, day, dep, cfg, "real")
        lr = larac(G, s, d, day, dep, cfg)
        ref = reference(G, s, d, day, dep, cfg)
        assert lr.status == ref.status == cheap.status == OK
        T = fast.real + cfg.slack_sec
        assert ref.real <= T + 1e-6 and lr.real <= T + 1e-6
        assert cheap.cost - 1e-6 <= ref.cost <= lr.cost + 1e-6 <= fast.cost + 1e-6 or fast.cost >= ref.cost - 1e-6
        assert lr.cost >= ref.cost - 1e-6
        if lr.extra["bound_active"]:
            n_active += 1
            assert lr.extra["LB"] <= ref.cost + 1e-6
        else:
            assert lr.cost == pytest.approx(cheap.cost, abs=1e-6)
    assert n_active >= 1  # 제약이 걸리는 사례가 실제로 있어야 의미 있는 시험이다


def test_larac_mode_b_reevaluates(G):
    r = larac(G, "개화", "중앙보훈병원", "weekday", g.parse_hhmm("18:00"), g.Config(mode="B"))
    assert r.status == OK and "cost_B" in r.extra and r.extra["requested_mode"] == "B"


def test_larac_fields(G):
    r = larac(G, "개화", "중앙보훈병원", "weekday", g.parse_hhmm("18:00"), g.Config())
    for k in ("lambda", "LB", "bound_active", "T", "shortest_real"):
        assert k in r.extra
    assert r.feasible is True and r.iterations >= 0


def test_genetic_stub_raises(G):
    from algorithms import genetic

    with pytest.raises(NotImplementedError):
        genetic.run(G, "선릉", "수서", "weekday", 30600, g.Config(), 0)
    assert genetic.fitness(100, 50, 40, 2.0) == 120
