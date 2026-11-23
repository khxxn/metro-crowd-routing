import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import graph as g  # noqa: E402


@pytest.fixture(scope="module")
def G():
    return g.load_graph(ROOT / "data")


def test_counts(G):
    assert G.counts == g.EXPECTED


def test_slot_of():
    assert g.slot_of(8 * 3600 + 30 * 60) == 6  # 08:30
    assert g.slot_of(5 * 3600 + 30 * 60) == 0  # 05:30
    assert g.slot_of(0) == 37  # 00:00 → 전날 심야 slot (24:00)
    assert g.slot_of(24 * 3600 + 30 * 60) == 38  # 24:30
    assert g.slot_of(30 * 3600) == 38  # 상한 고정


def test_clock_of():
    assert g.clock_of(3600) == 3600 + 86400
    assert g.clock_of(4 * 3600) == 4 * 3600
    assert g.clock_of(70000) == 70000


def test_ride_mult():
    assert g.ride_mult(0) == 1.0
    assert g.ride_mult(33.75) == 1.0
    assert g.ride_mult(144.5) == pytest.approx(1 + 0.00631 * (144.5 - 33.75), abs=1e-9)


def test_ride_edge_hand_calc(G):
    # 사당(0226)→방배(0225) 평일 08:30: seconds 210, c 144.5 → 356.8초
    e = next(e for e in G.adj["0226"] if e.v == "0225" and e.kind == "ride")
    assert e.seconds == 210
    assert G.cong[("0226", "0225")]["weekday"][6] == 144.5
    real, cost = G.edge_values(e, "weekday", 6)
    assert real == 210
    assert cost == pytest.approx(356.8, abs=0.1)


def test_transfer_edge_hand_calc(G):
    # 서울 1→4호선(0150→0426) 평일 05:30(slot 0): cost 1780.8, real 808.0
    e = next(e for e in G.adj["0150"] if e.v == "0426" and e.kind == "transfer")
    real, cost = G.edge_values(e, "weekday", 0)
    assert real == pytest.approx(808.0)
    assert cost == pytest.approx(1780.8)


def test_transfer_mult_override(G):
    e = next(e for e in G.adj["0150"] if e.v == "0426" and e.kind == "transfer")
    _, cost = G.edge_values(e, "weekday", 0, g.Config(transfer_mult=3.0))
    assert cost == pytest.approx(3.0 * 808.0)


def test_no_service_transfer(G):
    e = next(e for e in G.adj["0150"] if e.v == "0426" and e.kind == "transfer")
    assert G.edge_values(e, "sat", 37) is None


def test_cost_ge_real_everywhere(G):
    for e in G.ride_edges + G.transfer_edges:
        for day in g.DAYS:
            for slot in range(g.N_SLOTS):
                v = G.edge_values(e, day, slot)
                if v is not None:
                    assert v[1] >= v[0] >= 0


def test_missing_congestion_is_zero(G):
    # 혼잡도 행이 없는 (간선, 요일, slot)은 c=0 → cost == real
    total = sum(len(G.ride_edges) * 1 for _ in g.DAYS) * g.N_SLOTS
    assert total - g.EXPECTED["congestion_rows"] == 296


def test_nodes_by_name(G):
    assert len(G.nodes_by_name("서울")) >= 2  # 환승역은 노드 여러 개
    with pytest.raises(KeyError):
        G.nodes_by_name("없는역")


def test_ids_are_strings(G):
    assert "0150" in G.nodes
    assert all(isinstance(k, str) for k in G.nodes)


def test_validation_fails_on_wrong_counts(tmp_path):
    import shutil

    for f in (ROOT / "data").glob("*.csv"):
        shutil.copy(f, tmp_path / f.name)
    p = tmp_path / "station_edges.csv"
    lines = p.read_text(encoding="utf-8-sig").splitlines()
    p.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8-sig")
    with pytest.raises(g.GraphValidationError):
        g.load_graph(tmp_path)
