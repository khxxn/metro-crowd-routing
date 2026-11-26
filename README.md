# 5분 더 걸려도 덜 붐비는 길은? — 합친 버전 (merge-all)

다익스트라(main), A*·GA(Susu)를 C++ 서버 하나와 지도 화면 하나로 합쳤다. 화면에서 알고리즘을 골라 경로를 보고,
같은 질의를 세 알고리즘으로 푼 결과를 한 표로 비교한다.

## 실행

```bash
# 윈도우(Git Bash)
g++ -O2 -std=c++17 -static route_server.cpp -o route_server -lws2_32
./route_server data route_ui.html
# 리눅스·맥은 -static, -lws2_32 없이 빌드
```

브라우저에서 `http://127.0.0.1:8080`을 연다. 포트를 바꾸려면 `./route_server data route_ui.html 9000`.

## 알고리즘

세 알고리즘 모두 같은 문제(점수 최소 경로)를 같은 비용 식, 팀 표준인 방식 B(간선에 들어서는 시각으로 시간대를 다시 찾음),
같은 막차·첫차 규칙으로 푼다. 다익스트라가 비교의 기준이다.

| 화면 이름 | 함수 | 출처 | 비교할 것 |
|---|---|---|---|
| 다익스트라 | `route()` → `search()` | main | 기준 |
| A* | `route_astar()` → `search()` | Susu | 다익스트라와 같은 답인지, 탐색 수·계산 시간이 얼마나 줄었는지 |
| GA | `route_ga()` | Susu | 다익스트라 점수와 얼마나 차이 나는지, 계산 시간 |

각 함수는 `by_real = true`면 최단 시간 경로를, `false`면 점수 최소(덜 붐비는) 경로를 돌려준다.

## 합치면서 바뀐 점

- **탐색 상태를 (노드, 열차를 탄 채로 왔나) 둘로 나눔.** 노드마다 기록을 하나만 두면, 환승해서 온 빠른 기록이 막차에 막히고 계속 타고 온 기록은 버려져서 심야에 경로를 못 찾거나 크게 돌아갔다(예: 아차산→잠실새내 일 23:30, 최단 시간이 88.8분 → 34.8분). 무작위 6,000개 질의(최단 시간·점수 각각, 11,960번 탐색)에서 264번 결과가 바뀌었고 모두 더 좋아졌다.
- **다익스트라와 A\*가 같은 탐색 함수 `search()`를 쓴다.** A\*는 Susu 브랜치의 하한 h(혼잡 가산·운행 제한을 뺀 남은 비용)를 그대로 넘긴다. 무작위 질의에서 다익스트라와 점수가 모두 같고, 평일 08:30 무작위 2,000질의 평균 탐색 수는 다익스트라 287, A\* 30이다.
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
