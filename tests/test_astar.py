"""3단계 검증: 방식 A에서 A* 점수 == Dijkstra 점수 (200쌍 이상, 허용오차 1e-6)."""
import random
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import graph as g  # noqa: E402
from algorithms.astar import astar, build_heuristic, edge_lower_bound  # noqa: E402
from algorithms.common import OK  # noqa: E402
from algorithms.dijkstra import dijkstra  # noqa: E402

N_PAIRS = 220
TIMES = ["08:30", "12:30", "18:00"]
DAYS = ["weekday", "sat", "sun"]


@pytest.fixture(scope="module")
def G():
    return g.load_graph(ROOT / "data")


def sample_cases(G, n, seed=42):
    rng = random.Random(seed)
    names = sorted(G.by_name)
    cases = []
    while len(cases) < n:
        s, d = rng.sample(names, 2)
        cases.append((s, d, rng.choice(DAYS), rng.choice(TIMES)))
    return cases


def test_astar_equals_dijkstra_mode_a(G, capsys):
    cfg = g.Config(mode="A")
    rows = []
    for s, d, day, t in sample_cases(G, N_PAIRS):
        dep = g.parse_hhmm(t)
        a = astar(G, s, d, day, dep, cfg)
        b = dijkstra(G, s, d, day, dep, cfg, "cost")
        assert a.status == b.status, (s, d, day, t)
        if b.status == OK:
            assert a.cost == pytest.approx(b.cost, abs=1e-6), (s, d, day, t, a.cost, b.cost)
            rows.append((a.expanded, b.expanded))
    assert len(rows) >= 200
    ea, eb = zip(*rows)
    with capsys.disabled():
        print(f"\n[A* vs Dijkstra] {len(rows)}쌍 모두 점수 일치 (오차 <= 1e-6)")
        print(f"  확장 노드 평균  A*={statistics.mean(ea):.1f}  Dijkstra={statistics.mean(eb):.1f}"
              f"  (A*/Dijkstra={sum(ea) / sum(eb):.2%})")
        print(f"  확장 노드 중앙값 A*={statistics.median(ea):.0f}  Dijkstra={statistics.median(eb):.0f}")
        print(f"  A*가 더 적게 확장한 쌍: {sum(x < y for x, y in rows)}  같음: {sum(x == y for x, y in rows)}"
              f"  더 많음: {sum(x > y for x, y in rows)}")


def test_heuristic_admissible_and_consistent(G):
    cfg = g.Config()
    for dst, day in [("수서", "weekday"), ("천안", "sat"), ("동작", "sun")]:
        h = build_heuristic(G, dst, day, cfg)
        for edges in G.adj.values():
            for e in edges:
                if e.u in h and e.v in h:  # 하한 간선 기준 consistency
                    assert h[e.u] <= edge_lower_bound(G, e, day, cfg) + h[e.v] + 1e-9
                # 하한은 모든 시간대 실제 값 이하
                for slot in range(g.N_SLOTS):
                    v = G.edge_values(e, day, slot, cfg)
                    if v is not None:
                        assert edge_lower_bound(G, e, day, cfg) <= min(v) + 1e-9


def test_h_never_exceeds_true_cost_and_real(G):
    cfg = g.Config(mode="A")
    for s, d, day, t in sample_cases(G, 30, seed=7):
        r = astar(G, s, d, day, g.parse_hhmm(t), cfg)
        if r.status == OK:
            assert r.extra["h_start"] <= r.cost + 1e-6
            assert r.extra["h_start"] <= r.real + 1e-6  # 하한은 실제 시간 이하


def test_late_night_status_matches_dijkstra(G):
    cfg = g.Config(mode="A")
    for s, d, day, t in sample_cases(G, 40, seed=3):
        dep = g.parse_hhmm("23:00")
        a = astar(G, s, d, day, dep, cfg)
        b = dijkstra(G, s, d, day, dep, cfg, "cost")
        assert a.status == b.status
        if b.status == OK:
            assert a.cost == pytest.approx(b.cost, abs=1e-6)


def test_reference_queries_cost_match(G):
    cfg = g.Config(mode="A")
    for s, d, day, t, score in [("선릉", "수서", "weekday", "08:30", 33.2),
                                ("광명", "천안", "weekday", "08:30", 95.0),
                                ("노량진", "동작", "weekday", "08:30", 8.2)]:
        r = astar(G, s, d, day, g.parse_hhmm(t), cfg)
        assert r.cost / 60 == pytest.approx(score, abs=0.1)


def test_mode_b_runs_and_is_close(G):
    # 방식 B는 상태당 라벨 1개 근사. 검증만: 실행되고 cost >= real
    r = astar(G, "개화", "중앙보훈병원", "weekday", g.parse_hhmm("18:00"), g.Config(mode="B"))
    assert r.status == OK and r.cost >= r.real > 0
