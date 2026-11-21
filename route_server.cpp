// route_server.cpp : 지하철 혼잡도 경로 서버 (외부 라이브러리 없음, 윈도우/리눅스/맥 겸용)
//   - 경로 계산은 route_cmp.cpp와 같은 방식 B(시간 흐름 반영) 다익스트라, 막차·첫차 검사 포함
//   - 같은 프로그램이 웹페이지(route_ui.html)도 내려준다 → 브라우저에서 http://127.0.0.1:8080 으로 연다
//
// 빌드(윈도우 MinGW / Git Bash): g++ -O2 -std=c++17 route_server.cpp -o route_server -lws2_32
// 빌드(리눅스·맥)              : g++ -O2 -std=c++17 route_server.cpp -o route_server
// 실행: ./route_server <data폴더> <화면 html 경로> [포트=8080] [all]     예) ./route_server data route_ui.html
//        끝에 all 을 붙이면 같은 와이파이의 다른 기기(휴대폰 등)에서도 접속할 수 있다. 기본은 이 컴퓨터에서만.
//        Docker·클라우드: 환경변수 PORT(포트), BIND_ALL=1(모든 주소에서 받기)로도 지정할 수 있다.
//
// API
//   GET /api/graph                                   → 노선도 그리기용 역·구간 목록
//   GET /api/route?from=교대&to=강동&day=weekday&time=18:00 → 최단 시간 경로(f)와 혼잡 반영 경로(c)
//   algorithm=dijkstra(기본값), astar, ga로 탐색 알고리즘 선택
#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <queue>
#include <random>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#ifdef _MSC_VER
#pragma comment(lib, "ws2_32.lib")
#endif
typedef SOCKET sock_t;
static const sock_t BAD_SOCK = INVALID_SOCKET;
static void close_sock(sock_t s) { closesocket(s); }
static void shut_send(sock_t s) { shutdown(s, SD_SEND); }
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>
typedef int sock_t;
static const sock_t BAD_SOCK = -1;
static void close_sock(sock_t s) { close(s); }
static void shut_send(sock_t s) { shutdown(s, SHUT_WR); }
#endif
#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif
using namespace std;

// ---------- 확정 식의 상수 ----------
constexpr double ALPHA = 0.00631;  // 혼잡 계수
constexpr double C0 = 33.75;       // 임계 혼잡도(%)
constexpr int FIRST_SEC = 19800;   // 05:30 (자정부터 초)
constexpr int SLOT_SEC = 1800;     // 30분
constexpr int NSLOT = 39;          // 0 = 05:30~06:00, ..., 38 = 24:30~25:00

enum Day { WEEKDAY = 0, SAT = 1, SUN = 2 };

int slot_of(int t) {
    if (t < FIRST_SEC) t += 86400;
    return min((t - FIRST_SEC) / SLOT_SEC, NSLOT - 1);
}
int clock_of(int t) { return t < 4 * 3600 ? t + 86400 : t; }

// ---------- CSV 읽기 (UTF-8 BOM 제거, CRLF 허용, 따옴표 없음) ----------
vector<vector<string>> read_csv(const string& path) {
    ifstream f(path, ios::binary);
    if (!f) { cerr << "열 수 없음: " << path << "\n"; exit(1); }
    vector<vector<string>> rows;
    string line;
    bool first = true;
    while (getline(f, line)) {
        if (first) {
            first = false;
            if (line.size() >= 3 && (unsigned char)line[0] == 0xEF && (unsigned char)line[1] == 0xBB &&
                (unsigned char)line[2] == 0xBF)
                line.erase(0, 3);
        }
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        vector<string> cols;
        size_t p = 0;
        while (true) {
            size_t q = line.find(',', p);
            if (q == string::npos) { cols.push_back(line.substr(p)); break; }
            cols.push_back(line.substr(p, q - p));
            p = q + 1;
        }
        rows.push_back(move(cols));
    }
    return rows;
}
double num(const string& s) { return s.empty() ? 0.0 : stod(s); }
int col(const vector<string>& header, const string& name) {
    for (size_t i = 0; i < header.size(); i++) if (header[i] == name) return (int)i;
    cerr << "열 없음: " << name << "\n"; exit(1);
}
int day_of(const string& s) { return s == "weekday" ? WEEKDAY : s == "sat" ? SAT : SUN; }

// ---------- 데이터 ----------
struct TrVal { int walk = 0; double wait = 0, cost = 0; bool ok = false; };
struct Data {
    vector<string> id, name, line, lat, lon;             // station_nodes.csv (lat, lon은 CSV 글자 그대로)
    unordered_map<string, int> idx;
    unordered_map<string, vector<int>> by_name;
    struct Ride { int to, sec, e; int first[3], last[3]; };
    struct Tr { int to, e; };
    vector<vector<Ride>> ride;
    vector<vector<Tr>> tr;
    vector<array<array<float, NSLOT>, 3>> cong;
    vector<array<array<TrVal, NSLOT>, 3>> trv;

