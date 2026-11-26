// Windows: g++ -O2 -std=c++17 tests/route_ga_test.cpp -o route_ga_test.exe -lws2_32
// Run from the project root: ./route_ga_test.exe data
#define main route_server_main
#include "../route_server.cpp"
#undef main
#include <cmath>
#include <stdexcept>

static int checks = 0;
static void check(bool ok, const char* message) {
    ++checks;
    if (!ok) throw runtime_error(message);
}
static bool close_enough(double a, double b) {
    return abs(a - b) <= 1e-8 * max(1.0, max(abs(a), abs(b)));
}
static Data graph(int n) {
    Data d; d.ride.resize(n); d.tr.resize(n);
    for (int i = 0; i < n; i++) {
        string name = "N" + to_string(i);
        d.id.push_back(name); d.name.push_back(name); d.by_name[name].push_back(i);
    }
    return d;
}
static int ride(Data& d, int u, int v, int seconds, float congestion = 0) {
    int edge = (int)d.cong.size(); d.cong.emplace_back();
    for (auto& day : d.cong.back()) day.fill(congestion);
    d.ride[u].push_back({v, seconds, edge, {0, 0, 0}, {200000, 200000, 200000}});
    return edge;
}
static int transfer(Data& d, int u, int v, int walk, double wait, double cost, bool ok = true) {
    int edge = (int)d.trv.size(); d.trv.emplace_back();
    for (auto& day : d.trv.back()) day.fill({walk, wait, cost, ok});
    d.tr[u].push_back({v, edge});
    return edge;
}

// Recompute every returned step from the input data and its actual departure time.
static void validate(const Data& d, const Result& r, const string& from, const string& to, int day, int depart) {
    check(r.found, "expected a feasible GA route");
    check(!r.path.empty() && r.steps.size() + 1 == r.path.size(), "path/step size mismatch");
    const auto& origins = d.by_name.at(from); const auto& goals = d.by_name.at(to);
    check(find(origins.begin(), origins.end(), r.path.front()) != origins.end(), "wrong origin");
    check(find(goals.begin(), goals.end(), r.path.back()) != goals.end(), "wrong destination");
    check(set<int>(r.path.begin(), r.path.end()).size() == r.path.size(), "GA returned a cycle");
    double real = 0, score = 0; bool aboard = false;
    for (size_t i = 0; i < r.steps.size(); i++) {
        const Step& s = r.steps[i];
        check(s.from == r.path[i] && s.to == r.path[i + 1], "step endpoints mismatch");
        int now = depart + (int)real, slot = slot_of(now);
        bool matched = false;
        if (s.type == 1) {
            for (const auto& e : d.ride[s.from]) {
                double c = d.cong[e.e][day][slot];
                double expected = e.sec * (1 + ALPHA * max(c - C0, 0.0));
                if (e.to != s.to || !close_enough(s.real, e.sec) || !close_enough(s.score, expected) || !close_enough(s.cong, c)) continue;
                if (!aboard && (clock_of(now) < e.first[day] || clock_of(now) > e.last[day])) continue;
                matched = true; break;
            }
        } else {
            check(s.type == 0, "unknown step type");
            for (const auto& e : d.tr[s.from]) {
                const auto& v = d.trv[e.e][day][slot];
                if (e.to == s.to && v.ok && close_enough(s.real, v.walk + v.wait) && close_enough(s.score, v.cost) && s.cong == 0) { matched = true; break; }
            }
        }
        check(matched, "step violates an edge, the common cost formula, or service hours");
        real += s.real; score += s.score; aboard = s.type == 1;
    }
    check(close_enough(real, r.real) && close_enough(score, r.score), "totals do not match returned steps");
}

