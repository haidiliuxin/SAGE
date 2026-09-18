"""从评测库读取每个策略单元的执行结果（调试用）。

用法：.venv\\Scripts\\python.exe scripts/dump_run_arms.py [--db 路径] [--task bench-C001]
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(REPO_ROOT / "data" / "benchmark" / "benchmark.db"))
    parser.add_argument("--task", default="")
    args = parser.parse_args()

    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    tables = [
        row[0]
        for row in connection.execute(
            "select name from sqlite_master where type='table'"
        )
    ]
    print("tables:", tables)
    for table in ("strategy_runs", "strategy_results", "run_strategies"):
        if table in tables:
            columns = [
                row[1] for row in connection.execute(f"pragma table_info({table})")
            ]
            print(f"\n== {table} ==\ncols: {columns}")
            for row in connection.execute(f"select * from {table} order by rowid limit 15"):
                print(dict(row))
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
