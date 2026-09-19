"""生成《100 条口令评测：用例清单与复现方式》文档（把评测用例交给队友复测）。"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def load_benchmark_module():
    spec = importlib.util.spec_from_file_location(
        "bench", REPO_ROOT / "scripts" / "benchmark_100.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_benchmark_module()
    cases = module.corpus()
    rows = []
    for case in cases:
        extra = "—"
        if case.get("context"):
            extra = "PII（见下方）"
        if case.get("historical_passwords"):
            extra = "历史口令：" + "、".join(case["historical_passwords"])
        rows.append(
            f"| {case['id']} | {case['category']} | `{case['password']}` | "
            f"`{case['md5']}` | {extra} |"
        )
    pii = json.dumps(module.PII, ensure_ascii=False, indent=2, sort_keys=True)
    rules = "\n".join(f"- `{Path(path).name}`" for path in module.RULE_FILES)
    doc = f"""# 100 条口令评测：用例清单与复现方式（交给队友复测）

> 这份评测回答两个问题：**系统能解出多少"不那么常见"的口令**、**命中是否只来自基线单元（S1）**。
> 语料与词表都由 `scripts/benchmark_100.py` 内置生成（确定性、可复现），不需要额外准备数据。

## 一、怎么跑（最短路径）

```powershell
git fetch origin
git checkout feat/adaptive-native-slicing        # 自适应切片分支；基线行为在 main 上也有
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

## 二、用例清单（100 条，10 类各 10 条）

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
（列：id / category / password / md5 / context / historical_passwords）。

评测里的 PII（`pii` 与部分 `cn+mixed` 用例共用）：

```json
{pii}
```

### 明细

| ID | 类别 | 口令 | MD5 | 附带信息 |
| --- | --- | --- | --- | --- |
{chr(10).join(rows)}

## 三、预期结果（本机实测，可用于对照）

### 30 条快速口径（C001–C030：dict+num / dict+symbol / pinyin+num）

| 指标 | 自适应（bandit + 切片） | 基线（固定顺序 + 不切片） |
| --- | --- | --- |
| 命中 | 25/30 | 25/30 |
| 命中来源策略 | S1 12、S7 5、S5 4、S3 3、S2 1（**S1 占 48%**） | S1 23、S2 1、S3 1（**S1 占 92%**） |
| 平均耗时 | 32.3s | 14.3s |
| 决策数（探索/利用） | 233（207/26） | 139（46/93） |
| 归档 | `docs/experiments/benchmark-100-20260919-1042-adaptive.*` | `docs/experiments/benchmark-100-20260919-1049-fixed-noslice.*` |

### 完整 100 条（main 上的 `79e0cbd`，规则链 best66 × d3ad0ne，时间预算 60s）

| 类别 | 命中 | 类别 | 命中 |
| --- | --- | --- | --- |
| dict+num | 10/10 | keyboard | 7/10 |
| pinyin+num | 10/10 | phrase | 7/10 |
| pii | 10/10 | cn+mixed | 6/10 |
| reuse | 9/10 | dict+symbol | 5/10 |
| | | leet | 1/10 |
| | | hard（对照） | 0/10 ✅ |

合计 **65/100**，命中来源 S1 51、S4 10、S2/S3/S5/S7 各 1~2；
归档：`docs/experiments/benchmark-100-20260919-0316.*`。

## 四、结果怎么读（复测时请按同一口径）

每个用例一行，关键列：

- `recovered` / `hit_strategy`：是否命中、由哪个策略命中（**复测重点：不应只有 S1**）；
- `tested`：本用例实际测试的候选总量；
- `stop_reason`：`all_targets_recovered`（命中即停）/ `candidates_exhausted`（候选耗尽）/
  `strategy_budgets`（各单元时间预算耗尽）；
- `native_units`：原生单元的**键空间**（S1 词表 × 规则链、S6 掩码、S7 词表 × 掩码）；
- `plan_arms`：Python 候选单元产出量；
- `reason`：未命中归因（Python 候选空间未覆盖 / 原生单元已实测仍未命中）；
- `run_status` / `run_message`：运行状态与 hashcat 失败原因（排查用）。

## 五、已知差异点（复测时容易踩）

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
    print("写出", target, len(cases), "条用例")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