int main(int argc, char** argv) {
    try {
        Data d = graph(5);
        ride(d, 0, 1, 100, 200); ride(d, 1, 3, 100, 200);
        ride(d, 0, 2, 140); ride(d, 2, 3, 140);
        for (bool by_real : {false, true}) {
            Result r = route_ga(d, "N0", "N3", WEEKDAY, 8 * 3600, by_real);
            validate(d, r, "N0", "N3", WEEKDAY, 8 * 3600);
            check(r.path == (by_real ? vector<int>{0, 1, 3} : vector<int>{0, 2, 3}), "fitness does not use the requested objective");
            check(json_result(r) == json_result(route_ga(d, "N0", "N3", WEEKDAY, 8 * 3600, by_real)), "fixed seed is not reproducible");
            Result same = route_ga(d, "N0", "N0", WEEKDAY, 8 * 3600, by_real);
            validate(d, same, "N0", "N0", WEEKDAY, 8 * 3600);
            check(same.real == 0 && same.score == 0 && same.steps.empty(), "same-station cost");
            check(!route_ga(d, "N0", "N4", WEEKDAY, 8 * 3600, by_real).found, "disconnected destination");
            check(!route_ga(d, "N3", "N0", WEEKDAY, 8 * 3600, by_real).found, "edge direction was ignored");
        }

        Data timed = graph(4);
        int first = ride(timed, 0, 1, 120), tr = transfer(timed, 1, 3, 20, 480, 500);
        timed.trv[tr][WEEKDAY][1] = {20, 10.5, 30.5, true};
        ride(timed, 0, 2, 200); ride(timed, 2, 3, 20);
        for (bool by_real : {false, true}) {
            Result r = route_ga(timed, "N0", "N3", WEEKDAY, FIRST_SEC + SLOT_SEC - 60, by_real);
            validate(timed, r, "N0", "N3", WEEKDAY, FIRST_SEC + SLOT_SEC - 60);
            check(r.path == vector<int>({0, 1, 3}) && close_enough(r.real, 150.5) && close_enough(r.score, 150.5), "transfer slot/fractional wait");
            Result weekend = route_ga(timed, "N0", "N3", SAT, FIRST_SEC + SLOT_SEC - 60, by_real);
            validate(timed, weekend, "N0", "N3", SAT, FIRST_SEC + SLOT_SEC - 60);
            check(weekend.path == vector<int>({0, 2, 3}), "day-specific transfer cost");
        }
        timed.cong[first][WEEKDAY][0] = 180;
        validate(timed, route_ga(timed, "N0", "N3", WEEKDAY, FIRST_SEC + SLOT_SEC - 60, false), "N0", "N3", WEEKDAY, FIRST_SEC + SLOT_SEC - 60);

        Data parallel = graph(2);
        ride(parallel, 0, 1, 100, 200); ride(parallel, 0, 1, 140);
        transfer(parallel, 0, 1, 10, 20, 1000);
        for (bool by_real : {false, true}) {
            Result r = route_ga(parallel, "N0", "N1", WEEKDAY, 8 * 3600, by_real);
            validate(parallel, r, "N0", "N1", WEEKDAY, 8 * 3600);
            check(close_enough(by_real ? r.real : r.score, by_real ? 30 : 140), "parallel edge/type identity");
        }
        App app; app.D = parallel;
        for (const string& algorithm : {string("dijkstra"), string("astar"), string("ga")}) {
            string log;
            string response = handle(app, "GET /api/route?from=N0&to=N1&day=weekday&time=08:00&algorithm=" + algorithm + " HTTP/1.1\r\n\r\n", log);
            auto run = algorithm == "ga" ? route_ga : algorithm == "astar" ? route_astar : route_dijkstra;
            check(response.find("HTTP/1.1 200 OK") == 0, "algorithm API status");
            check(response.find("\"algorithm\":\"" + algorithm + "\"") != string::npos, "algorithm API selection");
            check(response.find("\"f\":" + json_result(run(app.D, "N0", "N1", WEEKDAY, 8 * 3600, true))) != string::npos, "API time objective dispatch");
            check(response.find("\"c\":" + json_result(run(app.D, "N0", "N1", WEEKDAY, 8 * 3600, false))) != string::npos, "API congestion objective dispatch");
        }

        Data service = graph(2); ride(service, 0, 1, 60);
        service.ride[0][0].first[WEEKDAY] = FIRST_SEC;
        service.ride[0][0].last[WEEKDAY] = FIRST_SEC + 60;
        for (bool by_real : {false, true}) {
            for (int depart : {FIRST_SEC - 60, FIRST_SEC + 120}) {
                Result r = route_ga(service, "N0", "N1", WEEKDAY, depart, by_real);
                check(!r.found && r.no_train && !r.search_limited, "closed service metadata");
                check(r.origin_last == route_dijkstra(service, "N0", "N1", WEEKDAY, depart, by_real).origin_last, "origin last departure");
            }
            service.ride[0][0].first[WEEKDAY] = 23 * 3600;
            service.ride[0][0].last[WEEKDAY] = 25 * 3600;
            validate(service, route_ga(service, "N0", "N1", WEEKDAY, 30 * 60, by_real), "N0", "N1", WEEKDAY, 30 * 60);
            service.ride[0][0].first[WEEKDAY] = FIRST_SEC;
            service.ride[0][0].last[WEEKDAY] = FIRST_SEC + 60;
        }
        Data multi = graph(5); multi.by_name["start"] = {0, 1}; multi.by_name["goal"] = {3, 4};
        ride(multi, 0, 3, 200); ride(multi, 1, 2, 20); ride(multi, 2, 4, 20);
        for (bool by_real : {false, true}) {
            Result r = route_ga(multi, "start", "goal", WEEKDAY, 8 * 3600, by_real);
            validate(multi, r, "start", "goal", WEEKDAY, 8 * 3600);
            check(r.path == vector<int>({1, 2, 4}), "multiple origins/destinations");
        }

        Data cyclic = graph(8);
        for (int u = 0; u < 7; u++) for (int v = 0; v < 8; v++) {
            if (u != v && (u + v) % 3 != 0) ride(cyclic, u, v, 10 + (u * 17 + v * 11) % 100, (u * 31 + v * 23) % 180);
        }
        for (bool by_real : {false, true}) validate(cyclic, route_ga(cyclic, "N0", "N7", WEEKDAY, 8 * 3600, by_real), "N0", "N7", WEEKDAY, 8 * 3600);

        Data limited = graph(15);
        for (int u = 0; u < 14; u++) for (int v = 0; v < 14; v++) if (u != v) ride(limited, u, v, 1);
        transfer(limited, 13, 14, 1, 0, 1, false);
        Result exhausted = route_ga(limited, "N0", "N14", WEEKDAY, 8 * 3600, false);
        check(!exhausted.found && exhausted.search_limited, "search exhaustion must not be reported as proven unreachability");

        if (argc > 1) {
            Data actual; actual.load(argv[1]);
            int routes = 0, differences = 0;
            for (int day = 0; day < 3; day++) for (int depart : {8 * 3600, 17 * 3600 + 59 * 60, 18 * 3600, 23 * 3600 + 50 * 60}) {
                for (size_t i = 0; i < actual.name.size(); i += max<size_t>(1, actual.name.size() / 4)) {
                    const auto& from = actual.name[i]; const auto& to = actual.name[(i + actual.name.size() / 3) % actual.name.size()];
                    for (bool by_real : {false, true}) {
                        Result r = route_ga(actual, from, to, day, depart, by_real);
                        Result baseline = route_dijkstra(actual, from, to, day, depart, by_real);
                        if (r.found) validate(actual, r, from, to, day, depart);
                        else check(r.search_limited || !baseline.found, "missed a feasible real-data route without search-limit status");
                        if (r.found && baseline.found && r.path != baseline.path) differences++;
                        routes++;
                    }
                }
            }
            mt19937 samples(42);
            for (int sample = 0; sample < 200; sample++) {
                const auto& from = actual.name[samples() % actual.name.size()];
                const auto& to = actual.name[samples() % actual.name.size()];
                int day = sample % 3, depart = (sample % 2 ? 17 : 7) * 3600 + samples() % 7200;
                for (bool by_real : {false, true}) {
                    Result r = route_ga(actual, from, to, day, depart, by_real);
                    Result baseline = route_dijkstra(actual, from, to, day, depart, by_real);
                    if (r.found) validate(actual, r, from, to, day, depart);
                    else check(r.search_limited || !baseline.found, "missed a feasible sampled route without search-limit status");
                    if (r.found && baseline.found && r.path != baseline.path) differences++;
                    routes++;
                }
            }
            cout << "Real data: " << routes << " routes checked, " << differences << " paths differed from Dijkstra\n";
        }
        cout << "PASS: " << checks << " checks\n";
    } catch (const exception& e) {
        cerr << "FAIL: " << e.what() << '\n'; return 1;
    }
}
