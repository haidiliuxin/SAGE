# 第 3 步优化：原生攻击记账 + 词表 × 规则（100 条评测）

> 适用范围：`SAGE_PLANNER_TYPE=rule`，hashcat 7.1.2，本地 RTX 4060 Laptop（8GB 显存）。
> 相关代码：`src/sage_pass/keyspace.py`、`planner.py`、`real_executor.py`、
> `hashcat_adapter.py`；评测脚本：`scripts/benchmark_100.py`。

## 1. 本轮解决的问题

### 1.1 原生攻击的候选记账（整轮运行被中断的根因）

现象：

```
内部错误：tested must be between zero and candidate_count（strategy=S7 tested=8100 candidate_count=2000）
```

- 调度层（`BanditScheduler.observe` / `UCBScheduler.observe`）要求
  `tested ≤ candidate_count ≤ 该单元分配预算`；
- 但 S6（`-a 3` 掩码）、S7（`-a 6` 词表 × 掩码）、S1（词表 × 规则）的候选由 hashcat
  自己枚举，真实测试量是**键空间**：81 词 × `?d?d` = 8100，而计划按权重只给该单元
  2000 的 Python 候选预算；
- 旧实现用 `min(tested, 预算)` 夹取记账，反而让 `tested > candidate_count`，于是整个
  运行被一个纯记账问题中断（`state.failed_launch`）。

同源的第二个问题：hashcat 适配层把"掩码个数"当作候选量，导致掩码作业的实测条数被夹到
1～2 条（S6 只记 2 条候选），计划/统计完全失真。

### 1.2 修复（三层，各司其职）

| 层 | 位置 | 做法 |
| --- | --- | --- |
| 键空间模型 | `keyspace.py` | `-a 3`：掩码乘积；`-a 0`：词表条数 × 规则条数；`-a 6/7`：词表条数 × 掩码键空间。支持 `?l/?u/?d/?s/?a/?b/?h/?H`、`?1..?4` 自定义字符集、`??` 字面量；无法估算返回 `None`（保守）。行数统计刻意取上界。 |
| 计划层 | `planner._fit_native_units()` | 键空间精确可知 → **优先满足原生单元**（按各自键空间预留预算），剩余预算再按权重分给 Python 生成单元；单靠自己就超出任务候选预算的掩码直接忽略（永远跑不起来）；仍装不下时按预算裁剪掩码阶梯并写入计划警告。 |
| 执行层 | `real_executor._fit_job_to_budget()` | 启动前兜底：掩码单元裁剪掩码阶梯；词表单元用 `hashcat -l/--limit` 截断词表条数（`-l` 限制**词表条数**，规则/掩码按倍数放大，因此按倍数反算安全条数）；确实装不下时**优雅跳过该单元**并写明原因，不再中断整个运行。 |
| 记账 | `real_executor._record_batch_result()` | `candidate_count = max(候选数, 实测数, 1)`，保证 `tested ≤ candidate_count ≤ 分配预算`。 |
| 适配层 | `hashcat_adapter` | `candidate_count` 改用键空间（掩码/词表 × 规则不再被夹取）；`tested` 按键空间上界夹取；批次工作目录对原生单元同样保留（`session.json` 含 argv，`stdout.log`/`stderr.log` 可复盘）。 |

### 1.3 词表 × 规则落到 S1（优化清单第 1 项）

- `RulePlanner` 现在把 `hashcat_rule_files` 写进 **S1**：
  `hashcat -a 0 <dict.txt> -r best66.rule`，规则作用在**外部词表**上；
- S2 回退为"Python 规则变形候选"，不再叠加 `-r`（避免规则二次作用于已变形的候选，
  也避免键空间虚高 66 倍）；
- 混合掩码加入 `?d?d?d?d`，覆盖"词 + 年份"（`summer2023`、`coffee2024` 这类）。

### 1.4 规则链（多个 `-r` 是乘积，不是并集）

实测发现（hashcat 7.1.2）：`-r best66.rule -r dive.rule` 会打印 `Rules: 6512220` =
66 × 98670，键空间 = 词表条数 × 6512220（81 词 → **5.27 亿**）。也就是说多个规则文件是
**规则链**（先按第一个文件的规则变换，再按第二个继续变换），不是并集；把两份规则合并成
一个文件才是并集（`Rules: 98736`，键空间 800 万）。

这一条直接影响三件事，本轮都做了处理：

1. **键空间模型**（`keyspace.rule_chain_count()`）：多个规则文件相乘，单个文件等价于该文件
   的规则条数。适配层与执行层都用它，避免"以为测了 800 万、实际测了 5.27 亿"的记账失真。
2. **计划层的降级策略**：S1 的规则链是乘积，很容易超出任务候选预算；装不下时按预算
   **缩短规则链**（保留"乘积能装下"的前缀，最短退化为纯词表），并在计划警告里写明，
   而不是直接放弃主力攻击。
3. **时间预算**：规则链的键空间是亿级（2.25 亿键 ≈ 9 秒纯 GPU 时间，5.27 亿键 ≈ 21 秒），
   整轮时间预算 30 秒时 S1 只分到 20%（6 秒），会被截断在键空间中途——评测脚本因此把
   时间预算提到 60 秒（S1 = 12 秒）。


## 2. 验证方式

```powershell
# 单元测试（键空间解析、计划层预算分配/裁剪、执行层 -l 截断与越界跳过、适配层记账）
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest -q tests/test_native_keyspace_budget.py
# 全量
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m pytest -q
# 单案例诊断（打印计划预算/参数与逐单元实测）
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\diag_single_case.py --password summer2023
# 从评测库读逐单元结果
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\dump_run_arms.py
# 100 条真实评测
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\benchmark_100.py --out docs/experiments
```

评测配置：时间预算 60s、候选预算 10 亿、批大小 100000、命中即停、词表 81 条、
规则链 `best66.rule × d3ad0ne.rule`、掩码阶梯 `?d?d?d?d,?l?l?l?l,?l?l?l?l?d?d`、
混合掩码 `?d?d?d?d,?d?d,!`。

## 3. 已知限制

- PCFG grammar（`pcfg_full`）仍缺训练数据（优化清单第 7 项未完成的一半）；
- 纯掩码作业（`-a 3`）无法用 `-l` 精确截断（hashcat 的 `-l` 对掩码按 base 计数，
  倍数与掩码内部结构相关），因此改用"裁剪掩码阶梯"保证不越预算；
- 原生单元的键空间估算取**上界**（规则文件里的注释/空行也计入），只会更保守，
  不会导致越预算。