    void load(const string& dir) {
        auto N = read_csv(dir + "/station_nodes.csv");
        int ci = col(N[0], "id"), cn = col(N[0], "station_name"), cl = col(N[0], "line_key"),
            cla = col(N[0], "lat"), clo = col(N[0], "lon");
        for (size_t r = 1; r < N.size(); r++) {
            idx[N[r][ci]] = (int)id.size();
            by_name[N[r][cn]].push_back((int)id.size());
            id.push_back(N[r][ci]); name.push_back(N[r][cn]); line.push_back(N[r][cl]);
            lat.push_back(N[r][cla]); lon.push_back(N[r][clo]);
        }
        ride.assign(id.size(), {}); tr.assign(id.size(), {});

        auto E = read_csv(dir + "/station_edges.csv");
        int ef = col(E[0], "from_node_id"), et = col(E[0], "to_node_id"), es = col(E[0], "seconds");
        const char* dn[3] = {"weekday", "sat", "sun"};
        int fc[3], lc[3];
        for (int d = 0; d < 3; d++) {
            fc[d] = col(E[0], string("first_dep_") + dn[d] + "_sec");
            lc[d] = col(E[0], string("last_dep_") + dn[d] + "_sec");
        }
        unordered_map<string, int> emap;
        for (size_t r = 1; r < E.size(); r++) {
            int e = (int)cong.size();
            emap[E[r][ef] + "," + E[r][et]] = e;
            Ride rd{idx.at(E[r][et]), stoi(E[r][es]), e, {}, {}};
            for (int d = 0; d < 3; d++) { rd.first[d] = stoi(E[r][fc[d]]); rd.last[d] = stoi(E[r][lc[d]]); }
            ride[idx.at(E[r][ef])].push_back(rd);
            cong.emplace_back();
            for (auto& d : cong.back()) d.fill(0.f);
        }

        auto C = read_csv(dir + "/ride_congestion_filled.csv");
        int cf = col(C[0], "from_node_id"), ct = col(C[0], "to_node_id"), cd = col(C[0], "day_type"),
            cs = col(C[0], "slot_idx"), cp = col(C[0], "congestion_pct");
        for (size_t r = 1; r < C.size(); r++)
            cong[emap.at(C[r][cf] + "," + C[r][ct])][day_of(C[r][cd])][stoi(C[r][cs])] = stof(C[r][cp]);

        auto T = read_csv(dir + "/transfer_edges_cost.csv");
        int tf = col(T[0], "from_node_id"), tt = col(T[0], "to_node_id"), td = col(T[0], "day_type"),
            ts = col(T[0], "slot_idx"), tw = col(T[0], "walk_sec"), tq = col(T[0], "wait_sec"),
            tc = col(T[0], "cost_sec"), tk = col(T[0], "status");
        unordered_map<string, int> tmap;
        for (size_t r = 1; r < T.size(); r++) {
            string key = T[r][tf] + "," + T[r][tt];
            auto it = tmap.find(key);
            if (it == tmap.end()) {
                it = tmap.emplace(key, (int)trv.size()).first;
                trv.emplace_back();
                tr[idx.at(T[r][tf])].push_back({idx.at(T[r][tt]), it->second});
            }
            TrVal& v = trv[it->second][day_of(T[r][td])][stoi(T[r][ts])];
            v.walk = stoi(T[r][tw]); v.wait = num(T[r][tq]); v.cost = num(T[r][tc]);
            v.ok = (T[r][tk] == "OK");
        }
    }
};

inline double ride_cost(int seconds, double c) { return seconds * (1.0 + ALPHA * max(c - C0, 0.0)); }

// ---------- 다익스트라 (route_cmp.cpp의 route()와 같은 로직 + 단계별 값 기록) ----------
struct Step { int type; int from, to; double real, score, cong; };   // type 1 = 탑승, 0 = 환승
struct Result {
    bool found = false;
    bool no_train = false;
    bool search_limited = false;  // GA가 탐색 예산 안에서 유효 경로를 찾지 못한 경우
    int origin_last = -1;
    double score = 0, real = 0;
    vector<int> path;
    vector<Step> steps;
};

