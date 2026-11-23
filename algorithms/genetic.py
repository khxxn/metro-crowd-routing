"""유전 알고리즘 자리 (6단계).

팀원 코드가 들어올 자리다. 시그니처는 다른 알고리즘과 같다.
팀원 구현이 이 시그니처가 아니면 감싸는 어댑터를 쓴다(README의 '유전 알고리즘 연결' 참고).

공정한 비교를 위해 적합도는 같은 문제를 풀어야 한다:
    fitness = cost + penalty * max(0, real - T),   T = 최단 실제시간 + slack_sec
아래 fitness()를 그대로 쓰면 된다.
"""
from __future__ import annotations

from algorithms.common import Result
from graph import Config, Graph


def fitness(cost: float, real: float, T: float, penalty: float) -> float:
    """작을수록 좋다. T를 넘긴 실제시간(초)마다 penalty(점수 초)를 더한다."""
    return cost + penalty * max(0.0, real - T)


def run(G: Graph, src: str, dst: str, day: str, depart: float, cfg: Config, seed: int) -> Result:
    """팀원 구현이 들어올 자리. 연결 전에는 NotImplementedError를 낸다."""
    raise NotImplementedError("유전 알고리즘은 팀원이 구현해서 이 함수에 연결한다")
