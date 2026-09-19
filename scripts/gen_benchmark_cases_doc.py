"""生成《100 条口令评测：用例清单与复现方式》文档（把评测用例交给队友复测）。

同时把本机已有评测结果（归档 CSV）标注到每条用例上，便于队友区分
"本机命中 / 本机未命中（以及是哪个配置下未命中）"。
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# 归档结果：标签 → (CSV 文件名, 覆盖范围描述)
ARCHIVED_RUNS = {
    "100": (
        "benchmark-100-20260919-0316.csv",
        "完整 100 条（固定顺序 + 不切片，时间预算 60s）",
    ),
    "adaptive30": (
        "benchmark-100-20260919-1042-adaptive.csv",
        "前 30 条（bandit + 自适应切片，时间预算 120s）",
    ),
    "baseline30": (
        "benchmark-100-20260919-1049-fixed-noslice.csv",
        "前 30 条（固定顺序 + 不切片，时间预算 120s）",
    ),
}


def load_benchmark_module():
    spec = importlib.util.spec_from_file_location(
        "bench", REPO_ROOT / "scripts" / "benchmark_100.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_results() -> dict[str, dict[str, dict]]:
    """读取归档 CSV：{标签: {用例 id: 结果行}}。"""
    results: dict[str, dict[str, dict]] = {}
    for label, (filename, _) in ARCHIVED_RUNS.items():
        path = REPO_ROOT / "docs" / "experiments" / filename
        if not path.is_file():
            continue
        results[label] = {
            row["id"]: row
            for row in csv.DictReader(path.open(encoding="utf-8-sig"))
        }
    return results


def mark(row: dict | None) -> str:
    if row is None:
        return "—"
    status = row.get("run_status") or ""
    if status == "failed":
        return "⚠ 运行失败"
    if row.get("recovered") == "True":
        strategy = row.get("hit_strategy") or "?"
        return f"✅ {strategy}"
    return "❌ 未命中"


def summary_table(results: dict[str, dict[str, dict]], cases: list[dict]) -> str:
    categories = list(dict.fromkeys(case["category"] for case in cases))
    lines = [
        "| 类别 | " + " | ".join(ARCHIVED_RUNS[label][1] for label in ARCHIVED_RUNS) + " |",
        "| --- | " + " | ".join("---" for _ in ARCHIVED_RUNS) + " |",
    ]
    for category in categories:
        cells = []
        for label in ARCHIVED_RUNS:
            rows = results.get(label, {})
            ids = [case["id"] for case in cases if case["category"] == category]
            hit = sum(1 for case_id in ids if rows.get(case_id, {}).get("recovered") == "True")
            total = sum(1 for case_id in ids if case_id in rows)
            cells.append(f"{hit}/{total}" if total else "—")
        lines.append(f"| {category} | " + " | ".join(cells) + " |")
    totals = []
    for label in ARCHIVED_RUNS:
        rows = results.get(label, {})
        hit = sum(1 for row in rows.values() if row.get("recovered") == "True")
        totals.append(f"**{hit}/{len(rows)}**" if rows else "—")
    lines.append("| **合计** | " + " | ".join(totals) + " |")
    return "\n".join(lines)


def main() -> int:
    module = load_benchmark_module()
    cases = module.corpus()
    results = load_results()

    rows = []
    for case in cases:
        marks = []
        for label in ARCHIVED_RUNS:
            marks.append(mark(results.get(label, {}).get(case["id"])))
        extra = "—"
        if case.get("context"):
            extra = "PII（见下方）"
        if case.get("historical_passwords"):
            extra = "历史口令：" + "、".join(case["historical_passwords"])
        rows.append(
            f"| {case['id']} | {case['category']} | `{case['password']}` | "
            + " | ".join(marks)
            + f" | {extra} |"
        )

    missed_100 = [
        case
        for case in cases
        if results.get("100", {}).get(case["id"], {}).get("recovered") != "True"
        and case["id"] in results.get("100", {})
    ]
    missed_lines = [
        "| ID | 类别 | 口令 | 本机未命中原因 |",
        "| --- | --- | --- | --- |",
    ]
    for case in missed_100:
        row = results["100"][case["id"]]
        reason = row.get("reason") or ""
        if case["category"].startswith("hard"):
            short = "设计上的不可达样本（高熵对照）"
        elif reason.startswith("候选空间已覆盖"):
            short = "候选空间已覆盖但未命中（预算/调度问题）"
        elif "原生单元已实测" in reason:
            short = "Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中"
        else:
            short = reason or "—"
        missed_lines.append(
            f"| {case['id']} | {case['category']} | `{case['password']}` | {short} |"
        )

    pii = json.dumps(module.PII, ensure_ascii=False, indent=2, sort_keys=True)
    rules = "\n".join(f"- `{Path(path).name}`" for path in module.RULE_FILES)
    doc = f"""# 100 条口令评测：用例清单与复现方式（交给队友复测）

