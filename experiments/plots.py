"""7단계 그림. results/raw.csv 등을 읽어 PNG를 만든다."""
from __future__ import annotations

import csv
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

for p in ("/mnt/c/Windows/Fonts/malgun.ttf", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"):
    if Path(p).exists():
        font_manager.fontManager.addfont(p)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=p).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 110

LABEL = {"dijkstra_real": "Dijkstra\n(시간 최단)", "dijkstra_cost": "Dijkstra\n(점수 최소)",
         "astar": "A*", "larac": "LARAC", "ga": "GA", "ref": "Reference"}
COLOR = {"dijkstra_real": "#8a8f98", "dijkstra_cost": "#4c78a8", "astar": "#59a14f",
         "larac": "#f58518", "ref": "#b07aa1", "ga": "#cccccc"}


def _read(path):
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    def conv(v):
        if v in ("", None):
            return None
        if v in ("True", "False"):
            return v == "True"
        try:
            return float(v)
        except ValueError:
            return v

    return [{k: conv(v) for k, v in r.items()} for r in rows]


def _q(r):
    return (r["group"], r["day"], r["time"], r["src"], r["dst"])


def fig_gap_box(raw, out):
    algos = ["dijkstra_real", "dijkstra_cost", "astar", "larac"]
    data, labels = [], []
    for a in algos:
        gaps = [r["gap_vs_ref"] for r in raw if r["group"] == "random" and r["algo"] == a
                and r["status"] == "ok" and r["feasible"] and r["gap_vs_ref"] is not None]
        data.append(gaps)
        exact = 100 * sum(abs(x) < 1e-6 for x in gaps) / len(gaps) if gaps else 0
        labels.append(f"{LABEL[a]}\n정확 {exact:.1f}% (n={len(gaps)})")
    fig, ax = plt.subplots(figsize=(8, 4.6))
    bp = ax.boxplot(data, tick_labels=labels, patch_artist=True, showfliers=True,
                    flierprops={"markersize": 3, "alpha": .4})
    for patch, a in zip(bp["boxes"], algos):
        patch.set_facecolor(COLOR[a])
        patch.set_alpha(.75)
    ax.set_ylabel("오차율 (%) = (점수 - 기준 점수) / 기준 점수")
    ax.set_title("알고리즘별 오차율 (허용시간 안의 결과만, 기준 = Reference)")
    ax.grid(axis="y", alpha=.3)
    fig.tight_layout()
    fig.savefig(out / "fig_gap_box.png")
    plt.close(fig)


def fig_runtime(raw, out):
    algos = ["dijkstra_real", "dijkstra_cost", "astar", "larac", "ref"]
    med = [statistics.median([r["runtime_ms"] for r in raw if r["group"] == "random" and r["algo"] == a
                              and r["status"] == "ok"] or [float("nan")]) for a in algos]
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    bars = ax.bar([LABEL[a] for a in algos], med, color=[COLOR[a] for a in algos])
    ax.set_yscale("log")
    ax.set_ylabel("실행 시간 중앙값 (ms, 로그축)")
    ax.set_title("알고리즘별 실행 시간 (질의마다 5회 중앙값, 휴리스틱은 매번 새로 계산)")
    for b, v in zip(bars, med):
        ax.text(b.get_x() + b.get_width() / 2, v * 1.08, f"{v:.1f}", ha="center", fontsize=9)
    ax.grid(axis="y", alpha=.3, which="both")
    fig.tight_layout()
    fig.savefig(out / "fig_runtime.png")
    plt.close(fig)


def fig_tradeoff(raw, out):
    by = {}
    for r in raw:
        if r["group"] == "random" and r["status"] == "ok":
            by.setdefault(_q(r), {})[r["algo"]] = r
    fig, ax = plt.subplots(figsize=(7, 5))
    for algo, c, m in (("dijkstra_cost", COLOR["dijkstra_cost"], "o"), ("larac", COLOR["larac"], "^")):
        xs, ys = [], []
        for a in by.values():
            f, x = a.get("dijkstra_real"), a.get(algo)
            if f and x:
                xs.append((x["real"] - f["real"]) / 60)
                ys.append((f["cost_A"] - x["cost_A"]) / 60)
        ax.scatter(xs, ys, s=10, alpha=.45, c=c, marker=m, label=f"{LABEL[algo].replace(chr(10), ' ')} (n={len(xs)})")
    ax.axvline(5, color="k", ls="--", lw=.8)
    ax.text(5.1, ax.get_ylim()[1] * .92, "slack 5분", fontsize=8)
    ax.set_xlabel("실제시간 증가 (분) = 시간 최단 경로 대비")
    ax.set_ylabel("점수 감소 (분) = 시간 최단 경로 대비")
    ax.set_title("실제시간을 더 쓰면 점수가 얼마나 줄어드는가")
    ax.grid(alpha=.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "fig_tradeoff.png")
    plt.close(fig)


def fig_bound_active(raw, out):
    keys, vals = [], []
    groups = {}
    for r in raw:
        if r["algo"] == "larac" and r["status"] == "ok" and r["bound_active"] is not None:
            k = (r["group"] if r["group"] != "random" else f"{r['day']} {r['time']}")
            groups.setdefault(k, []).append(bool(r["bound_active"]))
    for k, v in groups.items():
        keys.append(k)
        vals.append(100 * sum(v) / len(v))
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    bars = ax.bar(keys, vals, color="#f58518")
    for b, v, k in zip(bars, vals, keys):
        ax.text(b.get_x() + b.get_width() / 2, v + 1, f"{v:.0f}%\n(n={len(groups[k])})", ha="center", fontsize=8)
    ax.set_ylabel("LARAC 제약이 실제로 걸린 비율 (%)")
    ax.set_title("LARAC: 허용시간 제약이 걸린 질의 비율 (걸리지 않으면 점수 최소 경로가 그대로 답)")
    ax.set_ylim(0, max(vals + [10]) * 1.35)
    plt.setp(ax.get_xticklabels(), rotation=25, ha="right")
    ax.grid(axis="y", alpha=.3)
    fig.tight_layout()
    fig.savefig(out / "fig_bound_active.png")
    plt.close(fig)


