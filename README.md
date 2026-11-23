# 혼잡도 길찾기 — "5분 더 걸려도 덜 붐비는 길은?"

서울 지하철 1~9호선에서 실제 소요시간과 시간대별 혼잡도를 함께 고려하는 경로 탐색 알고리즘을 구현하고 실제 데이터로 비교한다.
비용 식과 데이터 규칙은 팀 공통이며 `CLAUDE.md`에 있다(팀 Notion 설명서의 요약).

## 실행 방법

```bash
bash 알고리즘/subway-route/subway-route/setup.sh .   # 최초 1회: 폴더, subway-etl 클론, 가상환경, 데이터 점검
source .venv/bin/activate

python app.py --open                                  # UI 프로그램 (http://127.0.0.1:8765)
python route.py 선릉 수서 weekday 08:30 --algo astar --mode A
python -m experiments.run --pairs 300 --seed 42       # 실험 전체 (results/ 생성, 약 30초)
pytest                                                # 테스트 전체
```

## 공유하기 (zip)
`python tools/make_zip.py`가 `dist/subway-congestion-route.zip`을 만든다. 받는 사람은 압축을 풀고 `run.bat`(Windows) 또는 `bash run.sh`(맥·리눅스)를 실행하면 된다. 파이썬 3.10 이상만 있으면 되고, UI와 CLI는 표준 라이브러리만 써서 설치할 것이 없다. 테스트와 실험 그림을 다시 만들려면 `pip install -r requirements.txt`가 필요하다.

- **들어가는 것:** 프로그램, `data/`(팀 CSV와 수도권 확장 데이터), `results/`(실험 결과), `tests/`, 문서, `START_HERE.txt`(처음 쓰는 사람용 안내).
- **빠지는 것:** `.venv`, `subway-etl/`(팀원 저장소 복사본), `알고리즘/`(수업 자료), 캐시. 확장 데이터는 이미 만들어져 있어서 `subway-etl` 없이도 실행된다.
- **주의:** `data/`에 팀 공유 CSV가 들어 있다. CLAUDE.md는 이 데이터를 저장소에 커밋하지 말라고 하므로, 팀 밖으로 보내기 전에 확인한다.
- 포트 8765가 이미 쓰이고 있으면 다음 포트(8766…)로 자동 이동하고 터미널에 주소를 알려준다.

| 파일 | 역할 |
|---|---|
| `graph.py` | 데이터 로더, `Config`, `slot_of`, `clock_of`, `ride_mult`, `edge_values`, `nodes_by_name` |
| `algorithms/dijkstra.py` | 공통 탐색 뼈대 + Dijkstra (`weight`와 `h`를 인자로 받는다) |
| `algorithms/astar.py` | 혼잡 가중 A\* |
| `algorithms/larac.py` | 라그랑주 완화 LARAC |
| `algorithms/reference.py` | 정답 기준(이중 기준 라벨 설정법, 비교 대상 아님) |
| `algorithms/genetic.py` | 유전 알고리즘이 들어올 자리(스텁) |
| `experiments/evaluate.py` | 어떤 경로든 방식 A·B 점수와 실제시간을 다시 계산 |
| `experiments/run.py`, `plots.py` | 실험과 그림 |
| `route.py` | CLI |
| `app.py`, `ui/index.html` | UI 프로그램 (표준 라이브러리 서버 + 브라우저 화면) |
| `tools/build_extension.py` | 수도권 확장 데이터(`data/ext/`) 생성 |
| `routelib.py` | CLI·UI·실험이 같이 쓰는 함수 |