// by_real = true: 실제 소요시간 최소(일반 최단시간), false: 점수(혼잡 반영 비용) 최소. 시간 흐름 반영(방식 B).
Result route(const Data& D, const string& from, const string& to, int day, int depart_sec, bool by_real) {
    int n = (int)D.id.size();
    vector<double> key(n, 1e18), real(n, 0), sco(n, 0), s_real(n, 0), s_score(n, 0), s_cong(n, 0);
    vector<int> prev(n, -1);
    vector<char> by_ride(n, 0), s_type(n, 0);
    bool no_train = false; int origin_last = -1;
    using P = pair<double, int>;
    priority_queue<P, vector<P>, greater<P>> pq;
    for (int s : D.by_name.at(from)) { key[s] = 0; pq.push({0, s}); }
    vector<char> is_dst(n, 0);
    for (int d : D.by_name.at(to)) is_dst[d] = 1;

    while (!pq.empty()) {
        auto [d, u] = pq.top(); pq.pop();
        if (d > key[u]) continue;
        if (is_dst[u]) {
            Result r; r.found = true; r.score = sco[u]; r.real = real[u];
            for (int x = u; x != -1; x = prev[x]) r.path.push_back(x);
            reverse(r.path.begin(), r.path.end());
            for (size_t i = 1; i < r.path.size(); i++) {
                int x = r.path[i];
                r.steps.push_back({s_type[x], r.path[i - 1], x, s_real[x], s_score[x], s_cong[x]});
            }
            return r;
        }
        int now = depart_sec + (int)real[u];
        int slot = slot_of(now);
        for (const auto& e : D.ride[u]) {
            if (!by_ride[u]) {
                int t = clock_of(now);
                if (t < e.first[day] || t > e.last[day]) {
                    no_train = true;
                    if (key[u] == 0 && t > e.last[day]) origin_last = max(origin_last, e.last[day]);
                    continue;
                }
            }
            double c = D.cong[e.e][day][slot];
            double ds = ride_cost(e.sec, c), dr = e.sec;
            double w = by_real ? dr : ds;
            if (d + w < key[e.to]) {
                key[e.to] = d + w; real[e.to] = real[u] + dr; sco[e.to] = sco[u] + ds; prev[e.to] = u; by_ride[e.to] = 1;
                s_type[e.to] = 1; s_real[e.to] = dr; s_score[e.to] = ds; s_cong[e.to] = c;
                pq.push({key[e.to], e.to});
            }
        }
        for (const auto& e : D.tr[u]) {
            const TrVal& v = D.trv[e.e][day][slot];
            if (!v.ok) { no_train = true; continue; }
            double ds = v.cost, dr = v.walk + v.wait;
            double w = by_real ? dr : ds;
            if (d + w < key[e.to]) {
                key[e.to] = d + w; real[e.to] = real[u] + dr; sco[e.to] = sco[u] + ds; prev[e.to] = u; by_ride[e.to] = 0;
                s_type[e.to] = 0; s_real[e.to] = dr; s_score[e.to] = ds; s_cong[e.to] = 0;
                pq.push({key[e.to], e.to});
            }
        }
    }
    Result none; none.no_train = no_train; none.origin_last = origin_last;
    return none;
}

