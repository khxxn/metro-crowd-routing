// route_server.cpp : 지하철 혼잡도 경로 서버 (외부 라이브러리 없음, 윈도우/리눅스/맥 겸용)
//   - 경로 계산은 방식 B(간선에 들어서는 시각으로 시간대를 다시 찾음), 막차·첫차 검사 포함. 다익스트라·A*·GA 세 가지
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
//   GET /api/compare?from=..&to=..&day=..&time=.. → 세 알고리즘의 혼잡 반영 경로·점수·실행 시간 비교표
#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cmath>
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
    // A*용: 노드 좌표(미터)와 모든 간선 중 가장 빠른 이동 속도(미터/초). load() 끝에서 prepare_geo()로 채운다
    vector<double> px, py;
    double vmax = 0;
    // A* 랜드마크 h용: lm_to[종류][랜드마크][노드] = 노드 → 랜드마크 최소 비용, lm_from = 랜드마크 → 노드.
    // 종류 0 = 점수 하한, 1 = 실제 시간 하한. load() 끝에서 prepare_landmarks()로 한 번 채운다
    vector<int> landmarks;
    vector<vector<double>> lm_to[2], lm_from[2];

    void prepare_geo();
    void prepare_landmarks(int count = 8);
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
        prepare_geo();
        prepare_landmarks();
    }
};

// 위도·경도를 평면 좌표(미터)로 바꾸고, 간선마다 (직선거리 ÷ 걸리는 시간)의 최댓값 vmax를 구한다.
// 탑승은 seconds, 환승은 그 간선의 (걷기 + 대기) 중 가장 짧은 값으로 나눈다. 그래서 어떤 간선이든
// "걸리는 시간 ≥ 직선거리 ÷ vmax"가 성립하고, 경로 전체로는 "남은 시간 ≥ 도착역까지 직선거리 ÷ vmax"가 된다.
void Data::prepare_geo() {
    size_t n = id.size();
    px.assign(n, 0); py.assign(n, 0);
    double lat0 = 0;
    for (size_t v = 0; v < n; v++) lat0 += atof(lat[v].c_str()) / max<size_t>(n, 1);
    const double PI = 3.14159265358979323846, cosl = cos(lat0 * PI / 180);
    for (size_t v = 0; v < n; v++) {
        px[v] = atof(lon[v].c_str()) * 111320.0 * cosl;
        py[v] = atof(lat[v].c_str()) * 110540.0;
    }
    auto dist = [&](int a, int b) { return hypot(px[a] - px[b], py[a] - py[b]); };
    vmax = 1e-9;
    for (size_t u = 0; u < n; u++) {
        for (const auto& e : ride[u]) if (e.sec > 0) vmax = max(vmax, dist((int)u, e.to) / e.sec);
        for (const auto& e : tr[u]) {
            double fastest = 1e18;
            for (const auto& day : trv[e.e]) for (const auto& v : day) if (v.ok) fastest = min(fastest, v.walk + v.wait);
            if (fastest < 1e17 && fastest > 0) vmax = max(vmax, dist((int)u, e.to) / fastest);
        }
    }
}