> 这份评测回答两个问题：**系统能解出多少"不那么常见"的口令**、**命中是否只来自基线单元（S1）**。
> 语料与词表都由 `scripts/benchmark_100.py` 内置生成（确定性、可复现），不需要额外准备数据。
>
> 下表已标注**本机实测结果**：✅ 命中（含命中策略）、❌ 未命中、⚠ 运行失败。
> 机读版本（带同样标注）：`docs/experiments/benchmark-cases-100.csv`。

## 一、本机结果总览

{summary_table(results, cases)}

三个配置分别是：

{chr(10).join(f"{index + 1}. **{description}** — `docs/experiments/{ARCHIVED_RUNS[label][0]}`" for index, (label, (_, description)) in enumerate(ARCHIVED_RUNS.items()))}

## 二、怎么跑（最短路径）

```powershell
git fetch origin
git checkout feat/adaptive-native-slicing        # 自适应切片分支；旧行为在 main 上
python -m venv .venv; .\\.venv\\Scripts\\pip install -r requirements.txt
$env:PYTHONPATH='src'

# 1) 自适应（bandit 调度 + 原生攻击切片）—— 30 条快速口径
.\\.venv\\Scripts\\python.exe scripts\\benchmark_100.py --limit 30 `
    --scheduler heuristic_bandit --tag adaptive --out docs/experiments

# 2) 基线（固定顺序 + 不切片 = 改动前行为）
.\\.venv\\Scripts\\python.exe scripts\\benchmark_100.py --limit 30 `
    --scheduler fixed --no-adaptive-slicing --tag fixed-noslice --out docs/experiments

# 3) 对照汇总（命中 / 命中策略数 / 决策数 / 探索-利用 / 各臂拉取与命中）
$env:PYTHONIOENCODING='utf-8'
.\\.venv\\Scripts\\python.exe scripts\\compare_schedulers.py

# 4) 完整 100 条（约 40~60 分钟，取决于 GPU）
.\\.venv\\Scripts\\python.exe scripts\\benchmark_100.py --out docs/experiments
```

**前置依赖**（脚本顶部常量，路径不同就改这三处）：

- hashcat 7.1.2：`HASHCAT = r"F:\\SA\\tools\\hashcat-7.1.2\\hashcat.exe"`（需 `rules/` 目录）
- 规则链（两份，hashcat 自带）：
{rules}
- John the Ripper run 目录（`zip2john.exe`，评测里用不到但脚本会设置）：`JOHN_RUN`

**运行环境要求**：GPU（本机 RTX 4060 Laptop 8GB）；主机内存 ≥ 4GB 空闲
（规则链 225 万条/词，内存不足会报 `Not enough allocatable memory (RAM) for this ruleset`）。

**脚本固定配置**（想复现同样的数字就别改）：时间预算 120s、候选预算 10 亿、
批大小 100000、命中即停、词表 81 条（`data/benchmark/base-words.txt`，脚本生成）、
掩码阶梯 `?d?d?d?d,?l?l?l?l,?l?l?l?l?d?d`、混合掩码 `?d?d?d?d,?d?d,!`、
planner=`rule`、`SAGE_SEED_WORDLISTS=data/wordlists/zh-base.txt`。

## 三、用例清单（100 条，10 类各 10 条）

| 类别 | 条数 | 设计意图 |
| --- | --- | --- |
| dict+num | 10 | 词典词 + 数字/年份（`summer2023`、`dragon88`） |
| dict+symbol | 10 | 词典词 + 符号 + 大小写/leet 混合（`Dr@gon!88`、`P@ssw0rd2024`） |
| pinyin+num | 10 | 拼音 + 数字（`woaini1314`、`beijing2008`） |
| pii | 10 | 个人信息派生（姓名/昵称/生日/手机尾号/邮箱） |
| reuse | 10 | 旧口令复用（提供 `historical_passwords`，含换年份/换符号） |
| keyboard | 10 | 键盘序列（`1qaz2wsx`、`zxcvbnm!`） |
| phrase | 10 | 短语/多词（`correct-horse`、`welcometothejungle`） |
| leet | 10 | leet 替换（`Tr0ub4dor&3`、`H@ck3r2020`） |
| cn+mixed | 10 | 中文与中英混合（`密码123`、`张三@1998`） |
| hard（对照） | 10 | 高熵随机口令，**预期全部不可达**，用于确认评测没有"作弊" |