def fig_time_curve(out):
    p = out / "time_curve.csv"
    if not p.exists():
        return
    rows = _read(p)
    if not rows:
        return
    t = [r["time"] for r in rows]
    fig, ax = plt.subplots(figsize=(9, 4.3))
    ax.plot(t, [r["cost_min_cost"] for r in rows], "o-", ms=3, color=COLOR["dijkstra_cost"], label="점수 최소 경로의 점수")
    ax.plot(t, [r["cost_fastest_path"] for r in rows], "s-", ms=3, color=COLOR["dijkstra_real"], label="시간 최단 경로의 점수")
    ax.set_xticks(range(0, len(t), 4))
    ax.set_xticklabels(t[::4], rotation=0)
    ax.set_ylabel("점수 (분)")
    ax.set_xlabel("출발 시각 (평일)")
    ax.set_title(f"출발 시각별 점수 곡선: {rows[0]['src']} → {rows[0]['dst']}")
    ax.grid(alpha=.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "fig_time_curve.png")
    plt.close(fig)


def fig_line_compare(raw, out):
    by = {}
    for r in raw:
        if r["group"] in ("line2", "line5", "random") and r["status"] == "ok" and r["day"] == "weekday" and r["time"] == "08:30":
            by.setdefault(_q(r), {})[r["algo"]] = r
    stats = {}
    for q, a in by.items():
        f, c = a.get("dijkstra_real"), a.get("dijkstra_cost")
        if not (f and c):
            continue
        s = stats.setdefault(q[0], {"reduction": [], "extra_min": [], "differs": []})
        s["reduction"].append((f["cost_A"] - c["cost_A"]) / f["cost_A"] * 100)
        s["extra_min"].append((c["real"] - f["real"]) / 60)
        s["differs"].append(f["path"] != c["path"])
    names = [("line2", "2호선 위주"), ("line5", "5호선 위주"), ("random", "무작위 OD")]
    names = [(k, n) for k, n in names if k in stats]
    fig, axs = plt.subplots(1, 3, figsize=(11, 4))
    specs = [("reduction", "점수 감소율 평균 (%)", "시간 최단 대비"),
             ("extra_min", "실제시간 증가 평균 (분)", "점수 최소 경로 선택 시"),
             ("differs", "다른 경로가 나온 OD 비율 (%)", "시간 최단 ≠ 점수 최소")]
    for ax, (key, yl, ttl) in zip(axs, specs):
        vals = [100 * statistics.mean(stats[k][key]) if key == "differs" else statistics.mean(stats[k][key]) for k, _ in names]
        bars = ax.bar([f"{n}\n(n={len(stats[k]['differs'])})" for k, n in names], vals, color=["#4c78a8", "#59a14f", "#8a8f98"][: len(names)])
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.1f}", ha="center", va="bottom", fontsize=9)
        ax.set_ylabel(yl)
        ax.set_title(ttl, fontsize=10)
        ax.grid(axis="y", alpha=.3)
    fig.suptitle("실험 A: 노선 특성 (평일 08:30)")
    fig.tight_layout()
    fig.savefig(out / "fig_line_compare.png")
    plt.close(fig)


def fig_sensitivity(out):
    p = out / "sensitivity.csv"
    if not p.exists():
        return
    rows = _read(p)
    alphas = sorted({r["alpha"] for r in rows})
    slacks = sorted({r["slack_sec"] for r in rows})
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.8))
    for ax, key, ttl in zip(axs, ("larac_differs_pct", "cost_min_differs_pct"),
                            ("LARAC 경로가 시간 최단과 다른 OD (%)", "점수 최소 경로가 시간 최단과 다른 OD (%)")):
        grid = [[next(r[key] for r in rows if r["alpha"] == a and r["slack_sec"] == s) for s in slacks] for a in alphas]
        im = ax.imshow(grid, cmap="YlGnBu", vmin=0, vmax=max(max(x) for x in grid) * 1.1 or 1)
        ax.set_xticks(range(len(slacks)), [f"{int(s)}초" for s in slacks])
        ax.set_yticks(range(len(alphas)), [str(a) for a in alphas])
        ax.set_xlabel("slack")
        ax.set_ylabel("ALPHA")
        ax.set_title(ttl, fontsize=10)
        for i, row in enumerate(grid):
            for j, v in enumerate(row):
                ax.text(j, i, f"{v:.1f}", ha="center", va="center", fontsize=10)
        fig.colorbar(im, ax=ax, shrink=.8)
    fig.suptitle("민감도 분석 (평일 08:30, 무작위 OD)")
    fig.tight_layout()
    fig.savefig(out / "fig_sensitivity.png")
    plt.close(fig)


def make_all(out, G=None):
    out = Path(out)
    raw = _read(out / "raw.csv")
    for fn in (fig_gap_box, fig_runtime, fig_tradeoff, fig_bound_active, fig_line_compare):
        fn(raw, out)
    fig_time_curve(out)
    fig_sensitivity(out)


if __name__ == "__main__":
    import sys

    make_all(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "results")
