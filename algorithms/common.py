"""모든 알고리즘이 공유하는 결과 형식과 시간 유틸."""
from __future__ import annotations

from dataclasses import dataclass, field

from graph import Edge

# status 값
OK = "ok"
NO_ROUTE = "no_route"  # 그래프상 경로 자체가 없음
NO_SERVICE = "no_service"  # 첫차·막차/환승 불가로 막힘 → "차가 없어요"
INFEASIBLE = "infeasible"  # 허용시간 T 안에 도착하는 경로가 없음 (LARAC/Reference)
NOT_IMPLEMENTED = "not_implemented"  # 팀원 구현이 아직 연결되지 않음 (genetic.py)


@dataclass
class Result:
    path: list[str]  # 노드 id 순서 (출발 노드 포함)
    edges: list[Edge]
    cost: float  # 점수(초)
    real: float  # 실제 소요시간(초)
    feasible: bool | None  # real <= T. T가 정의되지 않은 알고리즘은 None
    expanded: int
    iterations: int
    runtime_ms: float
    status: str
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == OK

    def stations(self, G) -> list[str]:
        return [G.nodes[n][0] for n in self.path]