// ---------- A* (기존 route()와 같은 입력·비용식·결과 형식) ----------
// h: 혼잡 가산·운행 제한을 제외한 이동시간 + 해당 요일의 최소 환승 비용.
// 역방향 그래프에서 목적지까지의 하한을 구해, 남은 비용을 과대평가하지 않는다.
// 기존 route()처럼 역마다 후보 하나를 유지하므로 시간 의존 비용의 최적성 한계도 같다.
Result route_astar(const Data& D, const string& from, const string& to, int day, int depart_sec, bool by_real) {
    int n = (int)D.id.size();
    using P = pair<double, int>;
    vector<vector<P>> reverse_edges(n);  // {비용 하한, 이전 역}
    for (int u = 0; u < n; u++) {
        for (const auto& e : D.ride[u]) reverse_edges[e.to].push_back({(double)e.sec, u});
        for (const auto& e : D.tr[u]) {
            double lower = 1e18;
            for (const TrVal& v : D.trv[e.e][day])
                if (v.ok) lower = min(lower, by_real ? v.walk + v.wait : v.cost);
            if (lower < 1e18) reverse_edges[e.to].push_back({lower, u});
        }
    }

    vector<double> h(n, 1e18);
    vector<char> is_dst(n, 0);
    priority_queue<P, vector<P>, greater<P>> hpq;
    for (int d : D.by_name.at(to)) { is_dst[d] = 1; h[d] = 0; hpq.push({0, d}); }
    while (!hpq.empty()) {
        auto [d, u] = hpq.top(); hpq.pop();
        if (d > h[u]) continue;
        for (const auto& [w, v] : reverse_edges[u]) {
            if (d + w < h[v]) { h[v] = d + w; hpq.push({h[v], v}); }
        }
    }

    vector<double> key(n, 1e18), real(n, 0), sco(n, 0), s_real(n, 0), s_score(n, 0), s_cong(n, 0);
    vector<int> prev(n, -1);
    vector<char> by_ride(n, 0), s_type(n, 0);
    bool no_train = false; int origin_last = -1;
    using Q = pair<double, P>;  // {g + h, {g, 역}}; 실제 누적 비용 g로 오래된 항목을 검사한다.
    priority_queue<Q, vector<Q>, greater<Q>> pq;
    for (int s : D.by_name.at(from)) { key[s] = 0; pq.push({h[s], {0, s}}); }

    while (!pq.empty()) {
        auto [d, u] = pq.top().second; pq.pop();
        if (d > key[u]) continue;
        if (is_dst[u]) {
            Result r; r.found = true; r.score = sco[u]; r.real = real[u];
            for (int x = u; x != -1; x = prev[x]) r.path.push_back(x);
            reverse(r.path.begin(), r.path.end());
            for (size_t i = 1; i < r.path.size(); i++) {
                int x = r.path[i];
                r.steps.push_back({s_type[x], r.path[i - 1], x, s_real[x], s_score[x], s_cong[x]});
            }
            return r;
        }
        int now = depart_sec + (int)real[u];
        int slot = slot_of(now);
        for (const auto& e : D.ride[u]) {
            if (!by_ride[u]) {
                int t = clock_of(now);
                if (t < e.first[day] || t > e.last[day]) {
                    no_train = true;
                    if (key[u] == 0 && t > e.last[day]) origin_last = max(origin_last, e.last[day]);
                    continue;
                }
            }
            double c = D.cong[e.e][day][slot];
            double ds = ride_cost(e.sec, c), dr = e.sec;
            double w = by_real ? dr : ds;
            if (d + w < key[e.to]) {
                key[e.to] = d + w; real[e.to] = real[u] + dr; sco[e.to] = sco[u] + ds; prev[e.to] = u; by_ride[e.to] = 1;
                s_type[e.to] = 1; s_real[e.to] = dr; s_score[e.to] = ds; s_cong[e.to] = c;
                pq.push({key[e.to] + h[e.to], {key[e.to], e.to}});
            }
        }
        for (const auto& e : D.tr[u]) {
            const TrVal& v = D.trv[e.e][day][slot];
            if (!v.ok) { no_train = true; continue; }
            double ds = v.cost, dr = v.walk + v.wait;
            double w = by_real ? dr : ds;
            if (d + w < key[e.to]) {
                key[e.to] = d + w; real[e.to] = real[u] + dr; sco[e.to] = sco[u] + ds; prev[e.to] = u; by_ride[e.to] = 0;
                s_type[e.to] = 0; s_real[e.to] = dr; s_score[e.to] = ds; s_cong[e.to] = 0;
                pq.push({key[e.to] + h[e.to], {key[e.to], e.to}});
            }
        }
    }
    Result none; none.no_train = no_train; none.origin_last = origin_last;
    return none;
}