// 랜드마크 하한 표. 간선마다 "어느 요일·시간대에도 이보다 싸게는 못 간다"는 하한 비용을 둔다.
//   탑승: seconds (혼잡 배율 ≥ 1이라 점수도 시간도 seconds 이상)
//   환승: 점수는 그 간선의 가장 작은 cost_sec, 시간은 가장 작은 (걷기 + 대기)
// 이 하한 그래프에서 랜드마크마다 앞·뒤 방향 다익스트라를 한 번씩 돌려 표로 저장한다(질의와 무관, 한 번만).
// 랜드마크는 서로 멀리 떨어진 역을 차례로 고른다(앞서 고른 랜드마크들에서 가장 먼 역).
void Data::prepare_landmarks(int count) {
    int n = (int)id.size();
    for (int t = 0; t < 2; t++) { lm_to[t].clear(); lm_from[t].clear(); }
    landmarks.clear();
    if (n == 0) return;
    using P = pair<double, int>;
    vector<vector<P>> fw[2], bw[2];   // 하한 그래프 (종류별, 정방향·역방향)
    for (int t = 0; t < 2; t++) { fw[t].assign(n, {}); bw[t].assign(n, {}); }
    for (int u = 0; u < n; u++) {
        for (const auto& e : ride[u])
            for (int t = 0; t < 2; t++) { fw[t][u].push_back({(double)e.sec, e.to}); bw[t][e.to].push_back({(double)e.sec, u}); }
        for (const auto& e : tr[u]) {
            double lo[2] = {1e18, 1e18};
            for (const auto& day : trv[e.e])
                for (const auto& v : day)
                    if (v.ok) { lo[0] = min(lo[0], v.cost); lo[1] = min(lo[1], v.walk + v.wait); }
            for (int t = 0; t < 2; t++)
                if (lo[t] < 1e17) { fw[t][u].push_back({lo[t], e.to}); bw[t][e.to].push_back({lo[t], u}); }
        }
    }
    auto dijkstra = [&](const vector<vector<P>>& g, int src) {
        vector<double> d(n, 1e18);
        priority_queue<P, vector<P>, greater<P>> pq;
        d[src] = 0; pq.push({0, src});
        while (!pq.empty()) {
            auto [du, u] = pq.top(); pq.pop();
            if (du > d[u]) continue;
            for (const auto& [w, v] : g[u]) if (du + w < d[v]) { d[v] = du + w; pq.push({d[v], v}); }
        }
        return d;
    };
    vector<double> nearest(n, 1e18);   // 이미 고른 랜드마크들까지의 최소 거리(실제 시간 하한 기준)
    int next = 0;
    for (int k = 0; k < min(count, n); k++) {
        if (k > 0) {   // 고른 랜드마크들에서 가장 먼(닿을 수 있는) 역
            double farthest = -1;
            for (int v = 0; v < n; v++) if (nearest[v] < 1e17 && nearest[v] > farthest) { farthest = nearest[v]; next = v; }
        } else {        // 첫 랜드마크: 0번 역에서 가장 먼 역
            vector<double> d0 = dijkstra(fw[1], 0);
            double farthest = -1;
            for (int v = 0; v < n; v++) if (d0[v] < 1e17 && d0[v] > farthest) { farthest = d0[v]; next = v; }
        }
        landmarks.push_back(next);
        for (int t = 0; t < 2; t++) {
            lm_from[t].push_back(dijkstra(fw[t], next));   // 랜드마크 → 노드
            lm_to[t].push_back(dijkstra(bw[t], next));     // 노드 → 랜드마크
        }
        for (int v = 0; v < n; v++) nearest[v] = min(nearest[v], min(lm_from[1].back()[v], lm_to[1].back()[v]));
    }
}

inline double ride_cost(int seconds, double c) { return seconds * (1.0 + ALPHA * max(c - C0, 0.0)); }

// ---------- 공통 결과 형식 ----------
struct Step { int type; int from, to; double real, score, cong; };   // type 1 = 탑승, 0 = 환승
struct Result {
    bool found = false;
    bool no_train = false;
    bool search_limited = false;  // GA가 탐색 예산 안에서 유효 경로를 찾지 못한 경우
    int origin_last = -1;
    double score = 0, real = 0;
    vector<int> path;
    vector<Step> steps;
    long expanded = 0;    // 우선순위 큐에서 꺼내 펼친 상태 수. 탐색량 비교용이며 GA는 세지 않는다
};

// ---------- 공통: 탐색 기록과 이동 규칙 (방식 B) ----------
// 다익스트라와 A*가 같이 쓰는 것은 "지하철 규칙"(어디로 갈 수 있고 비용이 얼마인가)과 기록·경로 복원뿐이다.
// 탐색 순서(우선순위 큐에 무엇을 넣나)는 route_dijkstra와 route_astar에 각각 따로 있다.
//
// 상태 = 역 노드 × 2 + (열차를 탄 채로 왔으면 1). 같은 노드라도 환승해서 온 경우는 다음 열차의 막차·첫차를
// 확인하고, 계속 타고 온 경우는 확인하지 않아서 갈 수 있는 곳이 다르다. 노드마다 기록을 하나만 두면
// 심야에 "환승해서 온 더 빠른 기록"이 막차에 막혀 경로가 크게 돌아가는 문제가 있어 둘로 나눴다.

// 상태마다 지금까지의 최소 가중치(key)와 경로 복원용 기록
struct Labels {
    vector<double> key, real, sco, s_real, s_score, s_cong;
    vector<int> prev;
    vector<char> s_type, done;
    explicit Labels(int S)
        : key(S, 1e18), real(S, 0), sco(S, 0), s_real(S, 0), s_score(S, 0), s_cong(S, 0), prev(S, -1), s_type(S, 0), done(S, 0) {}
};

// 이동 한 번(탑승 한 구간 또는 환승 한 번). to = 도착 상태, type 1 = 탑승, 0 = 환승
struct Move { int to; double real, score, cong; int type; };

