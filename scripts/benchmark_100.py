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
  区分"候选空间未覆盖（生成器能力问题）"与"覆盖但未命中（预算/调度问题）"；
  原生单元（词表 × 规则 / 掩码 / 词表 × 掩码）由 hashcat 自己枚举，Python 流里看不到，
  因此按 keyspace.py 的键空间模型单独记录（它们的覆盖由 hashcat 实测决定）。

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
# 规则集：best66（大小写/数字/符号后缀）+ d3ad0ne（3.4 万条组合规则）。
# hashcat 对多个 -r 做**规则链**（乘积）：66 × 34111 ≈ 225 万条/词，81 词 ≈ 1.8 亿键，
# 在本机约 7 秒可跑完。实测覆盖：best66 = 16/100，best66×d3ad0ne = 51/100，
# best66×dive（66 × 98670 ≈ 6.5M/词，5.3 亿键，需 20 秒以上）= 55/100；
# 注意 hashcat 的规则链很吃主机内存，三份以上规则文件会报
# "Not enough allocatable memory (RAM) for this ruleset"。
RULE_FILES = (
    RULES,
    r"F:\SA\tools\hashcat-7.1.2\rules\d3ad0ne.rule",
)
JOHN_RUN = r"F:\SA\tools\john\john-1.9.0-jumbo-1-win64\run"
WORK = REPO_ROOT / "data" / "benchmark"

# 时间预算：规则链键空间大（2.1 亿键 ≈ 9 秒纯 GPU 时间 + 3 秒启动），
# 30 秒的整轮预算会把 S1 的份额（20% = 6 秒）压到跑不完。
# 自适应切片开启时每个原生单元要多一次"探针"启动（规则链加载本身就要数秒），
# 因此评测用 120 秒，让每个单元都有"探针 + 提交"的空间。
TIME_BUDGET = 120
# 原生攻击（词表 × 规则链 / 掩码 / 词表 × 掩码）由 hashcat 自己枚举候选：
# 候选预算必须容纳这些键空间，否则计划层会裁掉它们。
CANDIDATE_BUDGET = 1_000_000_000
# 单批次候选粒度：默认 1000 会让每次 hashcat 启动（约 3 秒）只测 1000 条候选，
# 吞吐评测应放大以摊薄进程启动开销（Python 候选单元一次提交整段候选）。
STREAM_BATCH_SIZE = 100_000
MASK_LADDER = "?d?d?d?d,?l?l?l?l,?l?l?l?l?d?d"
# 混合掩码：词 + 短后缀（?d?d / !）与词 + 4 位年份（?d?d?d?d，覆盖 summer2023 这类）。
HYBRID_MASKS = "?d?d?d?d,?d?d,!"

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


