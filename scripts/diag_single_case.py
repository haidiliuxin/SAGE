"""单案例诊断：打印计划中原生单元的预算/参数，以及每个策略单元的执行结果。

用法（在仓库根目录、已设置 PYTHONPATH=src 时）：
    .venv\\Scripts\\python.exe scripts/diag_single_case.py --password summer2023
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HASHCAT = os.environ.get("SAGE_HASHCAT_PATH", r"F:\SA\tools\hashcat-7.1.2\hashcat.exe")
RULES = os.environ.get(
    "SAGE_RULES_PATH", r"F:\SA\tools\hashcat-7.1.2\rules\best66.rule"
)
WORK = REPO_ROOT / "data" / "benchmark"

sys.path.insert(0, str(REPO_ROOT / "scripts"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--password", default="summer2023")
    parser.add_argument("--budget", type=int, default=2_000_000)
    parser.add_argument("--time", type=float, default=30.0)
    args = parser.parse_args()

    import benchmark_100 as bench  # noqa: PLC0415

    WORK.mkdir(parents=True, exist_ok=True)
    db_path = WORK / "diag.db"
    if db_path.exists():
        db_path.unlink()
    wordlist_path = WORK / "base-words.txt"
    word_count = bench.write_wordlist(wordlist_path)

    os.environ["SAGE_DATABASE_URL"] = "sqlite:///" + str(db_path).replace("\\", "/")
    os.environ["SAGE_UPLOAD_DIR"] = str(WORK / "uploads")
    os.environ["SAGE_HASHCAT_PATH"] = HASHCAT
    os.environ["SAGE_PLANNER_TYPE"] = "rule"
    os.environ["SAGE_SCHEDULER_TYPE"] = "heuristic_bandit"
    os.environ["SAGE_WORDLIST_PATH"] = str(wordlist_path)
    os.environ["SAGE_RULES_PATH"] = RULES
    os.environ["SAGE_WORDLIST_RULES"] = RULES
    os.environ.setdefault(
        "SAGE_SEED_WORDLISTS",
        str(REPO_ROOT / "data" / "wordlists" / "zh-base.txt"),
    )
    markov_model = REPO_ROOT / "models" / "markov-demo"
    if markov_model.is_dir():
        os.environ["SAGE_S3_GENERATOR"] = "markov"
        os.environ["SAGE_MARKOV_RULESET_PATH"] = str(markov_model)
    os.environ["SAGE_MASK_LADDER"] = bench.MASK_LADDER
    os.environ["SAGE_HYBRID_MASKS"] = bench.HYBRID_MASKS
    os.environ["SAGE_STOP_ON_HIT"] = "true"

    from fastapi.testclient import TestClient  # noqa: PLC0415

    from sage_pass.main import app  # noqa: PLC0415

    print(f"词表 {word_count} 条；目标口令 {args.password}")
    with TestClient(app) as client:
        created = client.post(
            "/api/tasks",
            json={
                "name": "diag",
                "target": {
                    "type": "hash",
                    "content": hashlib.md5(args.password.encode()).hexdigest(),
                    "file_id": None,
                },
                "known_algorithm": "md5",
                "time_budget": args.time,
                "candidate_budget": args.budget,
                "context": {},
            },
        )
        task_id = created.json()["task_id"]
        client.post(f"/api/tasks/{task_id}/analyze")
        plan = client.post(f"/api/tasks/{task_id}/plan").json()
        print("计划单元：")
        for item in plan["strategies"]:
            print(
                f"  {item['strategy_id']:<3} 候选预算={item['candidate_budget']:<9} "
                f"时间预算={item['time_budget']:<4} 参数={item['parameters']}"
            )
        for warning in plan.get("warnings", []):
            print(f"  警告：{warning}")

        started = client.post(
            f"/api/tasks/{task_id}/execute", json={"mode": "real", "stop_on_hit": True}
        ).json()
        run_id = started["run_id"]
        deadline = time.monotonic() + args.time * 4 + 120
        while time.monotonic() < deadline:
            status = client.get(f"/api/runs/{run_id}/status").json()
            if status["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(1)
        result = client.get(f"/api/runs/{run_id}/result").json()
        research = client.get(f"/api/runs/{run_id}/research").json()
        print(f"运行状态={result['status']} 总测试={result['total_tested']} "
              f"恢复={result['total_recovered']} 停止原因={research.get('stop_reason')}")
        print(f"消息：{result.get('message')}")
        for item in result["strategy_results"]:
            print(f"  {json.dumps(item, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