## 가정
- `day`는 서비스 요일이다. 00:00~03:59 출발은 호출하는 쪽에서 전날 요일과 24시 이후 시각(예: 24:30)으로 넘긴다.
- 탐색 상태는 `(노드, by_ride)`다. 막차 확인이 `by_ride`에 따라 달라서, 노드당 라벨 1개로 합치면 방식 A에서도 정확하지 않을 수 있다.
- 새로 타는 탑승 간선에서 `first ≤ clock_of(now) ≤ last`를 벗어나면(첫차 전, 막차 후) 막힌 것으로 본다.
- 혼잡도 행이 없는 (간선, 요일, slot)은 c = 0이다. 896 × 3 × 39 = 104,832행 중 296행이 비어 있다.
- 허용시간은 `T = 최단 실제시간 + slack_sec`(기본 300초)이고, 최단 실제시간은 `weight="real"` Dijkstra로 구한다.
- `subway-etl/`은 읽기 전용 참고용이다. 그쪽 그래프는 환승에 심리 페널티 180초와 배차간격÷2 대기를 쓰므로 우리 식과 다르다.
- Notion 원문은 이 저장소에 없다. 2.204 배율과 검증용 행 수는 `CLAUDE.md` 요약과 CSV의 `cost_sec` 열을 따랐다.
- 실행 시간은 질의마다 5회 중앙값이다. `runtime_ms`는 A\*·Reference의 휴리스틱 캐시를 매번 비운 값(처음 질의)이고, `runtime_warm_ms`는 캐시를 재사용한 값이다.
- 막차 질의(`24:30` 등)는 방식 A로 확인했다.

## 수도권 확장 (추정값)
팀 CSV는 서울 1~9호선(458노드, 역 이름 404개)뿐이다. 수도권 전체(801노드, 역 이름 658개)를 쓰려면 `data/ext/`의 확장 데이터를 더한다.
`python tools/build_extension.py`가 `subway-etl/output`에서 팀 CSV에 없는 16개 노선(수인분당선, 경의·중앙선, 신분당선, 인천 1·2호선, 공항철도, 경춘선, 서해선, 에버라인, 의정부경전철, 우이신설선, 경강선, 신림선, 김포골드라인, GTX-A, 자기부상철도)의 343노드, 탑승 간선 652개, 환승 간선 232개를 만든다. 팀 CSV 4개는 바뀌지 않는다.

| 항목 | 새 노선에 쓴 값 | 근거 |
|---|---|---|
| 탑승 시간 | ETL의 `seconds` | 실측 차용·속도표 추정·GTX 값(ETL 출처 열 참고) |
| 혼잡도 | 0 (점수 = 실제 시간) | 팀 혼잡도 데이터가 없다 |
| 첫차·막차 | 05:30~24:00 운행 | ETL에 시각이 없다 |
| 환승 `real` | ETL 합계 − 180초 (같은 승강장 환승은 그대로) | 팀 규칙은 페널티가 없다. ETL은 걷기 + 배차간격÷2 + 180초를 더한 합계만 갖고 있다 |
| 환승 `cost` | 2.204 × `real` | 팀 확정 배율 |

- **편향 주의:** 새 노선은 혼잡도 0이라 점수가 실제 시간과 같다. 그래서 점수를 줄이는 알고리즘(Dijkstra 점수 최소, A\*, LARAC)은 같은 구간에서 새 노선을 유리하게 고른다. 새 노선이 실제로 덜 붐빈다는 뜻이 아니다. 새 노선이 들어간 경로에는 UI와 CLI가 추정 표시를 붙인다.
- 팀 기준값(CLAUDE.md의 9개 질의)과 실험 결과(`results/`)는 확장 없는 팀 데이터로 만든 것이다. 예를 들어 선릉→수서는 수인분당선이 생기면 점수 33.2분이 7.4분으로 바뀐다. 확장 노선의 시간과 환승은 추정이라 팀 검증에 쓰지 않는다.
- 사용: `load_graph(data, extended=True)`. `route.py`와 `app.py`는 확장을 기본으로 쓰고, `--base`를 주면 팀 데이터만 쓴다. 실험 러너는 팀 데이터를 쓴다.
- ETL은 1~9호선끼리의 탑승 간선 6개를 더 갖고 있지만 팀 데이터가 우선이라 쓰지 않았다.

## 알고리즘

### Dijkstra (`algorithms/dijkstra.py`)
라벨 `(key, cost, real, node, by_ride, parent)`. `weight`는 `"cost"`, `"real"` 또는 `f(real, cost)` 함수다.
방식 A는 출발 시각의 slot 하나를 전 간선에 고정하고, 방식 B는 간선에 들어서는 시각으로 slot을 다시 찾는다(상태당 라벨 1개 근사).
복잡도 O(E log V)(상태 수 2V). 선택 인자 `h`가 있으면 A\*로 동작한다.

### 혼잡 가중 A\* (`algorithms/astar.py`)
목적함수는 점수 `cost`의 합이고 우선순위는 `f = g + h(n)`이다.