// ---------- GA (경로를 염색체로 사용하는 유전 알고리즘) ----------
// 적합도는 기존과 동일하게 by_real ? real : score를 최소화한다. 별도 혼잡 계수나 벌점은 없다.
// 연결·운행 조건이 유효한 경로만 사용하며, 다익스트라/A*의 경로를 제외하거나 초기해로 쓰지 않는다.
Result route_ga(const Data& D, const string& from, const string& to, int day, int depart_sec, bool by_real) {
    constexpr int POPULATION = 32, GENERATIONS = 40, SEARCH_BUDGET = 4000;
    constexpr double CROSSOVER_RATE = 0.8, MUTATION_RATE = 0.3;
    mt19937 rng(20261121u);  // 같은 입력의 실험을 재현할 수 있도록 시드를 고정한다.
    auto random_index = [&](int count) { return uniform_int_distribution<int>(0, count - 1)(rng); };
    auto chance = [&](double p) { return bernoulli_distribution(p)(rng); };
    int n = (int)D.id.size();
    const auto& origins = D.by_name.at(from);
    vector<char> is_dst(n, 0);
    for (int d : D.by_name.at(to)) is_dst[d] = 1;
    for (int s : origins) {
        if (is_dst[s]) { Result r; r.found = true; r.path.push_back(s); return r; }
    }

    struct Gene { int from, to, type, index; };  // index는 해당 역의 ride/tr 목록 내 위치
    struct Individual { vector<Gene> genes; Result result; };
    vector<vector<Gene>> edges(n);
    vector<vector<int>> reverse_edges(n);
    for (int u = 0; u < n; u++) {
        for (int i = 0; i < (int)D.ride[u].size(); i++) edges[u].push_back({u, D.ride[u][i].to, 1, i});
        for (int i = 0; i < (int)D.tr[u].size(); i++) edges[u].push_back({u, D.tr[u][i].to, 0, i});
        for (const auto& e : edges[u]) reverse_edges[e.to].push_back(u);
    }
    // 초기 경로/돌연변이 생성 시 목적지와 연결되지 않은 가지는 방문하지 않는다.
    vector<int> hops(n, -1);
    queue<int> pending;
    for (int d : D.by_name.at(to)) { hops[d] = 0; pending.push(d); }
    while (!pending.empty()) {
        int u = pending.front(); pending.pop();
        for (int v : reverse_edges[u]) if (hops[v] < 0) { hops[v] = hops[u] + 1; pending.push(v); }
    }

    bool no_train = false, search_limited = false;
    int origin_last = -1;
    auto advance = [&](const Gene& g, double elapsed, bool aboard, Step& step) {
        int now = depart_sec + (int)elapsed, slot = slot_of(now);
        if (g.type == 1) {
            const auto& e = D.ride[g.from][g.index];
            if (!aboard) {
                int t = clock_of(now);
                if (t < e.first[day] || t > e.last[day]) {
                    no_train = true;
                    if (elapsed == 0 && t > e.last[day]) origin_last = max(origin_last, e.last[day]);
                    return false;
                }
            }
            double c = D.cong[e.e][day][slot];
            step = {1, g.from, g.to, (double)e.sec, ride_cost(e.sec, c), c};
        } else {
            const auto& e = D.tr[g.from][g.index];
            const TrVal& v = D.trv[e.e][day][slot];
            if (!v.ok) { no_train = true; return false; }
            step = {0, g.from, g.to, v.walk + v.wait, v.cost, 0};
        }
        return true;
    };
    // 교차·돌연변이 후에도 처음부터 시각을 진행하며 모든 구간의 비용을 다시 평가한다.
    auto evaluate = [&](int start, const vector<Gene>& genes) {
        Individual child; child.genes = genes;
        Result& r = child.result;
        r.path.push_back(start);
        vector<char> seen(n, 0); seen[start] = 1;
        bool aboard = false;
        for (const auto& g : genes) {
            Step step;
            if (g.from != r.path.back() || seen[g.to] || is_dst[g.from] || !advance(g, r.real, aboard, step)) return Individual{};
            r.path.push_back(g.to); r.steps.push_back(step);
            r.real += step.real; r.score += step.score;
            seen[g.to] = 1; aboard = g.type == 1;
        }
        r.found = is_dst[r.path.back()];
        return child;
    };
    // 유효한 접두 경로 뒤를 무작위 DFS로 생성한다. 무한 탐색을 막기 위해 확장 횟수를 제한한다.
    auto grow = [&](int start, const vector<Gene>& prefix) {
        Individual child = evaluate(start, prefix);
        Result& r = child.result;
        if (r.path.empty() || hops[r.path.back()] < 0) return Individual{};
        vector<char> seen(n, 0);
        for (int u : r.path) seen[u] = 1;
        int remaining = SEARCH_BUDGET;
        auto visit = [&](auto&& self, int u, bool aboard) -> bool {
            if (is_dst[u]) return true;
            if (remaining-- <= 0) { search_limited = true; return false; }
            auto choices = edges[u];
            shuffle(choices.begin(), choices.end(), rng);
            if (chance(0.7)) stable_sort(choices.begin(), choices.end(), [&](const Gene& a, const Gene& b) { return hops[a.to] < hops[b.to]; });
            for (const auto& g : choices) {
                if (seen[g.to] || hops[g.to] < 0) continue;
                Step step;
                if (!advance(g, r.real, aboard, step)) continue;
                double old_real = r.real, old_score = r.score;
                child.genes.push_back(g); r.path.push_back(g.to); r.steps.push_back(step);
                r.real += step.real; r.score += step.score; seen[g.to] = 1;
                if (self(self, g.to, g.type == 1)) return true;
                seen[g.to] = 0; r.real = old_real; r.score = old_score;
                child.genes.pop_back(); r.path.pop_back(); r.steps.pop_back();
                if (remaining <= 0) { search_limited = true; break; }
            }
            return false;
        };
        r.found = visit(visit, r.path.back(), !r.steps.empty() && r.steps.back().type == 1);
        return r.found ? child : Individual{};
    };
    // 공통 역에서 교차한 결과에 순환이 생기면 해당 순환 구간을 제거한다.
    auto repair = [&](int start, const vector<Gene>& genes) {
        vector<Gene> clean;
        vector<int> position(n, -1); position[start] = 0;
        for (const auto& g : genes) {
            int cut = position[g.to];
            if (cut < 0) { clean.push_back(g); position[g.to] = (int)clean.size(); }
            else while ((int)clean.size() > cut) { position[clean.back().to] = -1; clean.pop_back(); }
        }
        return clean;
    };
    auto better = [&](const Individual& a, const Individual& b) {
        return by_real ? a.result.real < b.result.real : a.result.score < b.result.score;
    };

    vector<Individual> population;
    for (int attempt = 0; attempt < POPULATION * 4 && (int)population.size() < POPULATION; attempt++) {
        Individual child = grow(origins[attempt % origins.size()], {});
        if (child.result.found) population.push_back(move(child));
    }
    if (population.empty()) {
        Result none; none.no_train = no_train; none.origin_last = origin_last; none.search_limited = search_limited;
        return none;
    }
    while ((int)population.size() < POPULATION) { Individual copy = population[random_index((int)population.size())]; population.push_back(move(copy)); }

    for (int generation = 0; generation < GENERATIONS; generation++) {
        stable_sort(population.begin(), population.end(), better);
        vector<Individual> next{population[0], population[1]};  // 가장 좋은 개체 2개 보존
        auto select = [&]() -> const Individual& {  // 개체 3개의 토너먼트 선택
            int best = random_index(POPULATION);
            for (int i = 0; i < 2; i++) { int j = random_index(POPULATION); if (better(population[j], population[best])) best = j; }
            return population[best];
        };
        while ((int)next.size() < POPULATION) {
            const Individual& a = select();
            Individual child = a;
            if (chance(CROSSOVER_RATE)) {
                const Individual& b = select();
                vector<int> position(n, -1);
                for (int j = 1; j + 1 < (int)b.result.path.size(); j++) position[b.result.path[j]] = j;
                vector<pair<int, int>> cuts;
                for (int i = 1; i + 1 < (int)a.result.path.size(); i++) {
                    int j = position[a.result.path[i]];
                    if (j >= 0) cuts.push_back({i, j});
                }
                if (!cuts.empty()) {
                    auto [i, j] = cuts[random_index((int)cuts.size())];
                    vector<Gene> genes(a.genes.begin(), a.genes.begin() + i);
                    genes.insert(genes.end(), b.genes.begin() + j, b.genes.end());
                    Individual crossed = evaluate(a.result.path.front(), repair(a.result.path.front(), genes));
                    if (crossed.result.found) child = move(crossed);
                }
            }
            if (!child.genes.empty() && chance(MUTATION_RATE)) {
                int cut = random_index((int)child.genes.size());
                vector<Gene> prefix(child.genes.begin(), child.genes.begin() + cut);
                Individual mutant = grow(child.result.path.front(), prefix);
                if (mutant.result.found) child = move(mutant);
            }
            next.push_back(move(child));
        }
        population = move(next);
    }
    return min_element(population.begin(), population.end(), better)->result;
}

