"""데이터 로더와 공통 시간·비용 함수 (1단계).

비용 식과 데이터 규칙은 팀 공통(CLAUDE.md)이다. 임의로 바꾸지 않는다.
  탑승  real = seconds,            cost = seconds * ride_mult(c)
  환승  real = walk + wait,        cost = cost_sec (transfer_mult 지정 시 mult * (walk+wait))
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

DAYS = ("weekday", "sat", "sun")
N_SLOTS = 39
SLOT_BASE = 19800  # 05:30
SLOT_SEC = 1800

# 로딩 직후 검증할 기대값 (CLAUDE.md / 프롬프트 1단계)
EXPECTED = {
    "nodes": 458,
    "station_names": 404,
    "ride_edges": 896,
    "congestion_rows": 104_536,
    "transfer_rows": 13_338,
    "transfer_edges": 114,
    "no_service_rows": 387,
}


# 수도권 확장 데이터(data/ext, tools/build_extension.py가 만든다)의 기대값
EXPECTED_EXT = {
    "ext_nodes": 343,
    "ext_station_names": 254,  # 새 이름만. 기존 이름과 겹치는 노드 70개는 이름이 합쳐진다
    "ext_ride_edges": 652,
    "ext_transfer_edges": 232,
}


class GraphValidationError(RuntimeError):
    """로딩한 데이터가 기대 행 수와 다를 때."""


@dataclass
class Config:
    alpha: float = 0.00631
    c0: float = 33.75
    transfer_mult: float | None = None  # None이면 cost_sec 그대로
    slack_sec: int = 300
    mode: str = "A"  # "A": 출발 slot 고정, "B": 간선 진입 시각으로 slot 재계산

    def __post_init__(self):
        if self.mode not in ("A", "B"):
            raise ValueError(f"mode must be 'A' or 'B', got {self.mode!r}")


@dataclass(frozen=True)
class Edge:
    u: str
    v: str
    kind: str  # "ride" | "transfer"
    seconds: int = 0  # ride 전용
    first: tuple = ()  # ride 전용: (weekday, sat, sun) 첫차 초
    last: tuple = ()  # ride 전용: (weekday, sat, sun) 막차 초

    @property
    def key(self) -> tuple[str, str]:
        return (self.u, self.v)

    def first_dep(self, day: str) -> int:
        return self.first[DAYS.index(day)]

    def last_dep(self, day: str) -> int:
        return self.last[DAYS.index(day)]


# ---------------------------------------------------------------- 시간·비용 함수
def slot_of(t: float) -> int:
    """자정부터 초 t → 30분 slot(0~38). 05:30 이전은 다음날로 본다."""
    tp = t + 86400 if t < SLOT_BASE else t
    return int(min((tp - SLOT_BASE) // SLOT_SEC, N_SLOTS - 1))


def clock_of(t: float) -> float:
    """막차 비교용 시계. 00:00~03:59는 24시 이후로 본다."""
    return t + 86400 if t < 4 * 3600 else t


def ride_mult(c: float, alpha: float = 0.00631, c0: float = 33.75) -> float:
    """혼잡도 c(%)에 따른 탑승 비용 배율. 항상 1 이상."""
    return 1.0 + alpha * max(c - c0, 0.0)


def parse_hhmm(s: str) -> int:
    """'08:30' / '24:30' → 초. 24:00 이후도 허용(막차 질의용)."""
    h, m = s.split(":")
    return int(h) * 3600 + int(m) * 60


# ---------------------------------------------------------------- 그래프
@dataclass
class Graph:
    nodes: dict[str, tuple[str, str]]  # id -> (station_name, line_key)
    by_name: dict[str, list[str]]
    ride_edges: list[Edge]
    transfer_edges: list[Edge]
    adj: dict[str, list[Edge]]  # node id -> 나가는 간선 (탑승 + 환승)
    # cong[(u,v)][day][slot] = 혼잡도(%), 행이 없으면 0.0
    cong: dict[tuple, dict[str, list[float]]]
    # trv[(u,v)][day][slot] = (walk, wait, cost, ok). NO_SERVICE는 (walk, None, None, False)
    trv: dict[tuple, dict[str, list[tuple]]]
    counts: dict[str, int] = field(default_factory=dict)
    ext_nodes: frozenset = frozenset()  # 수도권 확장 노드(혼잡도 0·첫차 막차 가정 등 추정값을 쓴다)

    def nodes_by_name(self, name: str) -> list[str]:
        if name not in self.by_name:
            raise KeyError(f"역 이름을 찾을 수 없음: {name!r}")
        return self.by_name[name]

    def edge_values(self, edge: Edge, day: str, slot: int, cfg: Config | None = None):
        """(real, cost) 초. 환승이 NO_SERVICE이면 None."""
        cfg = cfg or Config()
        if edge.kind == "ride":
            c = self.cong.get(edge.key, {}).get(day, (0.0,) * N_SLOTS)[slot]
            return float(edge.seconds), edge.seconds * ride_mult(c, cfg.alpha, cfg.c0)
        walk, wait, cost, ok = self.trv[edge.key][day][slot]
        if not ok:
            return None
        real = walk + wait
        if cfg.transfer_mult is not None:
            return float(real), cfg.transfer_mult * real
        return float(real), cost


def _read(path: Path):
    # BOM 때문에 utf-8-sig, id는 문자열 그대로 둔다
    with path.open(encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def _check(name: str, got: int):
    if got != EXPECTED[name]:
        raise GraphValidationError(f"{name}: {got:,} (기대 {EXPECTED[name]:,})")


def _check_ext(name: str, got: int):
    if got != EXPECTED_EXT[name]:
        raise GraphValidationError(f"{name}: {got:,} (기대 {EXPECTED_EXT[name]:,})")


def load_graph(data_dir: str | Path = "data", extended: bool = False) -> Graph:
    """팀 CSV 4개(서울 1~9호선)를 읽는다. extended=True면 data/ext의 수도권 노선을 더한다.

    확장 노선은 추정값이다: 혼잡도 0, 첫차·막차 05:30~24:00 가정, 환승은 정적 값(2.204배).
    """
    d = Path(data_dir)

    nodes: dict[str, tuple[str, str]] = {}
    by_name: dict[str, list[str]] = {}
    for r in _read(d / "station_nodes.csv"):
        nodes[r["id"]] = (r["station_name"], r["line_key"])
        by_name.setdefault(r["station_name"], []).append(r["id"])
    _check("nodes", len(nodes))
    _check("station_names", len(by_name))

    ride_edges: list[Edge] = []
    for r in _read(d / "station_edges.csv"):
        ride_edges.append(
            Edge(
                r["from_node_id"], r["to_node_id"], "ride",
                seconds=int(r["seconds"]),
                first=tuple(int(r[f"first_dep_{k}_sec"]) for k in DAYS),
                last=tuple(int(r[f"last_dep_{k}_sec"]) for k in DAYS),
            )
        )
    _check("ride_edges", len(ride_edges))

    cong: dict[tuple, dict[str, list[float]]] = {}
    n_cong = 0
    for r in _read(d / "ride_congestion_filled.csv"):
        n_cong += 1
        key = (r["from_node_id"], r["to_node_id"])
        arr = cong.setdefault(key, {k: [0.0] * N_SLOTS for k in DAYS})[r["day_type"]]
        v = r["congestion_pct"]
        arr[int(r["slot_idx"])] = float(v) if v != "" else 0.0  # 빈 값은 0
    _check("congestion_rows", n_cong)

    trv: dict[tuple, dict[str, list[tuple]]] = {}
    n_trv = n_ns = 0
    for r in _read(d / "transfer_edges_cost.csv"):
        n_trv += 1
        key = (r["from_node_id"], r["to_node_id"])
        arr = trv.setdefault(key, {k: [None] * N_SLOTS for k in DAYS})[r["day_type"]]
        ok = r["status"] == "OK"
        if not ok:
            n_ns += 1
        walk = int(r["walk_sec"])
        arr[int(r["slot_idx"])] = (
            (walk, float(r["wait_sec"]), float(r["cost_sec"]), True) if ok else (walk, None, None, False)
        )
    _check("transfer_rows", n_trv)
    _check("transfer_edges", len(trv))
    _check("no_service_rows", n_ns)
    counts = {"nodes": len(nodes), "station_names": len(by_name), "ride_edges": len(ride_edges),
              "congestion_rows": n_cong, "transfer_rows": n_trv,
              "transfer_edges": len(trv), "no_service_rows": n_ns}

    ext_nodes: set[str] = set()
    if extended:
        e = d / "ext"
        names_before = len(by_name)
        for r in _read(e / "station_nodes_ext.csv"):
            if r["id"] in nodes:
                raise GraphValidationError(f"확장 노드 id가 겹침: {r['id']}")
            nodes[r["id"]] = (r["station_name"], r["line_key"])
            by_name.setdefault(r["station_name"], []).append(r["id"])
            ext_nodes.add(r["id"])
        _check_ext("ext_nodes", len(ext_nodes))
        _check_ext("ext_station_names", len(by_name) - names_before)
        n_ext_ride = 0
        for r in _read(e / "station_edges_ext.csv"):
            if r["from_node_id"] not in nodes or r["to_node_id"] not in nodes:
                raise GraphValidationError(f"확장 간선의 노드가 없음: {r['from_node_id']}->{r['to_node_id']}")
            ride_edges.append(Edge(
                r["from_node_id"], r["to_node_id"], "ride", seconds=int(r["seconds"]),
                first=tuple(int(r[f"first_dep_{k}_sec"]) for k in DAYS),
                last=tuple(int(r[f"last_dep_{k}_sec"]) for k in DAYS)))
            n_ext_ride += 1
        _check_ext("ext_ride_edges", n_ext_ride)
        n_ext_tr = 0
        for r in _read(e / "transfer_edges_ext.csv"):
            key = (r["from_node_id"], r["to_node_id"])
            if key in trv or key[0] not in nodes or key[1] not in nodes:
                raise GraphValidationError(f"확장 환승 간선이 잘못됨: {key}")
            real, cost = float(r["real_sec"]), float(r["cost_sec"])
            # real = walk + wait. 걷기·대기를 나누지 않으므로 walk=real, wait=0. 시간대와 무관한 정적 값.
            trv[key] = {k: [(real, 0.0, cost, True)] * N_SLOTS for k in DAYS}
            n_ext_tr += 1
        _check_ext("ext_transfer_edges", n_ext_tr)
        counts.update({"ext_nodes": len(ext_nodes), "ext_station_names": EXPECTED_EXT["ext_station_names"],
                       "ext_ride_edges": n_ext_ride, "ext_transfer_edges": n_ext_tr,
                       "total_nodes": len(nodes), "total_station_names": len(by_name)})

    transfer_edges = [Edge(u, v, "transfer") for (u, v) in trv]
    adj: dict[str, list[Edge]] = {n: [] for n in nodes}
    for e_ in ride_edges + transfer_edges:
        adj[e_.u].append(e_)

    return Graph(nodes, by_name, ride_edges, transfer_edges, adj, cong, trv, counts=counts,
                 ext_nodes=frozenset(ext_nodes))