**휴리스틱.** `h(n)`은 n에서 도착역의 어느 노드까지의 간선별 하한 가중치 최단 거리이며, 도착역 노드들에서 시작하는 역방향 Dijkstra로 미리 계산한다.

| 간선 | 하한 가중치 |
|---|---|
| 탑승 | `seconds` |
| 환승 | 그 요일 전 시간대(NO_SERVICE 제외)에서 `min(walk+wait, cost)`의 최솟값 |

**왜 admissible한가.** 탑승은 `cost = seconds × m(c)`이고 `m(c) ≥ 1`(ALPHA ≥ 0)이라 `cost ≥ seconds`다. 환승은 `cost ≥ real = walk+wait ≥ 전 시간대 최솟값`이다. 따라서 모든 간선에서 `cost ≥ real ≥ 하한`이고, 하한의 합은 실제 점수의 합을 넘지 않는다. 환승의 대기는 시간대마다 달라서 한 시각의 값을 쓰면 하한이 아니므로 전 시간대 최솟값을 쓴다. `transfer_mult < 1`처럼 배율이 1 미만인 설정에서도 하한이 되도록 `cost`와의 `min`을 취한다.

**왜 consistent한가.** `h`가 하한 가중치 그래프에서 도착역까지의 최단 거리이므로 모든 간선 `(u, v)`에서 `h(u) ≤ 하한(u, v) + h(v)`다. 첫차·막차로 막히는 간선은 그래프에서 간선을 빼는 것과 같아서 이 성질을 깨지 않는다.

**결론.** 방식 A(정적 가중치)에서는 한 번 닫힌 상태를 다시 열 필요가 없고, Dijkstra와 같은 최적 점수를 낸다. 방식 B는 Dijkstra와 마찬가지로 상태당 라벨 1개를 쓰는 근사이며 최적성을 보장하지 않는다. 허용시간은 탐색에 넣지 않고 결과의 `feasible = real ≤ T`만 기록한다.

**비용.** 휴리스틱은 (도착역, 요일, 환승 배율)마다 한 번 만들어 캐시한다.

### 라그랑주 완화 LARAC (`algorithms/larac.py`)
문제: `real(P) ≤ T`를 만족하면서 `cost(P)` 최소. 방식 A(정적 가중치)에서 동작한다.

```
P_c ← Dijkstra(cost);  real(P_c) ≤ T 이면 P_c 반환          # 제약이 안 걸림
P_r ← Dijkstra(real)
반복:  λ = (cost(P_r) − cost(P_c)) / (real(P_c) − real(P_r))
       P ← Dijkstra(cost + λ·real)
       |L(P) − L(P_c)| < 1e-9 이면 P_r 반환,  real(P) ≤ T 이면 P_r ← P, 아니면 P_c ← P
```

`extra`에 `iterations`, `lambda`, 라그랑주 하한 `LB = L(P) − λ·T`, `bound_active`(제약이 실제로 걸렸는지), `T`, `shortest_real`이 들어 있다.
복잡도: Dijkstra 호출 수 = 2 + 반복 횟수.

**한계.** 근사해다. (real, cost) 평면에서 가중합 `cost + λ·real`을 최소로 하는 경로, 즉 볼록 껍질 위의 경로만 찾는다. 껍질 안쪽에 있는 최적해는 놓칠 수 있다(06-04 볼록 껍질과 연결). 방식 B로 실행하라는 요청이 오면 경로 탐색은 방식 A 가중치로 하고, 결과 경로를 `experiments/evaluate.py`의 `evaluate_B`로 다시 평가한다(`extra`의 `cost_B`, `real_B`).

### 정답 기준 Reference (`algorithms/reference.py`)
이중 기준 라벨 설정법(방식 A, 정확). 노드와 `by_ride`마다 `(real, cost)` 파레토 라벨 집합을 두고, `real > T`인 라벨과 지배당한 라벨을 버린다. 출력은 `real ≤ T` 중 최소 `cost`, 즉 제약 하 최적해다. 최악의 경우 라벨 수가 지수적일 수 있지만 여기서는 `T`가 최단시간 + 5분이라 라벨이 적다. 3단계 하한 `h`로 가지치기하고 `cost + h` 순서로 꺼내서, 도착역에서 처음 꺼낸 라벨이 최적이다. LARAC·GA의 오차율 계산에만 쓰며 비교 대상이 아니다. 장난감 그래프 150개에서 전수 탐색과 일치함을 확인했다.

