// 다익스트라·A* 점검: 설명서의 검증 값, 막차 질의, A*와 다익스트라의 관계 (GA는 route_ga_test.cpp)
// 빌드(윈도우): g++ -O2 -std=c++17 tests/route_check_test.cpp -o route_check_test.exe -lws2_32
// 실행(프로젝트 폴더에서): ./route_check_test data
#define main route_server_main
#include "../route_server.cpp"
#undef main
#include <cmath>
#include <random>

static int fails = 0, checks = 0;
static void check(bool ok, const string& what) {
    checks++;
    if (!ok) { fails++; printf("FAIL: %s\n", what.c_str()); }
}

int main(int argc, char** argv) {
    Data D; D.load(argc > 1 ? argv[1] : "data");

    // 1. 설명서 "검증용 예시"의 방식 B 점수(분)
    struct Q { const char *a, *b; int day, t; double score; } qs[] = {
        {"광명", "가산디지털단지", WEEKDAY, 30600, 11.9}, {"광명", "천안", WEEKDAY, 30600, 95.0},
        {"선릉", "수서", WEEKDAY, 30600, 33.2}, {"김포공항", "고속터미널", WEEKDAY, 30600, 62.9},
        {"당산", "석촌", WEEKDAY, 30600, 53.2}, {"여의도", "종합운동장", WEEKDAY, 30600, 40.4},
        {"개화", "중앙보훈병원", WEEKDAY, 64800, 97.3}, {"가양", "신논현", SAT, 45000, 45.0},
        {"노량진", "동작", WEEKDAY, 30600, 8.2}};
    for (auto& q : qs) {
        Result r = route_dijkstra(D, q.a, q.b, q.day, q.t, false);
        check(r.found && fabs(r.score / 60 - q.score) < 0.051, string(q.a) + "→" + q.b + " 점수");
    }

    // 2. 막차·첫차 (설명서 "심야·막차 질의")
    check(route_dijkstra(D, "선릉", "수서", WEEKDAY, 1800, false).found, "선릉→수서 평일 00:30 탈 수 있음");
    Result sat = route_dijkstra(D, "선릉", "수서", SAT, 1800, false);
    check(!sat.found && sat.no_train && sat.origin_last == 86310, "선릉→수서 토 00:30 막차 23:58");
    check(route_dijkstra(D, "김포공항", "개화", SAT, 7 * 60, false).found, "김포공항→개화 토 24:07 탈 수 있음");
    check(!route_dijkstra(D, "김포공항", "개화", SAT, 8 * 60, false).found, "김포공항→개화 토 24:08 막힘");
    check(route_dijkstra(D, "송파나루", "중앙보훈병원", SAT, 0, false).found, "송파나루 24:00 이어 타기");

    // 3. 노드당 기록 하나일 때 생기던 심야 버그: 최단 시간 경로가 2호선을 반대로 한 바퀴 돌던 경우
    Result f = route_dijkstra(D, "아차산", "잠실새내", SUN, 23 * 3600 + 1800, true);
    check(f.found && f.real / 60 < 40, "아차산→잠실새내 일 23:30 최단 시간이 40분 미만");

    // 4. 무작위 질의: A* = 다익스트라(점수), 점수 경로가 있고 최단 시간 경로보다 빠르지 않음
    vector<string> names;
    for (auto& kv : D.by_name) names.push_back(kv.first);
    sort(names.begin(), names.end());
    mt19937 rng(42);
    int times[] = {6 * 3600, 8 * 3600 + 1800, 12 * 3600, 18 * 3600, 23 * 3600};
    for (int i = 0; i < 400; i++) {
        string a = names[rng() % names.size()], b = names[rng() % names.size()];
        if (a == b) continue;
        int day = rng() % 3, t = times[rng() % 5];
        string tag = a + "→" + b + " day " + to_string(day) + " t " + to_string(t);
        Result dr = route_dijkstra(D, a, b, day, t, true), dc = route_dijkstra(D, a, b, day, t, false);
        Result ar = route_astar(D, a, b, day, t, true), ac = route_astar(D, a, b, day, t, false);
        check(dc.found == ac.found && (!dc.found || fabs(dc.score - ac.score) < 1e-6), "A* = 다익스트라(점수) " + tag);
        check(dr.found == ar.found && (!dr.found || fabs(dr.real - ar.real) < 1e-6), "A* = 다익스트라(시간) " + tag);
        check(!dc.found || ac.expanded <= dc.expanded, "A*가 다익스트라보다 많이 탐색하지 않음 " + tag);
        if (!dr.found) continue;
        check(dc.found, "최단 시간 경로가 있으면 점수 경로도 있음 " + tag);
        check(dc.real + 1e-6 >= dr.real, "점수 경로가 최단 시간보다 빠르지 않음 " + tag);
    }
    printf("%s: %d checks, %d failed\n", fails ? "FAIL" : "PASS", checks, fails);
    return fails ? 1 : 0;
}
