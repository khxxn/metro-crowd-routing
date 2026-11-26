// 가상 노선망을 CSV 4개로 만든다 (형식은 data/와 같아서 route_server로 그대로 읽을 수 있다)
// 빌드(윈도우): g++ -O2 -std=c++17 tools/gen_synthetic.cpp -o gen_synthetic -lws2_32
// 실행:        ./gen_synthetic data data_syn/k10 10        (실제 데이터 폴더, 출력 폴더, k [m=3] [seed=1])
#define main route_server_main
#include "../route_server.cpp"
#undef main
#include "synthetic.h"

int main(int argc, char** argv) {
    if (argc < 4) { fprintf(stderr, "usage: gen_synthetic <real_data_dir> <out_dir> <k> [m=3] [seed=1]\n"); return 1; }
    Data R; R.load(argv[1]);
    SynConfig cfg;
    cfg.k = atoi(argv[3]);
    if (argc > 4) cfg.m = atoi(argv[4]);
    if (argc > 5) cfg.seed = (unsigned)atoi(argv[5]);
    if (cfg.k < 2 || cfg.m < 0) { fprintf(stderr, "k >= 2, m >= 0\n"); return 1; }
    Data D = make_synthetic(R, cfg);
    write_synthetic(D, argv[2]);

    // 요약: 크기, 환승역 비율, 혼잡도가 실제처럼 나왔는지
    size_t rides = 0, trs = 0;
    for (auto& v : D.ride) rides += v.size();
    for (auto& v : D.tr) trs += v.size();
    size_t transfer_st = 0;
    for (auto& [name, nodes] : D.by_name) if (nodes.size() > 1) transfer_st++;
    auto avg = [](const vector<array<array<float, NSLOT>, 3>>& c, int slot) {
        double s = 0; for (auto& e : c) s += e[WEEKDAY][slot]; return c.empty() ? 0 : s / c.size();
    };
    printf("k=%d m=%d seed=%u -> %s\n", cfg.k, cfg.m, cfg.seed, argv[2]);
    printf("  역 %zu개 (환승역 %zu개, %.1f%%), 노드 %zu개, 탑승 간선 %zu개, 환승 간선 %zu개\n",
           D.by_name.size(), transfer_st, 100.0 * transfer_st / D.by_name.size(), D.id.size(), rides, trs);
    printf("  평일 평균 혼잡도(%%)   05:30 %.1f | 08:00 %.1f | 12:00 %.1f | 18:00 %.1f | 23:00 %.1f\n",
           avg(D.cong, 0), avg(D.cong, slot_of(8 * 3600)), avg(D.cong, slot_of(12 * 3600)), avg(D.cong, slot_of(18 * 3600)), avg(D.cong, slot_of(23 * 3600)));
    printf("  실제 서울 데이터(참고) 05:30 %.1f | 08:00 %.1f | 12:00 %.1f | 18:00 %.1f | 23:00 %.1f\n",
           avg(R.cong, 0), avg(R.cong, slot_of(8 * 3600)), avg(R.cong, slot_of(12 * 3600)), avg(R.cong, slot_of(18 * 3600)), avg(R.cong, slot_of(23 * 3600)));
    return 0;
}
