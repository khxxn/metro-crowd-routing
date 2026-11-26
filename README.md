# 5분 더 걸려도 덜 붐비는 길은?

서울 지하철에서 이동 시간과 혼잡도를 함께 따져, 조금 더 걸려도 덜 붐비는 경로를 찾는다.
다익스트라·A\*·GA 세 알고리즘을 같은 비용 식으로 구현해 결과와 속도를 비교한다.
C++ 파일 하나(`route_server.cpp`)가 경로 계산과 지도 화면 제공을 모두 맡는다. 외부 라이브러리는 쓰지 않는다.

## 파일 구성

| 파일 | 내용 |
|---|---|
| `route_server.cpp` | 서버 본체. CSV 읽기, 비용 식, 다익스트라·A\*·GA, HTTP API, 화면 파일 내려주기까지 한 파일에 있다 |
| `route_ui.html` | 지도 화면. 출발·도착역, 요일, 시각, 알고리즘을 골라 경로를 그리고 세 알고리즘 비교표를 보여 준다 |
| `data/station_nodes.csv` | 역 노드(노선별 역): id, 역 이름, 노선, 위도·경도 |
| `data/station_edges.csv` | 탑승 구간: 출발·도착 노드, 걸리는 시간(초), 요일별 첫차·막차 시각 |
| `data/ride_congestion_filled.csv` | 탑승 구간별 혼잡도(%). 요일 3종 × 시간대 39개, 빈 값을 채운 버전 |
| `data/transfer_edges_cost.csv` | 환승: 걷는 시간, 시간대별 대기 시간, 비용, 운행 여부 |
| `tests/route_check_test.cpp` | 검증 값, 막차·첫차, 심야 버그(아차산→잠실새내), A\*와 다익스트라의 답이 같은지 확인 |
| `tests/route_ga_test.cpp` | GA 테스트 (Susu) |
| `tools/synthetic.h` | 가상 격자 노선망 생성기. 실제 데이터의 혼잡도·탑승 시간·환승 값을 복사해, 크기를 마음대로 키운 노선망을 만든다 |
| `tools/gen_synthetic.cpp` | 생성기를 실행해 `data/`와 같은 형식의 CSV 4개로 저장하는 프로그램 |
| `Dockerfile`, `.dockerignore`, `docker-compose.yml` | Docker 이미지 빌드·실행 설정 |
| `DEPLOY.md` | Docker로 내 컴퓨터, 클라우드, AWS에 배포하는 방법 |

## 실행

```bash
# 윈도우(Git Bash)
g++ -O2 -std=c++17 -static route_server.cpp -o route_server -lws2_32
./route_server data route_ui.html
# 리눅스·맥은 -static, -lws2_32 없이 빌드
```

브라우저에서 `http://127.0.0.1:8080`을 연다. 포트를 바꾸려면 `./route_server data route_ui.html 9000`.

## 가상 노선망으로 실행 (입력 크기 n 실험)

가로 노선 k개 × 세로 노선 k개 격자 노선망을 만든다. 노선이 만나는 점이 환승역(`T0_0` ~ `T{k-1}_{k-1}`)이고,
환승역 사이에 일반역 3개가 있다. 시드를 고정하면 누구나 같은 노선망을 다시 만들 수 있다.

```bash
g++ -O2 -std=c++17 -static tools/gen_synthetic.cpp -o gen_synthetic -lws2_32
./gen_synthetic data data_syn/k30 30          # 실제 데이터 폴더, 출력 폴더, k [m=3] [seed=1]
./route_server data_syn/k30 route_ui.html
```

서버는 CSV를 한꺼번에 메모리에 올리므로 메모리가 약 0.7MB × k² 든다(k=30 0.6GB, k=50 1.7GB, k=60 2.5GB).
화면으로 띄우는 것은 k=50 이하를 권장한다. `data_syn/`은 git에 올리지 않는다.

## 알고리즘

세 알고리즘 모두 같은 문제(점수 최소 경로)를 같은 비용 식, 팀 표준인 방식 B(간선에 들어서는 시각으로 시간대를 다시 찾음),
같은 막차·첫차 규칙으로 푼다. 다익스트라가 비교의 기준이다.