// 상태 st에서 할 수 있는 이동을 하나씩 f(Move)로 넘긴다. 시간대는 이 상태에 도착한 시각으로 다시 찾는다(방식 B).
// 새로 타는 열차는 첫차·막차를, 환승은 그 시간대 운행 여부(NO_SERVICE)를 확인하고, 막힌 이동은 no_train에 남긴다.
template <class F>
void for_each_move(const Data& D, const Labels& L, int st, int day, int depart_sec, bool& no_train, int& origin_last, F f) {
    int u = st / 2; bool aboard = st & 1;
    int now = depart_sec + (int)L.real[st];   // 이 노드에 도착한(타려는) 시각
    int slot = slot_of(now);
    for (const auto& e : D.ride[u]) {
        if (!aboard) {   // 새로 타는 열차만 첫차·막차를 확인한다
            int t = clock_of(now);
            if (t < e.first[day] || t > e.last[day]) {
                no_train = true;
                if (L.prev[st] < 0 && t > e.last[day]) origin_last = max(origin_last, e.last[day]);
                continue;
            }
        }
        double c = D.cong[e.e][day][slot];
        f(Move{2 * e.to + 1, (double)e.sec, ride_cost(e.sec, c), c, 1});
    }
    for (const auto& e : D.tr[u]) {
        const TrVal& v = D.trv[e.e][day][slot];
        if (!v.ok) { no_train = true; continue; }   // NO_SERVICE: 그 시간대에는 갈아탈 수 없다
        f(Move{2 * e.to, v.walk + v.wait, v.cost, 0, 0});
    }
}

// st를 거쳐 m.to로 가는 길(가중치 ng)이 지금 기록보다 싸면 기록을 바꾸고 true
bool improve(Labels& L, int st, const Move& m, double ng) {
    int v = m.to;
    if (L.done[v] || ng >= L.key[v]) return false;
    L.key[v] = ng; L.real[v] = L.real[st] + m.real; L.sco[v] = L.sco[st] + m.score; L.prev[v] = st;
    L.s_type[v] = (char)m.type; L.s_real[v] = m.real; L.s_score[v] = m.score; L.s_cong[v] = m.cong;
    return true;
}

// 도착 상태 goal에서 prev를 거꾸로 따라가 경로를 만든다
Result trace(const Labels& L, int goal, long expanded) {
    Result r; r.found = true; r.score = L.sco[goal]; r.real = L.real[goal]; r.expanded = expanded;
    vector<int> chain;
    for (int x = goal; x != -1; x = L.prev[x]) chain.push_back(x);
    reverse(chain.begin(), chain.end());
    for (size_t i = 0; i < chain.size(); i++) {
        int x = chain[i];
        r.path.push_back(x / 2);
        if (i) r.steps.push_back({L.s_type[x], chain[i - 1] / 2, x / 2, L.s_real[x], L.s_score[x], L.s_cong[x]});
    }
    return r;
}

Result not_found(bool no_train, int origin_last, long expanded) {
    Result r; r.no_train = no_train; r.origin_last = origin_last; r.expanded = expanded;
    return r;
}

Result route_dijkstra(const Data& D, const string& from, const string& to, int day, int depart_sec, bool by_real);

// 방식 B에서 상태마다 기록을 하나만 두기 때문에, 아주 드물게(심야의 긴 이동) 점수가 낮은 기록이 막차에 막혀
// 점수 최소 탐색이 경로를 못 찾는 경우가 있다. 그때는 실제로 갈 수 있는 최단 시간 경로를 대신 돌려준다.
Result with_fallback(Result r, const Data& D, const string& from, const string& to, int day, int depart_sec) {
    if (r.found) return r;
    Result fast = route_dijkstra(D, from, to, day, depart_sec, true);
    if (!fast.found) return r;
    fast.expanded += r.expanded;
    return fast;
}

