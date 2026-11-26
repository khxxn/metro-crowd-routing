// 가상 지하철 노선망 생성기 (n을 키우는 실험용)
// route_server.cpp 뒤에 include해서 쓴다. 실제 서울 데이터(Data)를 받아 같은 모양의 Data를 만든다.
//
// 모양: 가로 노선 k개 × 세로 노선 k개 격자. 노선이 만나는 점이 환승역이고, 환승역 사이에 일반역 m개를 둔다.
//   환승역 = k², 전체 역 = k² + 2k(k-1)m, 노드(노선별 역) = 2k(k + (k-1)m)
//   m = 3이면 환승역 비율이 서울(약 13%)과 비슷한 13~16%가 된다.
// 값은 모두 실제 데이터에서 가져온다.
//   탑승 시간: 실제 탑승 간선의 seconds 중에서 무작위로 고른다(같은 구간의 양방향은 같은 값).
//   혼잡도: 실제 탑승 간선 하나의 "요일 3 × 시간대 39" 값을 통째로 복사한다(시간대별 모양 유지).
//           실제 간선을 평일 평균 혼잡도 순으로 줄 세우고, 격자 중심에 가까운 간선일수록 붐비는 쪽에서 고른다.
//   환승: 실제 환승 간선 하나의 걷는 시간·시간대별 대기 시간을 통째로 복사하고, 비용은 2.204 × (걷기 + 대기).
//   첫차·막차: 막히지 않게 둔다(첫차 0, 막차 아주 큰 값). 규모 실험에서 막차 영향을 빼기 위해서다.
//   큰 격자에서는 끝에서 끝까지 몇 시간~하루 넘게 걸리는데, 25:00 이후 시간대는 마지막 시간대 혼잡도를 그대로 쓴다.
#pragma once
#include <filesystem>
#include <random>

struct SynConfig {
    int k = 10;            // 가로·세로 노선 수
    int m = 3;             // 환승역 사이 일반역 수
    unsigned seed = 1;     // 같은 시드면 같은 노선망
    double noise = 0.15;   // 혼잡 위치 배정의 무작위성 (0이면 중심일수록 정확히 더 붐빔)
};

Data make_synthetic(const Data& R, const SynConfig& cfg) {
    mt19937 rng(cfg.seed);
    const int k = cfg.k, m = cfg.m, step = m + 1, len = (k - 1) * step + 1;   // len = 노선 하나의 역 수

    // 실제 데이터에서 가져올 값들
    vector<int> secs;
    for (const auto& es : R.ride) for (const auto& e : es) secs.push_back(e.sec);
    vector<int> prof(R.cong.size());   // 실제 탑승 간선 번호, 평일 평균 혼잡도 오름차순
    vector<double> mean(R.cong.size(), 0);
    for (size_t e = 0; e < R.cong.size(); e++) {
        prof[e] = (int)e;
        for (int s = 0; s < NSLOT; s++) mean[e] += R.cong[e][WEEKDAY][s] / NSLOT;
    }
    sort(prof.begin(), prof.end(), [&](int a, int b) { return mean[a] < mean[b]; });
    uniform_int_distribution<size_t> pick_sec(0, secs.size() - 1), pick_tr(0, R.trv.size() - 1);
    normal_distribution<double> jitter(0, cfg.noise);

    Data D;
    auto add_node = [&](const string& name, const string& line, int x, int y) {
        int v = (int)D.id.size();
        char buf[32];
        snprintf(buf, sizeof buf, "N%07d", v);
        D.id.push_back(buf); D.name.push_back(name); D.line.push_back(line);
        snprintf(buf, sizeof buf, "%.5f", 37.40 + y * 0.002); D.lat.push_back(buf);
        snprintf(buf, sizeof buf, "%.5f", 126.80 + x * 0.002); D.lon.push_back(buf);
        D.idx[D.id.back()] = v;
        D.by_name[name].push_back(v);
        D.ride.emplace_back(); D.tr.emplace_back();
        return v;
    };
    auto tname = [](int i, int j) { return "T" + to_string(i) + "_" + to_string(j); };

    // 노드: 가로 노선 i는 y = i * step, 세로 노선 j는 x = j * step. 만나는 점 (i, j)가 환승역 T{i}_{j}
    vector<vector<int>> H(k), V(k);
    for (int i = 0; i < k; i++)
        for (int p = 0; p < len; p++)
            H[i].push_back(add_node(p % step ? "H" + to_string(i) + "-" + to_string(p) : tname(i, p / step), "H" + to_string(i), p, i * step));
    for (int j = 0; j < k; j++)
        for (int p = 0; p < len; p++)
            V[j].push_back(add_node(p % step ? "V" + to_string(j) + "-" + to_string(p) : tname(p / step, j), "V" + to_string(j), j * step, p));

    // 탑승 간선: 노선을 따라 이웃한 역끼리 양방향
    const double c = (len - 1) / 2.0, rmax = c * sqrt(2.0);
    auto add_ride = [&](int a, int b, double mx, double my, int sec) {
        double r = rmax > 0 ? hypot(mx - c, my - c) / rmax : 0;   // 0 = 중심, 1 = 모서리
        double q = min(1.0, max(0.0, 1 - r + jitter(rng)));         // 1에 가까울수록 붐비는 실제 간선을 고른다
        int src = prof[(size_t)llround(q * (prof.size() - 1))];
        Data::Ride rd{b, sec, (int)D.cong.size(), {}, {}};
        for (int d = 0; d < 3; d++) { rd.first[d] = 0; rd.last[d] = 1000000000; }
        D.ride[a].push_back(rd);
        D.cong.push_back(R.cong[src]);
    };
    for (int i = 0; i < k; i++)
        for (int p = 0; p + 1 < len; p++) {
            int sec = secs[pick_sec(rng)];
            add_ride(H[i][p], H[i][p + 1], p + 0.5, i * step, sec);
            add_ride(H[i][p + 1], H[i][p], p + 0.5, i * step, sec);
        }
    for (int j = 0; j < k; j++)
        for (int p = 0; p + 1 < len; p++) {
            int sec = secs[pick_sec(rng)];
            add_ride(V[j][p], V[j][p + 1], j * step, p + 0.5, sec);
            add_ride(V[j][p + 1], V[j][p], j * step, p + 0.5, sec);
        }

    // 환승 간선: 환승역마다 가로 노선 노드 ↔ 세로 노선 노드, 방향마다 실제 환승 간선 하나를 복사
    auto add_tr = [&](int a, int b) {
        auto vals = R.trv[pick_tr(rng)];
        for (auto& day : vals) {
            // 실제 데이터의 심야 NO_SERVICE 칸은 그 앞 시간대의 대기 시간으로 채운다(막차 영향을 빼는 것과 같은 이유)
            double last_wait = 0; int walk = 0;
            for (auto& v : day) if (v.ok) { last_wait = v.wait; walk = v.walk; }
            for (auto& v : day) {
                if (v.ok) last_wait = v.wait;
                else { v.ok = true; v.wait = last_wait; v.walk = v.walk ? v.walk : walk; }
                v.cost = 2.204 * (v.walk + v.wait);
            }
        }
        D.tr[a].push_back({b, (int)D.trv.size()});
        D.trv.push_back(vals);
    };
    for (int i = 0; i < k; i++)
        for (int j = 0; j < k; j++) {
            int a = H[i][j * step], b = V[j][i * step];
            add_tr(a, b); add_tr(b, a);
        }
    D.prepare_geo();         // A* 직선거리 h용 좌표와 최고 속도
    D.prepare_landmarks();   // A* 랜드마크 h용 하한 표
    return D;
}