// ---------- JSON ----------
string jnum(double v) { char b[40]; snprintf(b, sizeof b, "%.17g", v); return b; }
string jstr(const string& s) {
    string o = "\"";
    for (unsigned char c : s) {
        if (c == '"') o += "\\\"";
        else if (c == '\\') o += "\\\\";
        else if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else o += (char)c;
    }
    return o + "\"";
}
string json_result(const Result& r) {
    string o = "{\"real\":" + jnum(r.real) + ",\"score\":" + jnum(r.score) + ",\"path\":[";
    for (size_t i = 0; i < r.path.size(); i++) o += (i ? "," : "") + to_string(r.path[i]);
    o += "],\"steps\":[";
    for (size_t i = 0; i < r.steps.size(); i++) {
        const Step& s = r.steps[i];
        o += string(i ? "," : "") + "[" + to_string(s.type) + "," + to_string(s.from) + "," + to_string(s.to) + "," +
             jnum(s.real) + "," + jnum(s.score) + "," + jnum(s.cong) + "]";
    }
    return o + "]}";
}
string json_graph(const Data& D) {
    string o = "{\"nodes\":[";
    for (size_t i = 0; i < D.id.size(); i++)
        o += string(i ? "," : "") + "[" + jstr(D.id[i]) + "," + jstr(D.name[i]) + "," + jstr(D.line[i]) + "," + D.lat[i] + "," + D.lon[i] + "]";
    o += "],\"edges\":[";
    set<pair<int, int>> seen; bool first = true;
    for (size_t u = 0; u < D.ride.size(); u++)
        for (const auto& e : D.ride[u]) {
            pair<int, int> k = {min((int)u, e.to), max((int)u, e.to)};
            if (!seen.insert(k).second) continue;
            o += string(first ? "" : ",") + "[" + to_string(u) + "," + to_string(e.to) + "]"; first = false;
        }
    return o + "]}";
}