| 화면 이름 | 함수 | 출처 | 비교할 것 |
|---|---|---|---|
| 다익스트라 | `route()` → `search()` → `search_h()` | main | 기준 |
| A* | `route_astar()` → `search_h()` | Susu (휴리스틱을 랜드마크 하한으로 바꿈) | 다익스트라와 같은 답인지, 탐색 수·계산 시간이 얼마나 줄었는지 |
| GA | `route_ga()` | Susu | 다익스트라 점수와 얼마나 차이 나는지, 계산 시간 |

각 함수는 `by_real = true`면 최단 시간 경로를, `false`면 점수 최소(덜 붐비는) 경로를 돌려준다.

## 개선 내역

- **탐색 상태를 (노드, 열차를 탄 채로 왔나) 둘로 나눔.** 노드마다 기록을 하나만 두면, 환승해서 온 빠른 기록이 막차에 막히고 계속 타고 온 기록은 버려져서 심야에 경로를 못 찾거나 크게 돌아갔다(예: 아차산→잠실새내 일 23:30, 최단 시간이 88.8분 → 34.8분). 무작위 6,000개 질의(최단 시간·점수 각각, 11,960번 탐색)에서 264번 결과가 바뀌었고 모두 더 좋아졌다.
- **다익스트라와 A\*가 같은 탐색 함수 `search_h()`를 쓴다.** 다익스트라는 h = 0, A\*는 아래 하한 h를 넘긴다.
- **A\* 휴리스틱을 바꿈.** 원래(Susu 브랜치)는 질의마다 도착역에서 거꾸로 다익스트라를 그래프 전체에 돌려 h를 구했다. h는 정확했지만 그 준비 비용이 본 탐색보다 커서 전체 시간은 다익스트라의 약 2배였다. 지금은 두 하한 중 큰 값을 쓴다.
  - 랜드마크(ALT): 서로 멀리 떨어진 기준 역 8개를 고르고, 데이터를 읽을 때 한 번만 "모든 역 ↔ 기준 역" 최소 비용(혼잡 가산 없는 하한)을 표로 만든다(`Data::prepare_landmarks()`). 질의 때는 삼각부등식 d(u,t) ≥ d(u,L) − d(t,L), d(L,t) − d(L,u)로 표에서 바로 구한다.
  - 직선거리: 도착역까지 직선거리 ÷ 가장 빠른 이동 속도(`Data::prepare_geo()`).
  - 두 하한 모두 남은 비용을 넘지 않고 간선마다 일관적이라 다익스트라와 같은 답을 낸다(무작위 수천 질의에서 전부 일치).

  무작위 질의 평균(계산 시간 / 탐색 수):

  | 데이터 | 다익스트라 | 원래 A\* (질의마다 역방향 다익스트라) | 지금 A\* (랜드마크) | 한 번 하는 준비 |
  |---|---|---|---|---|
  | 서울 | 0.035ms / 탐색 272 | 0.105ms / 34 | 0.014ms / 46 | 로딩 포함 68ms |
  | 가상 k=30 | 0.78ms / 4,213 | 1.76ms / 193 | 0.17ms / 456 | 37ms |
  | 가상 k=100 | 14.6ms / 49,354 | 33.2ms / 3,433 | 4.7ms / 8,569 | 0.8초 |

- **GA는 Susu 브랜치 코드 그대로**다.
- **`/api/compare` 추가.** 세 알고리즘의 점수, 실제 시간, 계산 시간(3번 중 가운데 값), 탐색 수를 돌려준다.
- **화면:** 결과 아래에 알고리즘 비교표. 줄을 누르면 그 알고리즘의 경로를 지도에 그린다.

## API

- `GET /api/route?from=교대&to=강동&day=weekday&time=18:00&algorithm=dijkstra|astar|ga`
- `GET /api/compare?from=교대&to=강동&day=weekday&time=18:00`
- `GET /api/graph`, `GET /healthz`

## 테스트

```bash
g++ -O2 -std=c++17 tests/route_check_test.cpp -o route_check_test -lws2_32 && ./route_check_test data   # 검증 값·막차·A*와 다익스트라
g++ -O2 -std=c++17 tests/route_ga_test.cpp -o route_ga_test -lws2_32 && ./route_ga_test data           # Susu의 GA 테스트
```

## 배포

Docker로 배포하는 방법은 [DEPLOY.md](DEPLOY.md)에 있다. 요약: `docker compose up --build` 후 `http://localhost:8080`.
