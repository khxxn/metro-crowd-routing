"""2단계 검증: CLAUDE.md의 기준값과 0.1분 이내로 일치해야 다음 단계로 간다."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import graph as g  # noqa: E402
from algorithms.common import NO_SERVICE, OK  # noqa: E402
from algorithms.dijkstra import dijkstra  # noqa: E402

TOL = 0.1  # 분


@pytest.fixture(scope="module")
def G():
    return g.load_graph(ROOT / "data")


def run(G, src, dst, day, hhmm, mode="A", weight="cost"):
    return dijkstra(G, src, dst, day, g.parse_hhmm(hhmm), g.Config(mode=mode), weight)


# (출발, 도착, 요일, 시각, A 점수, 실제, B 점수)  — 분
QUERIES = [
    ("광명", "가산디지털단지", "weekday", "08:30", 11.9, 10.0, 11.9),
    ("광명", "천안", "weekday", "08:30", 95.0, 95.0, 95.0),
    ("선릉", "수서", "weekday", "08:30", 33.2, 27.6, 33.2),
    ("김포공항", "고속터미널", "weekday", "08:30", 62.4, 49.9, 62.9),
    ("당산", "석촌", "weekday", "08:30", 53.6, 45.5, 53.2),
    ("여의도", "종합운동장", "weekday", "08:30", 40.4, 34.3, 40.4),
    ("개화", "중앙보훈병원", "weekday", "18:00", 100.4, 89.7, 97.3),
    ("가양", "신논현", "sat", "12:30", 44.8, 43.1, 45.0),
    ("노량진", "동작", "weekday", "08:30", 8.2, 6.9, 8.2),
]


@pytest.mark.parametrize("src,dst,day,hhmm,a_score,real,b_score", QUERIES)
def test_mode_a_score_and_real(G, src, dst, day, hhmm, a_score, real, b_score):
    r = run(G, src, dst, day, hhmm, "A")
    assert r.status == OK
    assert r.cost / 60 == pytest.approx(a_score, abs=TOL)
    assert r.real / 60 == pytest.approx(real, abs=TOL)


@pytest.mark.parametrize("src,dst,day,hhmm,a_score,real,b_score", QUERIES)
def test_mode_b_score(G, src, dst, day, hhmm, a_score, real, b_score):
    r = run(G, src, dst, day, hhmm, "B")
    assert r.status == OK
    assert r.cost / 60 == pytest.approx(b_score, abs=TOL)


def test_last_train_weekday(G):
    r = run(G, "선릉", "수서", "weekday", "24:30")
    assert r.status == OK
    assert r.cost / 60 == pytest.approx(40.6, abs=TOL)
    assert r.real / 60 == pytest.approx(31.8, abs=TOL)


def test_last_train_sat_no_service(G):
    r = run(G, "선릉", "수서", "sat", "24:30")  # 출발역 막차 23:58
    assert r.status == NO_SERVICE


def test_gimpo_gaehwa_last_train_boundary(G):
    assert run(G, "김포공항", "개화", "sat", "24:07").status == OK  # 막차 정각 이하는 탈 수 있다
    assert run(G, "김포공항", "개화", "sat", "24:08").status == NO_SERVICE


def test_real_weight_is_fastest(G):
    # 시간 최단 경로의 real은 점수 최소 경로의 real 이하
    for src, dst, day, hhmm, *_ in QUERIES:
        fast = run(G, src, dst, day, hhmm, "A", "real")
        cheap = run(G, src, dst, day, hhmm, "A", "cost")
        assert fast.real <= cheap.real + 1e-9
        assert cheap.cost <= fast.cost + 1e-9


def test_callable_weight_matches_builtin(G):
    a = run(G, "선릉", "수서", "weekday", "08:30", "A", "cost")
    b = run(G, "선릉", "수서", "weekday", "08:30", "A", lambda real, cost: cost)
    assert a.cost == pytest.approx(b.cost) and a.path == b.path


def test_result_fields(G):
    r = run(G, "노량진", "동작", "weekday", "08:30")
    assert len(r.path) == len(r.edges) + 1
    assert r.expanded > 0 and r.iterations == 1 and r.runtime_ms >= 0
    assert r.cost >= r.real > 0