// ---------- HTTP ----------
string url_decode(const string& s) {
    string o;
    for (size_t i = 0; i < s.size(); i++) {
        if (s[i] == '%' && i + 2 < s.size() + 0 && isxdigit((unsigned char)s[i + 1]) && isxdigit((unsigned char)s[i + 2])) {
            o += (char)stoi(s.substr(i + 1, 2), nullptr, 16); i += 2;
        } else if (s[i] == '+') o += ' ';
        else o += s[i];
    }
    return o;
}
unordered_map<string, string> parse_query(const string& q) {
    unordered_map<string, string> m;
    size_t p = 0;
    while (p <= q.size()) {
        size_t e = q.find('&', p); if (e == string::npos) e = q.size();
        string kv = q.substr(p, e - p); size_t eq = kv.find('=');
        if (eq != string::npos) m[url_decode(kv.substr(0, eq))] = url_decode(kv.substr(eq + 1));
        p = e + 1;
    }
    return m;
}
string response(int code, const char* msg, const string& ctype, const string& body, bool head) {
    string h = "HTTP/1.1 " + to_string(code) + " " + msg + "\r\nContent-Type: " + ctype + "\r\nContent-Length: " + to_string(body.size()) +
               "\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n";
    return head ? h : h + body;
}
const string JSON_T = "application/json; charset=utf-8";

struct App { Data D; string page_path; string graph_json; };

string handle(const App& A, const string& req, string& log) {
    size_t le = req.find("\r\n");
    string line = req.substr(0, le), method, target;
    { istringstream is(line); string ver; is >> method >> target >> ver; }
    bool head = method == "HEAD";
    log = method + " " + target;
    if (method != "GET" && !head) return response(405, "Method Not Allowed", "text/plain; charset=utf-8", "GET만 지원합니다", false);
    string path = target, query;
    size_t qm = target.find('?');
    if (qm != string::npos) { path = target.substr(0, qm); query = target.substr(qm + 1); }

    if (path == "/" || path == "/index.html") {
        ifstream f(A.page_path, ios::binary);
        if (!f) return response(500, "Internal Server Error", "text/plain; charset=utf-8", "화면 html을 열 수 없음: " + A.page_path, false);
        stringstream ss; ss << f.rdbuf();
        return response(200, "OK", "text/html; charset=utf-8", ss.str(), head);
    }
    if (path == "/healthz") return response(200, "OK", "text/plain; charset=utf-8", "ok", head);   // 배포 서비스의 상태 확인용
    if (path == "/api/graph") return response(200, "OK", JSON_T, A.graph_json, head);
    if (path == "/api/route") {
        auto q = parse_query(query);
        auto bad = [&](const string& why) { return response(400, "Bad Request", JSON_T, "{\"ok\":false,\"error\":" + jstr(why) + "}", head); };
        if (!q.count("from") || !q.count("to") || !q.count("day") || !q.count("time")) return bad("from, to, day, time이 필요합니다");
        const string &from = q["from"], &to = q["to"], &ds = q["day"], &ts = q["time"];
        if (!A.D.by_name.count(from)) return bad("알 수 없는 출발역: " + from);
        if (!A.D.by_name.count(to)) return bad("알 수 없는 도착역: " + to);
        if (ds != "weekday" && ds != "sat" && ds != "sun") return bad("day는 weekday, sat, sun 중 하나");
        if (ts.size() != 5 || ts[2] != ':' || !isdigit((unsigned char)ts[0]) || !isdigit((unsigned char)ts[1]) || !isdigit((unsigned char)ts[3]) || !isdigit((unsigned char)ts[4])) return bad("time은 HH:MM 형식");
        int hh = stoi(ts.substr(0, 2)), mm = stoi(ts.substr(3, 2));
        if (hh > 23 || mm > 59) return bad("time 범위 오류");
        const string algorithm = q.count("algorithm") ? q["algorithm"] : "dijkstra";
        if (algorithm != "dijkstra" && algorithm != "astar" && algorithm != "ga") return bad("algorithm은 dijkstra, astar, ga 중 하나");
        auto run_route = algorithm == "ga" ? route_ga : algorithm == "astar" ? route_astar : route;
        int day = day_of(ds), t = hh * 3600 + mm * 60;
        Result f = run_route(A.D, from, to, day, t, true), c = run_route(A.D, from, to, day, t, false);
        string body;
        if (!f.found || !c.found) {
            const Result& x = c.found ? f : c;
            body = string("{\"ok\":false,\"algorithm\":") + jstr(algorithm) + ",\"noTrain\":" + (x.no_train ? "true" : "false") + ",\"originLast\":" + to_string(x.origin_last) + ",\"searchLimited\":" + (x.search_limited ? "true" : "false") + "}";
        } else {
            body = "{\"ok\":true,\"algorithm\":" + jstr(algorithm) + ",\"same\":" + string(f.path == c.path ? "true" : "false") + ",\"f\":" + json_result(f) + ",\"c\":" + json_result(c) + "}";
        }
        return response(200, "OK", JSON_T, body, head);
    }
    if (path == "/favicon.ico") return response(204, "No Content", "image/x-icon", "", true);
    return response(404, "Not Found", "text/plain; charset=utf-8", "없는 주소: " + path, head);
}

