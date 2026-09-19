"""对比实验结果汇总：自适应调度 vs 固定顺序（同一语料、同一配置）。

用法：
    $env:PYTHONIOENCODING='utf-8'
    .venv\\Scripts\\python.exe F:\\SA\\demo-prep\\compare_schedulers.py 目录
"""

from __future__ import annotations

import csv
import glob
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path


def build_research_index(research_db: Path) -> dict[str, dict]:
    """按 run_id 汇总研究日志：探索/利用、各臂拉取与命中。"""
    connection = sqlite3.connect(f"file:{research_db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    index: dict[str, dict] = {}
    order: list[str] = []
    for row in connection.execute(
        "select run_id, event_type, payload from decision_research_events order by sequence"
    ):
        payload = json.loads(row["payload"])["payload"]
        run_id = row["run_id"]
        if run_id not in index:
            index[run_id] = {
                "decisions": 0,
                "explore": 0,
                "exploit": 0,
                "pulls": Counter(),
                "hit_arms": Counter(),
                "tested": 0,
                "seconds": 0.0,
            }
            order.append(run_id)
        entry = index[run_id]
        if row["event_type"] != "decision_completed":
            continue
        decision = payload["decision"]
        feedback = payload["feedback"]
        entry["decisions"] += 1
        entry["explore" if decision["exploration"] else "exploit"] += 1
        entry["pulls"][decision["strategy_id"]] += 1
        entry["tested"] += feedback.get("tested") or 0
        entry["seconds"] += feedback.get("duration") or 0.0
        if feedback.get("recovered"):
            entry["hit_arms"][decision["strategy_id"]] += 1
    index["__order__"] = order  # type: ignore[assignment]
    return index


def summarize(csv_path: Path, index: dict, run_ids: list[str] | None = None) -> dict:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    hits = [row for row in rows if row["recovered"] == "True"]
    by_strategy = Counter()
    for row in hits:
        for name in row["hit_strategy"].split(","):
            if name:
                by_strategy[name] += 1
    seconds = [float(row["seconds"]) for row in rows if row.get("seconds")]
    tested = [int(row["tested"]) for row in rows if row.get("tested", "").isdigit()]

    # 研究日志按 run_id 精确对齐（老 CSV 没有 run_id 时退回"最近 N 次运行"）。
    if run_ids is None:
        run_ids = [
            run_id
            for run_id in index["__order__"]
            if run_id in {row.get("run_id") for row in rows if row.get("run_id")}
        ]
    decisions = Counter()
    pulls: Counter = Counter()
    hit_arms: Counter = Counter()
    for run_id in run_ids:
        entry = index.get(run_id)
        if not isinstance(entry, dict):
            continue
        decisions[True] += entry["explore"]
        decisions[False] += entry["exploit"]
        pulls.update(entry["pulls"])
        hit_arms.update(entry["hit_arms"])
    return {
        "cases": len(rows),
        "hits": len(hits),
        "strategies": dict(sorted(by_strategy.items())),
        "distinct_strategies": len(by_strategy),
        "avg_seconds": sum(seconds) / len(seconds) if seconds else 0.0,
        "total_tested": sum(tested),
        "decisions": sum(decisions.values()),
        "explore": decisions.get(True, 0),
        "exploit": decisions.get(False, 0),
        "pulls": dict(sorted(pulls.items())),
        "hit_arms": dict(sorted(hit_arms.items())),
    }


def main() -> int:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else r"F:\SA\demo-prep\adaptive")
    research_db = Path(
        sys.argv[2] if len(sys.argv) > 2 else r"F:\SA\SAGE\data\benchmark\research\real.sqlite3"
    )
    labels = {
        "adaptive": "自适应（bandit + 切片）",
        "fixed-sliced": "固定顺序 + 切片",
        "fixed-noslice": "固定顺序 + 不切片（旧行为）",
    }
    index = build_research_index(research_db)
    order = index["__order__"]
    results = {}
    csvs = sorted(glob.glob(str(directory / "benchmark-100-*-*.csv")))
    # 按归档顺序把最近 (n*40) 次运行切给各配置（无 run_id 的 CSV 用序号对齐）
    per_config = 40
    tail = order[-per_config * len(csvs):] if len(csvs) else []
    for position, path in enumerate(csvs):
        csv_path = Path(path)
        tag = csv_path.stem.split("-", 3)[-1]
        window = tail[position * per_config : (position + 1) * per_config]
        results[tag] = summarize(csv_path, index, window if window else None)
    if not results:
        print("没有找到对比结果 CSV")
        return 1
    header = f"{'配置':<28}{'命中':>8}{'命中策略数':>12}{'平均耗时':>10}{'决策数':>8}{'探索':>7}{'利用':>7}"
    print(header)
    print("-" * len(header))
    for tag, data in results.items():
        print(
            f"{labels.get(tag, tag):<28}{data['hits']:>4}/{data['cases']:<3}"
            f"{data['distinct_strategies']:>12}{data['avg_seconds']:>9.1f}s"
            f"{data['decisions']:>8}{data['explore']:>7}{data['exploit']:>7}"
        )
    for tag, data in results.items():
        print(f"\n== {labels.get(tag, tag)} ==")
        print("  命中策略分布:", data["strategies"] or "（无）")
        print("  各臂被拉取:", data["pulls"])
        print("  各臂命中:", data["hit_arms"])
        print(f"  累计测试候选: {data['total_tested']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
