#!/usr/bin/env python3
"""수도권 확장 데이터 생성: subway-etl의 산출물에서 팀 CSV(1~9호선)에 없는 노선만 data/ext/에 만든다.

  python tools/build_extension.py

팀 CSV 4개는 건드리지 않는다. 확장 데이터는 **추정값**이다(data/ext/README.txt 참고).
  - 탑승: 시간은 ETL의 seconds, 혼잡도는 0(점수 = 실제 시간).
  - 첫차·막차: ETL에 없어서 05:30~24:00 운행으로 가정.
  - 환승: ETL은 합계(걷기 + 배차간격÷2 + 페널티 180초)만 갖고 있다. 팀 규칙은 페널티가 없으므로
      real = seconds - 180   (같은 승강장 SHARED 환승은 페널티가 없으므로 real = seconds)
      cost = 2.204 * real    (팀 확정 배율)
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ETL = ROOT / "subway-etl" / "output"
DATA = ROOT / "data"
OUT = DATA / "ext"

PENALTY = 180  # subway-etl/etl/step4_validate.py TRANSFER_PENALTY_SEC
TRANSFER_MULT = 2.204  # 팀 Notion 설명서(CLAUDE.md 요약)
FIRST_SEC = 5 * 3600 + 30 * 60  # 05:30
LAST_SEC = 24 * 3600  # 24:00


def read(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write(p, rows, fields):
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    old = {r["id"] for r in read(DATA / "station_nodes.csv")}
    nodes = [r for r in read(ETL / "station_nodes.csv") if r["id"] not in old]
    ids = {r["id"] for r in nodes}
    write(OUT / "station_nodes_ext.csv",
          [{k: r[k] for k in ("id", "station_name", "line_key", "lat", "lon")} for r in nodes],
          ["id", "station_name", "line_key", "lat", "lon"])

    rides, transfers = [], []
    for e in read(ETL / "station_edges.csv"):
        u, v = e["from_node_id"], e["to_node_id"]
        if u in old and v in old:
            continue  # 1~9호선끼리는 팀 데이터가 우선
        if u not in ids and u not in old or v not in ids and v not in old:
            raise SystemExit(f"알 수 없는 노드: {u}->{v}")
        sec = int(e["seconds"])
        if e["edge_type"] == "RIDE":
            row = {"from_node_id": u, "to_node_id": v, "seconds": sec, "source": e["source"]}
            for d in ("weekday", "sat", "sun"):
                row[f"first_dep_{d}_sec"] = FIRST_SEC
                row[f"last_dep_{d}_sec"] = LAST_SEC
            rides.append(row)
        else:
            real = sec if e["source"] == "SHARED" else sec - PENALTY
            if real <= 0:
                raise SystemExit(f"환승 시간이 0 이하: {u}->{v} {sec}")
            transfers.append({"from_node_id": u, "to_node_id": v, "real_sec": real,
                              "cost_sec": round(TRANSFER_MULT * real, 3), "source": e["source"]})
    fields = ["from_node_id", "to_node_id", "seconds", "source"] + \
             [f"{k}_dep_{d}_sec" for d in ("weekday", "sat", "sun") for k in ("first", "last")]
    # 열 순서를 팀 CSV와 맞춘다: first_dep_weekday, last_dep_weekday, first_dep_sat, ...
    write(OUT / "station_edges_ext.csv", rides, fields)
    write(OUT / "transfer_edges_ext.csv", transfers,
          ["from_node_id", "to_node_id", "real_sec", "cost_sec", "source"])
    (OUT / "README.txt").write_text(
        "수도권 확장 데이터 (추정값). tools/build_extension.py가 subway-etl/output에서 만든다.\n"
        "팀 CSV 4개(1~9호선)는 바뀌지 않는다.\n"
        "- station_nodes_ext.csv   : 팀 CSV에 없는 노선의 노드\n"
        "- station_edges_ext.csv   : 탑승 간선. seconds는 ETL 값(실측 차용·속도표 추정·GTX). 혼잡도는 0으로 본다.\n"
        "                           첫차·막차는 ETL에 없어서 05:30~24:00 운행으로 가정했다.\n"
        "- transfer_edges_ext.csv  : 환승 간선. ETL 합계에서 페널티 180초를 뺀 값이 real, 2.204배가 cost.\n"
        "                           같은 승강장(SHARED) 환승은 페널티가 없어서 그대로 real.\n"
        "                           걷기·배차간격÷2 대기는 시간대와 상관없이 일정한 값이다.\n",
        encoding="utf-8")
    print(f"노드 {len(nodes)}, 탑승 간선 {len(rides)}, 환승 간선 {len(transfers)} → {OUT}")


if __name__ == "__main__":
    main()