机读版本：`docs/experiments/benchmark-cases-100.csv`
（列：id / category / password / md5 / context / historical_passwords / 各配置结果）。

评测里的 PII（`pii` 与部分 `cn+mixed` 用例共用）：

```json
{pii}
```

### 明细（含本机结果）

| ID | 类别 | 口令 | {" | ".join(ARCHIVED_RUNS[label][1] for label in ARCHIVED_RUNS)} | 附带信息 |
| --- | --- | --- | {" | ".join("---" for _ in ARCHIVED_RUNS)} | --- |
{chr(10).join(rows)}

## 四、本机未命中的 35 条（完整 100 条口径）

{chr(10).join(missed_lines)}

其中 `hard(对照)` 的 10 条是**设计上的不可达样本**，可用于检查评测是否"作弊"；
其余未命中是下一步优化项，见 `docs/experiments/benchmark-100-findings.md` 的
"下一轮优化清单"（leet 叠加规则、历史口令结构变换、词表补键盘序列、组合攻击、结构模板）。

## 五、结果怎么读（复测时请按同一口径）

每个用例一行，关键列：

- `recovered` / `hit_strategy`：是否命中、由哪个策略命中（**复测重点：不应只有 S1**）；
- `tested`：本用例实际测试的候选总量；
- `stop_reason`：`all_targets_recovered`（命中即停）/ `candidates_exhausted`（候选耗尽）/
  `strategy_budgets`（各单元时间预算耗尽）；
- `native_units`：原生单元的**键空间**（S1 词表 × 规则链、S6 掩码、S7 词表 × 掩码）；
- `plan_arms`：Python 候选单元产出量；
- `reason`：未命中归因（Python 候选空间未覆盖 / 原生单元已实测仍未命中）；
- `run_status` / `run_message`：运行状态与 hashcat 失败原因（排查用）。

## 六、已知差异点（复测时容易踩）

1. **hashcat 的多个 `-r` 是规则链（乘积）**，不是并集：`best66 × d3ad0ne` 每个词
   225 万条规则，81 词 ≈ 1.8 亿键；规则表吃主机内存，三份以上大规则文件会失败。
2. **原生单元的每次启动有固定开销**（本机 5~9 秒），所以自适应切片的"探针 + 提交"
   需要足够的时间预算；30 秒预算下切片会挤掉覆盖，请用 120 秒（脚本默认）。
3. **无 GPU 时可先跑候选空间诊断**（不调用 hashcat，秒级）：
   `.\\.venv\\Scripts\\python.exe scripts\\benchmark_100.py --space-only`。
4. 复测结果请连 `--tag` 一起归档，便于和 `docs/experiments/` 里的历史结果对照。
"""
    target = REPO_ROOT / "docs" / "handoff" / "benchmark-cases.md"
    target.write_text(doc, encoding="utf-8")

    # 机读版本：把三个配置的结果并到用例 CSV 里
    csv_path = REPO_ROOT / "docs" / "experiments" / "benchmark-cases-100.csv"
    fields = [
        "id", "category", "password", "md5", "context", "historical_passwords",
        "local_hit_100", "local_strategy_100", "local_hit_30_adaptive",
        "local_hit_30_baseline",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            row100 = results.get("100", {}).get(case["id"], {})
            row_ad = results.get("adaptive30", {}).get(case["id"], {})
            row_bl = results.get("baseline30", {}).get(case["id"], {})
            writer.writerow({
                "id": case["id"],
                "category": case["category"],
                "password": case["password"],
                "md5": case["md5"],
                "context": json.dumps(case.get("context", {}), ensure_ascii=False, sort_keys=True),
                "historical_passwords": "|".join(case.get("historical_passwords", [])),
                "local_hit_100": row100.get("recovered", ""),
                "local_strategy_100": row100.get("hit_strategy", ""),
                "local_hit_30_adaptive": row_ad.get("recovered", ""),
                "local_hit_30_baseline": row_bl.get("recovered", ""),
            })
    print("写出", target)
    print("写出", csv_path)
    print("本机 100 条口径命中:", sum(1 for row in results.get("100", {}).values() if row.get("recovered") == "True"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