### 유전 알고리즘 연결 (`algorithms/genetic.py`)
팀원 코드가 들어올 자리이고 지금은 `NotImplementedError` 스텁이다. 시그니처는 다음과 같다.

```python
def run(G, src, dst, day, depart, cfg, seed) -> Result
```

팀원 구현이 이 시그니처가 아니면 감싸는 어댑터를 쓴다. 예를 들어 경로를 노드 id 리스트로만 돌려주는 구현이라면 `experiments.evaluate.edges_from_path(G, path)`로 간선을 복원하고 `evaluate(...)`로 점수를 다시 계산해서 `Result`를 만든다. 실험 러너는 `NotImplementedError`를 만나면 GA를 건너뛰고, 구현되면 같은 OD에 자동으로 포함한다(GA는 시드 30개 평균·표준편차를 쓰도록 확장해야 한다).
**공정한 비교를 위해 적합도는 같은 문제를 풀어야 한다**: `cost + 페널티 × max(0, real − T)`, `T = 최단 실제시간 + slack_sec`. `genetic.fitness()`를 그대로 쓰면 된다.

## 실험 (`experiments/run.py`)
- OD: 역명 404개에서 무작위 쌍(시드 고정), 실제시간 10분 미만 쌍은 제외한다. 무작위 300쌍 × 요일 2 × 출발 시각 4 = 2,400건.
- 실험 A(노선 특성): 2호선 위주와 5호선 위주 OD 각 25쌍. 선정 기준: 출발·도착역 모두 그 호선 노드가 있고, 점수 최소 경로의 탑승 간선 중 80% 이상이 그 호선이며, 실제시간 10분 이상. 평일 08:30으로 고정했다.
- 실험 B(알고리즘 비교): 같은 OD에 Dijkstra(시간 최단), Dijkstra(점수 최소), A\*, LARAC, GA(구현되어 있으면), Reference.
- 측정: `cost_A`, `cost_B`, `real`, `feasible`, `gap_vs_ref(%)`, `runtime_ms`, `expanded`, `iterations`, `bound_active`.
- 민감도: `alpha ∈ {0.004, 0.00631, 0.009}`, `slack ∈ {180, 300, 600}`에서 최단시간 경로와 다른 경로가 나오는 OD 비율(평일 08:30, 무작위 OD 100쌍).
- 산출물(`results/`): `raw.csv`, `summary.csv`, `summary_by_time.csv`, `sensitivity.csv`, `time_curve.csv`, `path_compare.md`, `fig_*.png`, `meta.txt`.

재현: `python -m experiments.run --days weekday,sat --times 08:30,12:30,18:00,23:00 --pairs 300 --seed 42 --slack 300`

## UI 프로그램 (`app.py`)
`python app.py`를 실행하고 브라우저에서 `http://127.0.0.1:8765`를 연다.
- **경로 비교:** 출발·도착역, 요일, 시각(24:30 가능), 방식 A/B, 여유 시간, ALPHA를 정하면 알고리즘별 경로를 카드로 비교하고 지도에 겹쳐 그린다. 점수와 실제 시간, 확장 노드, 반복 횟수, 제약 활성 여부, 출발 시각별 점수 곡선을 보여준다.
- **실험 결과:** 요약 표, 그림, 민감도, 대표 OD 경로 비교를 보여준다.

## 검증
`pytest`로 실행한다.

| 파일 | 내용 |
|---|---|
| `tests/test_graph.py` | 로더, 행 수 검증, 손계산 2개, NO_SERVICE 환승 |
| `tests/test_dijkstra.py` | CLAUDE.md의 9개 질의(방식 A·B)와 막차 질의 |
| `tests/test_astar.py` | 무작위 OD 220쌍에서 A\* = Dijkstra(1e-6), 휴리스틱 성질 |
| `tests/test_reference_larac.py` | 장난감 그래프에서 Reference = 전수 탐색, LARAC의 실행 가능성과 하한 |
| `tests/test_cli_app.py` | CLI 출력과 UI 서버 API |
| `tests/test_extended.py` | 수도권 확장: 개수, 환승·탑승 값, 연결성, A\*·LARAC·Reference 일관성, 확장이 기존 구간을 악화시키지 않음 |
