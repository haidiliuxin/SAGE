"""100 道口令评测：跑真实链路，统计命中率并给出失败原因归因。

用法（仓库根目录）：

    $env:PYTHONPATH='src'
    .\\.venv\\Scripts\\python.exe scripts\\benchmark_100.py --out docs/experiments

评测内容：
- 10 类共 100 个"不那么常见"的口令（结构口令、拼音、个人信息派生、旧口令复用、
  键盘序列、短语、leet、中文口令、高熵对照等）；
- 每个口令建一个 MD5 目标任务，走 分析 → 规划 → 真实执行（命中即停），
  记录是否命中、由哪个策略命中、测试候选数、耗时、停止原因；
- 对未命中的案例，逐单元拉取候选流判断该口令是否落在候选空间内，
  区分"候选空间未覆盖（生成器能力问题）"与"覆盖但未命中（预算/调度问题）"。

输出：docs/experiments/benchmark-100-<时间戳>.csv 与同名 .md 报告。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HASHCAT = r"F:\SA\tools\hashcat-7.1.2\hashcat.exe"
RULES = r"F:\SA\tools\hashcat-7.1.2\rules\best66.rule"
JOHN_RUN = r"F:\SA\tools\john\john-1.9.0-jumbo-1-win64\run"
WORK = REPO_ROOT / "data" / "benchmark"

TIME_BUDGET = 30
CANDIDATE_BUDGET = 20_000
MASK_LADDER = "?d?d?d?d,?l?l?l?l,?l?l?l?l?d?d"
HYBRID_MASKS = "?d?d,!"

# ----------------------------------------------------------------- 语料
# 词表基础词（评测"词表 × 规则"这条路径能覆盖多少）
BASE_WORDS = [
    # 英文常见词（配合规则变形）
    "summer", "winter", "dragon", "coffee", "monkey", "flower", "guitar", "banana",
    "rocket", "silver", "thunder", "shadow", "house", "master", "hunter", "admin",
    "password", "welcome", "security", "hacker", "cyber", "punk", "darknight",
    "blackwidow", "sunshine", "trustno", "opensesame", "helloworld", "goodmorning",
    "iloveyou", "letmein", "mypassword", "qwerty", "asdfgh", "zxcvbn", "poiuytre",
    "qazwsx", "qweasd", "zxcasd", "1qaz2wsx", "1q2w3e4r", "1qaz", "asdfghjkl",
    "never", "gonna", "troubador", "correct", "horse", "jungle", "china", "beijing",
    # 拼音 / 中文
    "woaini", "wodemima", "zhongguo", "shanghai", "xuexi", "gongzuo", "jiating",
    "pengyou", "laoshi", "mima", "zhangsan", "lisi", "wangwu", "zhaoliu", "sunqi",
    "zhouba", "wujiu", "zhengshi", "chenyi", "xiaoming", "shengri", "kuaile",
    "密码", "我的密码", "张三", "生日快乐", "示例大学", "网络安全", "图书馆", "课题组",
]
# 个人信息（供 S4 使用）
PII = {
    "name": "张三", "nickname": "小明", "username": "zhangsan",
    "email_local_part": "zhangsan.work", "phone_suffix": "7788",
    "birthday": "03-05", "birth_year": 1998,
    "years": [2019, 2020, 2021, 2023, 2024, 2025],
    "region": "北京", "organization": "示例大学",
    "interest_words": ["摄影", "篮球"], "authorized_keywords": ["示例大学", "网络安全"],
}


def corpus() -> list[dict]:
    cases: list[dict] = []

    def add(category: str, password: str, **extra) -> None:
        cases.append({"category": category, "password": password, **extra})

    # 1 词典词 + 数字
    for password in (
        "summer2023", "dragon88", "coffee2024", "monkey123", "flower99",
        "guitar007", "banana2020", "rocket42", "silver77", "thunder21",
    ):
        add("dict+num", password)
    # 2 词典词 + 符号 / 替换
    for password in (
        "P@ssw0rd2024", "Dr@gon!88", "C0ffee@7", "Monkey#123", "H0use!2021",
        "Sh@dow99", "Summ3r!23", "Winter#2024", "Rocket@21", "Fl0wer!7",
    ):
        add("dict+symbol", password)
    # 3 拼音 + 数字
    for password in (
        "woaini1314", "wodemima888", "zhongguo2023", "beijing2008", "shanghai2020",
        "xuexi1234", "gongzuo2024", "jiating666", "pengyou520", "laoshi888",
    ):
        add("pinyin+num", password)
    # 4 个人信息派生（提供 PII）
    for password in (
        "zhangsan1998", "Xiaoming_01", "zhangsan0305", "Xiaoming1998",
        "zhangsan7788", "Zhangsan2024", "xiaoming2001", "zhangsan_1998",
        "Xiaoming#1998", "zhangsan.work",
    ):
        add("pii", password, context=PII)
    # 5 旧口令复用（提供历史口令）
    history = ["Sunshine2019!", "Admin@2020", "Hunter2!2018", "Qwerty2019"]
    for password, used in (
        ("Sunshine2023#", history), ("Admin@2025", history), ("Hunter2!2024", history),
        ("Qwerty2024", history), ("sunshine2023!", history), ("Admin@2020!", history),
        ("hunter2!2018", history), ("Qwerty2019!", history), ("Sunshine2019", history),
        ("Admin2025", history),
    ):
        add("reuse", password, historical_passwords=used)
    # 6 键盘序列
    for password in (
        "1qaz2wsx", "qazwsx123", "zxcvbnm!", "1q2w3e4r", "asdfghjkl1",
        "qwerty789", "poiuytrewq", "1qaz@wsx", "zxcasdqwe", "qweasdzxc2",
    ):
        add("keyboard", password)
    # 7 短语 / 多词
    for password in (
        "correct-horse", "iloveyou2", "letmein123", "mypassword2024", "trustno1",
        "opensesame", "helloworld88", "goodmorning7", "ilovechina2020", "welcometothejungle",
    ):
        add("phrase", password)
    # 8 leet / 高级替换
    for password in (
        "Tr0ub4dor&3", "P@ssw0rd!", "Adm1n1str@tor", "S3cur1ty2024", "H@ck3r2020",
        "M@st3rP13ce", "D@rkN1ght99", "Bl@ckW1d0w", "Cyb3rPunk2077", "N3v3rG0nna",
    ):
        add("leet", password)
    # 9 中文 / 中英混合
    for password in (
        "密码123", "我的密码2024", "张三@1998", "生日快乐2024", "woaini520!",
        "xiaoming_2001", "zhang@123", "123456abc", "a1b2c3d4", "password!@#2024",
    ):
        add("cn+mixed", password, context=PII)
    # 10 高熵对照（预期不可达，用于确认评测本身不"作弊"）
    for password in (
        "X7#kL9$mQ2", "vN4$tR8*wZ", "9f2K!pQ7#x", "m3L$vB8nQ1!", "Zx9!Rk2#Wt",
        "7uJ#pL4$xN", "Qw8$Zr3!Vm", "Lk5#Bn9$Xy", "Tf2!Wq7#Rp", "8hM$xK4!Vd",
    ):
        add("hard(对照)", password)

    for index, case in enumerate(cases, start=1):
        case["id"] = f"C{index:03d}"
        case["md5"] = hashlib.md5(case["password"].encode()).hexdigest()
    return cases


def write_wordlist(path: Path) -> int:
    words = list(dict.fromkeys(BASE_WORDS))
    path.write_text("\n".join(words) + "\n", encoding="utf-8")
    return len(words)


# ----------------------------------------------------------------- 评测
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/experiments")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个（调试用）")
    parser.add_argument(
        "--space-only",
        action="store_true",
        help="只做候选空间覆盖分析（不调用 hashcat，可在无 GPU/内存紧张时使用）",
    )
    args = parser.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    db_path = WORK / "benchmark.db"
    if db_path.exists():
        db_path.unlink()
    wordlist_path = WORK / "base-words.txt"
    word_count = write_wordlist(wordlist_path)

    os.environ["SAGE_DATABASE_URL"] = "sqlite:///" + str(db_path).replace("\\", "/")
    os.environ["SAGE_UPLOAD_DIR"] = str(WORK / "uploads")
    os.environ["SAGE_HASHCAT_PATH"] = HASHCAT
    os.environ["SAGE_ZIP2JOHN_PATH"] = str(Path(JOHN_RUN) / "zip2john.exe")
    os.environ["SAGE_PLANNER_TYPE"] = "rule"
    os.environ["SAGE_SCHEDULER_TYPE"] = "heuristic_bandit"
    os.environ["SAGE_WORDLIST_PATH"] = str(wordlist_path)
    os.environ["SAGE_RULES_PATH"] = RULES
    os.environ["SAGE_MASK_LADDER"] = MASK_LADDER
    os.environ["SAGE_HYBRID_MASKS"] = HYBRID_MASKS
    os.environ["SAGE_STOP_ON_HIT"] = "true"

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from sage_pass.candidate_generator import CandidateGenerator  # noqa: PLC0415
    from sage_pass.main import app  # noqa: PLC0415
    from sage_pass.schemas import StrategyPlan, TaskContext  # noqa: PLC0415

    cases = corpus()
    if args.limit:
        cases = cases[: args.limit]
    print(f"词表基础词 {word_count} 条；评测口令 {len(cases)} 条", flush=True)
    print(f"配置：时间预算 {TIME_BUDGET}s / 候选预算 {CANDIDATE_BUDGET}；"
          f"掩码 {MASK_LADDER}；混合 {HYBRID_MASKS}", flush=True)

    rows: list[dict] = []
    with TestClient(app) as client:
        for index, case in enumerate(cases, start=1):
            task_payload = {
                "name": f"bench-{case['id']}",
                "target": {"type": "hash", "content": case["md5"], "file_id": None},
                "known_algorithm": "md5",
                "time_budget": TIME_BUDGET,
                "candidate_budget": CANDIDATE_BUDGET,
                "context": case.get("context", {}),
            }
            if case.get("historical_passwords"):
                task_payload["historical_passwords"] = case["historical_passwords"]

            row = {
                "id": case["id"], "category": case["category"], "password": case["password"],
                "recovered": False, "hit_strategy": "", "tested": 0, "seconds": 0.0,
                "stop_reason": "", "space_hit_arms": "", "reason": "", "error": "",
            }
            started = time.monotonic()
            try:
                task_id = client.post("/api/tasks", json=task_payload).json()["task_id"]
                client.post(f"/api/tasks/{task_id}/analyze")
                plan = StrategyPlan.model_validate(
                    client.post(f"/api/tasks/{task_id}/plan").json()
                )

                # 候选空间归因（两种模式都需要）
                def space_hits() -> tuple[list[str], dict[str, int]]:
                    arms: list[str] = []
                    produced_by_arm: dict[str, int] = {}
                    for item in plan.strategies:
                        stream = CandidateGenerator().open_plan_stream(
                            plan,
                            task_context=(
                                TaskContext(**case["context"])
                                if case.get("context") else None
                            ),
                            historical_passwords=tuple(
                                case.get("historical_passwords", ())
                            ),
                            max_candidates=CANDIDATE_BUDGET,
                        )
                        produced = 0
                        while produced < CANDIDATE_BUDGET:
                            batch = stream.pull(
                                item.strategy_id, CANDIDATE_BUDGET - produced
                            )
                            if not batch.candidates:
                                break
                            produced += len(batch.candidates)
                            if case["password"] in batch.candidates:
                                arms.append(item.strategy_id.value)
                                break
                        produced_by_arm[item.strategy_id.value] = produced
                    return arms, produced_by_arm

                hits, produced_by_arm = space_hits()
                row["space_hit_arms"] = ",".join(hits)
                row["plan_arms"] = ",".join(
                    f"{item.strategy_id.value}:{produced_by_arm.get(item.strategy_id.value, 0)}"
                    for item in plan.strategies
                )

                if args.space_only:
                    row["reason"] = (
                        f"候选空间覆盖（{','.join(hits)}）" if hits
                        else "候选空间未覆盖：当前生成栈产不出该口令"
                    )
                    row["seconds"] = round(time.monotonic() - started, 1)
                    rows.append(row)
                    print(
                        f"[{index:>3}/{len(cases)}] {row['id']} {row['category']:<12} "
                        f"{'覆盖' if hits else '未覆盖'} by={row['space_hit_arms'] or '-'}",
                        flush=True,
                    )
                    continue

                started_run = client.post(
                    f"/api/tasks/{task_id}/execute", json={"mode": "real", "stop_on_hit": True}
                )
                run_id = started_run.json()["run_id"]
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    status = client.get(f"/api/runs/{run_id}/status").json()
                    if status["status"] in {"completed", "failed", "cancelled"}:
                        break
                    time.sleep(1)
                result = client.get(f"/api/runs/{run_id}/result").json()
                research = client.get(f"/api/runs/{run_id}/research").json()

                row["seconds"] = round(time.monotonic() - started, 1)
                row["tested"] = result["total_tested"]
                row["stop_reason"] = research.get("stop_reason") or ""
                row["run_status"] = result["status"]
                row["recovered"] = result["total_recovered"] > 0
                if row["recovered"]:
                    row["hit_strategy"] = ",".join(
                        item["strategy_id"] for item in result["strategy_results"]
                        if item["recovered"] > 0
                    )
                    row["reason"] = "命中"
                else:
                    row["reason"] = (
                        f"候选空间已覆盖（{','.join(hits)}）但未命中：预算/调度问题"
                        if hits else "候选空间未覆盖：生成器能力不足"
                    )
            except Exception as exc:  # 单个案例失败不影响整体
                row["error"] = f"{type(exc).__name__}: {exc}"
                row["reason"] = "执行异常"
            rows.append(row)
            flag = "命中" if row["recovered"] else "未中"
            print(
                f"[{index:>3}/{len(cases)}] {row['id']} {row['category']:<12} {flag} "
                f"策略={row['hit_strategy'] or '-':<10} 测试={row['tested']:<6} "
                f"{row['seconds']:>5.1f}s  {row['reason']}",
                flush=True,
            )

    out_dir = REPO_ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    csv_path = out_dir / f"benchmark-100-{stamp}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows)
    hit = sum(1 for row in rows if row["recovered"])
    by_category: dict[str, list[dict]] = {}
    for row in rows:
        by_category.setdefault(row["category"], []).append(row)

    lines = [
        f"# 100 道口令评测报告（{datetime.now().strftime('%Y-%m-%d %H:%M')}）",
        "",
        f"- 命中：**{hit}/{total}**（{hit / total * 100:.1f}%）",
        f"- 配置：时间预算 {TIME_BUDGET}s、候选预算 {CANDIDATE_BUDGET}、"
        f"命中即停、词表 {word_count} 条、规则 best66、掩码 `{MASK_LADDER}`、混合 `{HYBRID_MASKS}`",
        "",
        "## 按类别",
        "",
        "| 类别 | 命中/总数 | 命中率 | 未命中原因分布 |",
        "| --- | --- | --- | --- |",
    ]
    for category, items in by_category.items():
        ok = sum(1 for item in items if item["recovered"])
        reasons: dict[str, int] = {}
        for item in items:
            if not item["recovered"]:
                reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        reason_text = "；".join(f"{k}×{v}" for k, v in reasons.items()) or "—"
        lines.append(f"| {category} | {ok}/{len(items)} | {ok / len(items) * 100:.0f}% | {reason_text} |")

    lines += ["", "## 未命中明细", "", "| ID | 类别 | 口令 | 候选空间命中单元 | 原因 |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        if not row["recovered"]:
            lines.append(
                f"| {row['id']} | {row['category']} | `{row['password']}` | "
                f"{row['space_hit_arms'] or '—'} | {row['reason']} |"
            )
    lines += ["", "## 命中明细（按策略）", "", "| ID | 类别 | 口令 | 命中策略 | 测试候选 | 耗时(s) |", "| --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        if row["recovered"]:
            lines.append(
                f"| {row['id']} | {row['category']} | `{row['password']}` | "
                f"{row['hit_strategy']} | {row['tested']} | {row['seconds']} |"
            )
    md_path = out_dir / f"benchmark-100-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n结果：{hit}/{total} 命中")
    print(f"CSV: {csv_path}")
    print(f"报告: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