// ---------- 다익스트라 (main 브랜치, khxxn) ----------
// 우선순위 = 출발부터 지금까지의 가중치 g. 가장 작은 상태부터 확정하고, 도착역 상태를 꺼내는 순간 끝낸다.
// by_real = true: 실제 소요시간 최소(일반 최단시간), false: 점수(혼잡 반영 비용) 최소.
Result route_dijkstra(const Data& D, const string& from, const string& to, int day, int depart_sec, bool by_real) {
    double ws = by_real ? 0 : 1, wr = by_real ? 1 : 0;   // 간선 가중치 = ws × 점수 + wr × 실제 시간
    int n = (int)D.id.size();
    Labels L(2 * n);
    vector<char> is_dst(n, 0);
    for (int d : D.by_name.at(to)) is_dst[d] = 1;
    bool no_train = false; int origin_last = -1; long expanded = 0; int goal = -1;

    using Q = pair<double, int>;   // {g, 상태}
    priority_queue<Q, vector<Q>, greater<Q>> pq;
    for (int s : D.by_name.at(from)) { L.key[2 * s] = 0; pq.push({0, 2 * s}); }
    while (!pq.empty()) {
        int st = pq.top().second; pq.pop();
        if (L.done[st]) continue;
        L.done[st] = 1;
        if (is_dst[st / 2]) { goal = st; break; }
        expanded++;
        for_each_move(D, L, st, day, depart_sec, no_train, origin_last, [&](const Move& m) {
            double ng = L.key[st] + ws * m.score + wr * m.real;
            if (improve(L, st, m, ng)) pq.push({ng, m.to});              // 우선순위 = g
        });
    }
    Result r = goal >= 0 ? trace(L, goal, expanded) : not_found(no_train, origin_last, expanded);
    return by_real ? r : with_fallback(r, D, from, to, day, depart_sec);
}

// ---------- A* (Susu 브랜치, 휴리스틱을 바꿈) ----------
// 다익스트라와 탐색 모양은 같고, 우선순위가 g + h라는 것만 다르다. h(u) = u에서 도착역까지 남은 가중치의 하한.
// 원래(Susu)는 질의마다 도착역에서 거꾸로 다익스트라를 그래프 전체에 돌려 h를 구했다. h는 정확했지만 그 준비 비용이
// 본 탐색보다 커서 A*가 다익스트라보다 느렸다. 지금은 질의마다 드는 준비 없이 두 가지 하한 중 큰 값을 h로 쓴다.
//   1) 랜드마크(ALT): 랜드마크 L마다 삼각부등식으로  d(u,t) ≥ d(u,L) − d(t,L),  d(u,t) ≥ d(L,t) − d(L,u)
//      (d는 하한 그래프의 최소 비용, Data::prepare_landmarks에서 한 번 계산)
//   2) 직선거리: d(u,t) ≥ 직선거리 ÷ vmax   (Data::prepare_geo)
// 실제 간선 비용 ≥ 하한 그래프의 간선 비용이므로 두 h 모두 남은 비용을 넘지 않고(admissible) 간선마다
// h(u) ≤ w(u→v) + h(v) 가 성립한다(consistent). 둘 중 큰 값도 같은 성질을 가져서 다익스트라와 같은 답을 낸다.
// 도착역 노드가 여러 개(환승역)면 노드마다 하한을 구해 가장 작은 값을 쓴다.
struct LowerBound {
    const Data& D;
    int t;                 // 하한 표 종류: 0 = 점수, 1 = 실제 시간
    bool has_geo;          // 좌표 없이 직접 만든 그래프(테스트)면 직선거리 하한은 0
    vector<int> dst;
    vector<vector<pair<double, double>>> dl;   // 도착 노드마다 (d(t,L), d(L,t))를 미리 꺼내 둔다
    LowerBound(const Data& D_, const string& to, bool by_real) : D(D_), t(by_real ? 1 : 0), dst(D_.by_name.at(to)) {
        has_geo = D.px.size() == D.id.size() && D.vmax > 0;
        int nl = (int)D.landmarks.size();
        dl.assign(dst.size(), vector<pair<double, double>>(nl));
        for (size_t i = 0; i < dst.size(); i++)
            for (int l = 0; l < nl; l++) dl[i][l] = {D.lm_to[t][l][dst[i]], D.lm_from[t][l][dst[i]]};
    }
    double operator()(int u) const {
        double best = 1e18;
        for (size_t i = 0; i < dst.size(); i++) {
            double lb = has_geo ? hypot(D.px[u] - D.px[dst[i]], D.py[u] - D.py[dst[i]]) / D.vmax : 0;
            for (size_t l = 0; l < dl[i].size(); l++) {
                double ul = D.lm_to[t][l][u], lu = D.lm_from[t][l][u];
                auto [tl, lt] = dl[i][l];
                if (ul < 1e17 && tl < 1e17) lb = max(lb, ul - tl);   // d(u,L) − d(t,L)
                if (lt < 1e17 && lu < 1e17) lb = max(lb, lt - lu);   // d(L,t) − d(L,u)
                if (ul >= 1e17 && tl < 1e17) lb = 1e18;              // u에서 L로 못 가는데 t에서는 간다 → u에서 t로도 못 간다
            }
            best = min(best, lb);
        }
        return best;
    }
};