void send_all(sock_t s, const string& data) {
    size_t off = 0;
    while (off < data.size()) {
        int n = (int)send(s, data.data() + off, (int)min<size_t>(data.size() - off, 1 << 16), MSG_NOSIGNAL);
        if (n <= 0) return;
        off += n;
    }
}

int main(int argc, char** argv) {
    try {
        if (argc < 3) { cerr << "usage: route_server <data폴더> <화면 html 경로> [포트=8080] [all]   예) route_server data route_ui.html\n"; return 1; }
        App A; A.D.load(argv[1]); A.page_path = argv[2];
        A.graph_json = json_graph(A.D);
        // 포트: 인자 > 환경변수 PORT > 8080 (클라우드·Docker는 PORT 환경변수로 포트를 알려 준다)
        const char* env_port = getenv("PORT"); const char* env_all = getenv("BIND_ALL");
        int port = argc >= 4 ? atoi(argv[3]) : (env_port && atoi(env_port) > 0 ? atoi(env_port) : 8080);
        bool all = (argc >= 5 && string(argv[4]) == "all") || (env_all && string(env_all) == "1");   // 컨테이너 안에서는 모든 주소에서 받아야 한다
        { ifstream t(A.page_path, ios::binary); if (!t) { cerr << "화면 html을 열 수 없음: " << A.page_path << "\n"; return 1; } }

#ifdef _WIN32
        WSADATA wsa;
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) { cerr << "WSAStartup 실패\n"; return 1; }
#endif
        sock_t ls = socket(AF_INET, SOCK_STREAM, 0);
        if (ls == BAD_SOCK) { cerr << "소켓을 만들 수 없음\n"; return 1; }
#ifndef _WIN32
        int one = 1; setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, (const char*)&one, sizeof one);   // 윈도우에서는 다른 프로그램이 포트를 가로챌 수 있어 쓰지 않는다
#endif
        sockaddr_in addr; memset(&addr, 0, sizeof addr);
        addr.sin_family = AF_INET; addr.sin_port = htons((unsigned short)port);
        addr.sin_addr.s_addr = htonl(all ? INADDR_ANY : INADDR_LOOPBACK);
        if (bind(ls, (sockaddr*)&addr, sizeof addr) != 0) { cerr << "포트 " << port << "을(를) 쓸 수 없음 (이미 사용 중일 수 있어요. 다른 포트로 실행해 보세요)\n"; return 1; }
        if (listen(ls, 16) != 0) { cerr << "listen 실패\n"; return 1; }
        printf("준비 완료: 역 %d개. 브라우저에서 http://127.0.0.1:%d 을 여세요. (끄기: Ctrl+C)\n", (int)A.D.id.size(), port);
        fflush(stdout);

        struct Client { sock_t s; string buf; chrono::steady_clock::time_point last; };
        vector<Client> cl;
        for (;;) {
            fd_set rf; FD_ZERO(&rf); FD_SET(ls, &rf);
            int nfds = (int)ls + 1;
            for (auto& c : cl) { FD_SET(c.s, &rf); nfds = max(nfds, (int)c.s + 1); }
            timeval tv; tv.tv_sec = 1; tv.tv_usec = 0;
            int r = select(nfds, &rf, nullptr, nullptr, &tv);
            if (r < 0) continue;
            auto now = chrono::steady_clock::now();
            if (r > 0 && FD_ISSET(ls, &rf)) {
                sock_t s = accept(ls, nullptr, nullptr);
                if (s != BAD_SOCK) { if (cl.size() >= 48) close_sock(s); else cl.push_back({s, "", now}); }
            }
            for (size_t i = 0; i < cl.size();) {
                Client& c = cl[i]; bool drop = false;
                if (r > 0 && FD_ISSET(c.s, &rf)) {
                    char buf[4096];
                    int n = (int)recv(c.s, buf, sizeof buf, 0);
                    if (n <= 0) drop = true;
                    else {
                        c.buf.append(buf, n); c.last = now;
                        if (c.buf.find("\r\n\r\n") != string::npos) {
                            auto t0 = chrono::steady_clock::now();
                            string log, out = handle(A, c.buf, log);
                            send_all(c.s, out);
                            double ms = chrono::duration<double, milli>(chrono::steady_clock::now() - t0).count();
                            printf("%s  (%.1f ms)\n", log.c_str(), ms); fflush(stdout);
                            shut_send(c.s); drop = true;
                        } else if (c.buf.size() > 16384) drop = true;
                    }
                }
                if (!drop && chrono::duration_cast<chrono::seconds>(now - c.last).count() > 10) drop = true;   // 말없이 열어 둔 연결은 정리
                if (drop) { close_sock(c.s); cl.erase(cl.begin() + i); } else i++;
            }
        }
    } catch (const exception& e) { cerr << "오류: " << e.what() << "\n"; return 1; }
}