// route_server가 읽는 CSV 4개와 같은 형식으로 저장한다(작은 k에서 눈으로 확인하거나 서버에 띄워 볼 때)
void write_synthetic(const Data& D, const string& dir) {
    filesystem::create_directories(dir);
    const char* dn[3] = {"weekday", "sat", "sun"};
    auto slot_start = [](int s) { char b[8]; int t = FIRST_SEC + s * SLOT_SEC; snprintf(b, sizeof b, "%02d:%02d", t / 3600, t % 3600 / 60); return string(b); };
    FILE* f = fopen((dir + "/station_nodes.csv").c_str(), "w");
    fprintf(f, "id,station_name,line_key,lat,lon\n");
    for (size_t v = 0; v < D.id.size(); v++)
        fprintf(f, "%s,%s,%s,%s,%s\n", D.id[v].c_str(), D.name[v].c_str(), D.line[v].c_str(), D.lat[v].c_str(), D.lon[v].c_str());
    fclose(f);
    FILE* fe = fopen((dir + "/station_edges.csv").c_str(), "w");
    FILE* fc = fopen((dir + "/ride_congestion_filled.csv").c_str(), "w");
    fprintf(fe, "from_node_id,to_node_id,seconds,edge_type,source,first_dep_weekday_sec,last_dep_weekday_sec,first_dep_sat_sec,last_dep_sat_sec,first_dep_sun_sec,last_dep_sun_sec\n");
    fprintf(fc, "from_node_id,to_node_id,day_type,slot_idx,slot_start,congestion_pct\n");
    for (size_t u = 0; u < D.ride.size(); u++)
        for (const auto& e : D.ride[u]) {
            const char *a = D.id[u].c_str(), *b = D.id[e.to].c_str();
            fprintf(fe, "%s,%s,%d,RIDE,SYNTHETIC,%d,%d,%d,%d,%d,%d\n", a, b, e.sec, e.first[0], e.last[0], e.first[1], e.last[1], e.first[2], e.last[2]);
            for (int d = 0; d < 3; d++)
                for (int s = 0; s < NSLOT; s++)
                    fprintf(fc, "%s,%s,%s,%d,%s,%.2f\n", a, b, dn[d], s, slot_start(s).c_str(), D.cong[e.e][d][s]);
        }
    fclose(fe); fclose(fc);
    FILE* ft = fopen((dir + "/transfer_edges_cost.csv").c_str(), "w");
    fprintf(ft, "from_node_id,to_node_id,station,from_line,to_line,day_type,slot_idx,slot_start,walk_sec,wait_sec,cost_sec,status\n");
    for (size_t u = 0; u < D.tr.size(); u++)
        for (const auto& e : D.tr[u])
            for (int d = 0; d < 3; d++)
                for (int s = 0; s < NSLOT; s++) {
                    const TrVal& v = D.trv[e.e][d][s];
                    if (v.ok)
                        fprintf(ft, "%s,%s,%s,%s,%s,%s,%d,%s,%d,%.1f,%.4f,OK\n", D.id[u].c_str(), D.id[e.to].c_str(), D.name[u].c_str(), D.line[u].c_str(),
                                D.line[e.to].c_str(), dn[d], s, slot_start(s).c_str(), v.walk, v.wait, v.cost);
                    else
                        fprintf(ft, "%s,%s,%s,%s,%s,%s,%d,%s,%d,,,NO_SERVICE\n", D.id[u].c_str(), D.id[e.to].c_str(), D.name[u].c_str(), D.line[u].c_str(),
                                D.line[e.to].c_str(), dn[d], s, slot_start(s).c_str(), v.walk);
                }
    fclose(ft);
}