Result route_astar(const Data& D, const string& from, const string& to, int day, int depart_sec, bool by_real) {
    double ws = by_real ? 0 : 1, wr = by_real ? 1 : 0;   // 간선 가중치 = ws × 점수 + wr × 실제 시간
    LowerBound h(D, to, by_real);
    int n = (int)D.id.size();
    Labels L(2 * n);
    vector<char> is_dst(n, 0);
    for (int d : D.by_name.at(to)) is_dst[d] = 1;
    bool no_train = false; int origin_last = -1; long expanded = 0; int goal = -1;

    using Q = pair<double, int>;   // {g + h, 상태}
    priority_queue<Q, vector<Q>, greater<Q>> pq;
    for (int s : D.by_name.at(from)) {
        double hs = h(s);
        if (hs >= 1e17) continue;   // 도착역에 닿을 수 없는 노드
        L.key[2 * s] = 0; pq.push({hs, 2 * s});
    }
    while (!pq.empty()) {
        int st = pq.top().second; pq.pop();
        if (L.done[st]) continue;
        L.done[st] = 1;
        if (is_dst[st / 2]) { goal = st; break; }
        expanded++;
        for_each_move(D, L, st, day, depart_sec, no_train, origin_last, [&](const Move& m) {
            if (L.done[m.to]) return;
            double hv = h(m.to / 2);
            if (hv >= 1e17) return;   // 도착역에 닿을 수 없는 노드
            double ng = L.key[st] + ws * m.score + wr * m.real;
            if (improve(L, st, m, ng)) pq.push({ng + hv, m.to});         // 우선순위 = g + h
        });
    }
    Result r = goal >= 0 ? trace(L, goal, expanded) : not_found(no_train, origin_last, expanded);
    return by_real ? r : with_fallback(r, D, from, to, day, depart_sec);
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

// 화면과 /api/compare에서 고를 수 있는 알고리즘. 모두 (최단 시간 경로, 혼잡 반영 경로)를 같은 함수 모양으로 낸다
struct Algo { const char* key; Result (*fn)(const Data&, const string&, const string&, int, int, bool); };
const Algo ALGOS[] = {
    {"dijkstra", route_dijkstra},     // main 브랜치(khxxn): 다익스트라
    {"astar", route_astar},           // Susu 브랜치: A* (휴리스틱 = 랜드마크·직선거리 하한)
    {"ga", route_ga},                 // Susu 브랜치: 유전 알고리즘
};

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
    if (path == "/api/route" || path == "/api/compare") {
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
        int day = day_of(ds), t = hh * 3600 + mm * 60;

        if (path == "/api/compare") {   // 세 알고리즘으로 혼잡 반영 경로를 구해 한 표로 비교한다
            string rows;
            for (const auto& a : ALGOS) {
                // 실행 시간은 혼잡 반영 경로를 한 번 구하는 데 걸린 시간. 3번 재서 가운데 값을 쓴다
                double ms[3]; Result r;
                for (int k = 0; k < 3; k++) {
                    auto t0 = chrono::steady_clock::now();
                    r = a.fn(A.D, from, to, day, t, false);
                    ms[k] = chrono::duration<double, milli>(chrono::steady_clock::now() - t0).count();
                }
                sort(ms, ms + 3);
                rows += string(rows.empty() ? "" : ",") + "{\"algorithm\":" + jstr(a.key) + ",\"found\":" + (r.found ? "true" : "false") +
                        ",\"score\":" + jnum(r.score) + ",\"real\":" + jnum(r.real) + ",\"ms\":" + jnum(ms[1]) +
                        ",\"expanded\":" + to_string(r.expanded) + ",\"path\":[";
                for (size_t i = 0; i < r.path.size(); i++) rows += (i ? "," : "") + to_string(r.path[i]);
                rows += "]}";
            }
            return response(200, "OK", JSON_T, "{\"ok\":true,\"rows\":[" + rows + "]}", head);
        }

        const string algorithm = q.count("algorithm") ? q["algorithm"] : "dijkstra";
        const Algo* algo = nullptr;
        for (const auto& a : ALGOS) if (algorithm == a.key) algo = &a;
        if (!algo) return bad("algorithm은 dijkstra, astar, ga 중 하나");
        Result f = algo->fn(A.D, from, to, day, t, true), c = algo->fn(A.D, from, to, day, t, false);
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