def native_keyspaces(
    parameters_by_strategy: dict[str, dict],
    *,
    wordlist_lines: int,
) -> dict[str, int]:
    """各原生单元的键空间（hashcat 会实际测试的候选数）。

    - S1：词表条数 × 规则条数（词表 × 规则）
    - S6：掩码键空间之和
    - S7：词表条数 × 混合掩码键空间之和
    """
    units: dict[str, int] = {}
    from sage_pass.keyspace import (  # noqa: PLC0415
        masks_keyspace,
        rule_chain_count,
    )

    s1_rules = parameters_by_strategy.get("S1", {}).get("hashcat_rule_files") or []
    if wordlist_lines > 0 and s1_rules:
        # 多个规则文件是规则链（乘积），不是并集。
        units["S1"] = wordlist_lines * rule_chain_count(s1_rules)
    masks = parameters_by_strategy.get("S6", {}).get("hashcat_masks") or []
    if masks:
        size = masks_keyspace(masks)
        if size is not None:
            units["S6"] = size
    hybrid = parameters_by_strategy.get("S7", {}).get("hashcat_hybrid_mask") or []
    if hybrid and wordlist_lines > 0:
        size = masks_keyspace(hybrid)
        if size is not None:
            units["S7"] = wordlist_lines * size
    return units


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
    parser.add_argument(
        "--scheduler",
        default="heuristic_bandit",
        help="调度器类型：heuristic_bandit（自适应）| fixed | round_robin | ucb",
    )
    parser.add_argument(
        "--no-adaptive-slicing",
        action="store_true",
        help="关闭原生单元的自适应切片（对照：一次拉完整段键空间的旧行为）",
    )
    parser.add_argument(
        "--tag",
        default="",
        help="结果文件附加标签（例如 adaptive / fixed-noslice）",
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
    os.environ["SAGE_SCHEDULER_TYPE"] = args.scheduler
    os.environ["SAGE_ADAPTIVE_SLICING"] = (
        "false" if args.no_adaptive_slicing else "true"
    )
    os.environ["SAGE_WORDLIST_PATH"] = str(wordlist_path)
    os.environ["SAGE_RULES_PATH"] = RULES
    # 词表 × 规则：多个规则文件（逗号分隔），S1 的原生词表作业会带 -r。
    os.environ["SAGE_WORDLIST_RULES"] = ",".join(RULE_FILES)
    os.environ["SAGE_DECISION_BATCH_SIZE"] = str(STREAM_BATCH_SIZE)
    os.environ["SAGE_HASHCAT_STREAM_BATCH_SIZE"] = str(STREAM_BATCH_SIZE)
    os.environ.setdefault(
        "SAGE_SEED_WORDLISTS",
        str(REPO_ROOT / "data" / "wordlists" / "zh-base.txt"),
    )
    markov_model = REPO_ROOT / "models" / "markov-demo"
    if markov_model.is_dir():
        # 本地训练的 OMEN/Markov 模型（scripts/train_markov.py 产出）。
        os.environ["SAGE_S3_GENERATOR"] = "markov"
        os.environ["SAGE_MARKOV_RULESET_PATH"] = str(markov_model)
    os.environ["SAGE_MASK_LADDER"] = MASK_LADDER
    os.environ["SAGE_HYBRID_MASKS"] = HYBRID_MASKS
    os.environ["SAGE_STOP_ON_HIT"] = "true"

    sys.path.insert(0, str(REPO_ROOT / "src"))
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from sage_pass.candidate_generator import CandidateGenerator  # noqa: PLC0415
    from sage_pass.keyspace import file_line_count  # noqa: PLC0415
    from sage_pass.main import app  # noqa: PLC0415
    from sage_pass.schemas import StrategyPlan, TaskContext  # noqa: PLC0415

    cases = corpus()
    if args.limit:
        cases = cases[: args.limit]
    print(f"词表基础词 {word_count} 条；评测口令 {len(cases)} 条", flush=True)
    print(f"配置：时间预算 {TIME_BUDGET}s / 候选预算 {CANDIDATE_BUDGET}；"
          f"批大小 {STREAM_BATCH_SIZE}；规则 {','.join(Path(r).name for r in RULE_FILES)}；"
          f"掩码 {MASK_LADDER}；混合 {HYBRID_MASKS}", flush=True)
    print(f"调度器 {args.scheduler}；自适应切片 "
          f"{'关闭' if args.no_adaptive_slicing else '开启'}", flush=True)

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
                "stop_reason": "", "space_hit_arms": "", "native_units": "",
                "reason": "", "error": "", "run_id": "",
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
                # 原生单元（hashcat 自己枚举）：Python 流里看不到，按键空间单独记录，
                # 避免把它们误判成"生成器能力不足"。
                native_units = native_keyspaces(
                    {item.strategy_id.value: dict(item.parameters) for item in plan.strategies},
                    wordlist_lines=(
                        file_line_count(os.environ["SAGE_WORDLIST_PATH"])
                        if os.environ.get("SAGE_WORDLIST_PATH") else 0
                    ),
                )
                row["native_units"] = ",".join(
                    f"{strategy}:{keyspace}" for strategy, keyspace in native_units.items()
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
                row["run_id"] = run_id
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
                row["run_message"] = (result.get("message") or "")[:200]
                row["recovered"] = result["total_recovered"] > 0
                if row["recovered"]:
                    row["hit_strategy"] = ",".join(
                        item["strategy_id"] for item in result["strategy_results"]
                        if item["recovered"] > 0
                    )
                    row["reason"] = "命中"
                else:
                    if hits:
                        row["reason"] = (
                            f"候选空间已覆盖（{','.join(hits)}）但未命中：预算/调度问题"
                        )
                    elif native_units:
                        row["reason"] = (
                            "Python 候选空间未覆盖；原生单元已实测仍未命中"
                            f"（{row['native_units']}）"
                        )
                    else:
                        row["reason"] = "候选空间未覆盖：生成器能力不足"
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
    suffix = f"-{args.tag}" if args.tag else ""
    csv_path = out_dir / f"benchmark-100-{stamp}{suffix}.csv"
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
        f"- 调度器：`{args.scheduler}`；自适应切片："
        f"{'关闭' if args.no_adaptive_slicing else '开启'}",
        f"- 配置：时间预算 {TIME_BUDGET}s、候选预算 {CANDIDATE_BUDGET}、"
        f"命中即停、词表 {word_count} 条、"
        f"规则 {'+'.join(Path(r).stem for r in RULE_FILES)}、"
        f"掩码 `{MASK_LADDER}`、混合 `{HYBRID_MASKS}`",
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

    lines += ["", "## 未命中明细", "", "| ID | 类别 | 口令 | Python 候选空间命中单元 | 原生单元键空间 | 原因 |", "| --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        if not row["recovered"]:
            lines.append(
                f"| {row['id']} | {row['category']} | `{row['password']}` | "
                f"{row['space_hit_arms'] or '—'} | {row.get('native_units') or '—'} | "
                f"{row['reason']} |"
            )
    lines += ["", "## 命中明细（按策略）", "", "| ID | 类别 | 口令 | 命中策略 | 测试候选 | 耗时(s) |", "| --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        if row["recovered"]:
            lines.append(
                f"| {row['id']} | {row['category']} | `{row['password']}` | "
                f"{row['hit_strategy']} | {row['tested']} | {row['seconds']} |"
            )
    by_strategy: dict[str, int] = {}
    for row in rows:
        if row["recovered"]:
            for name in row["hit_strategy"].split(","):
                if name:
                    by_strategy[name] = by_strategy.get(name, 0) + 1
    lines += ["", "## 策略命中分布", ""]
    lines += [
        f"- {name}：{count} 次" for name, count in sorted(by_strategy.items())
    ] or ["- （无命中）"]
    md_path = out_dir / f"benchmark-100-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n结果：{hit}/{total} 命中")
    print(f"CSV: {csv_path}")
    print(f"报告: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
