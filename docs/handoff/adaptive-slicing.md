# 自适应切片：让"实时按情况换策略"真正生效

> 分支：`feat/adaptive-native-slicing`（未合并 main）
> 相关代码：`src/sage_pass/keyspace.py`、`hashcat_adapter.py`、`real_executor.py`、`config.py`
> 对照脚本：`scripts/benchmark_100.py --scheduler {heuristic_bandit|fixed} [--no-adaptive-slicing]`

## 1. 问题（100 条评测暴露）

最终轮 100 个用例共 332 个调度决策，**全部是 `exploration=true`**（利用阶段 0 次），
65 个命中里 **51 个来自首个批次**（S1 一拉 1.73 亿键），其余单元几乎没有机会参与：

- 原生攻击（S1 词表 × 规则链 / S6 掩码 / S7 词表 × 掩码）**一次拉完整段键空间**，
  调度器每轮只有一个决策点，拿不到"这一片值不值得继续投入"的信号；
- Python 候选单元（S2/S3/S5）流很浅（几千条），很快 `candidates_exhausted`，
  剩余时间预算无处可花。

结论：不是调度算法不行，而是**候选供给粒度太粗**，自适应没有可用的决策点。

## 2. 做法

把原生单元切成**多个可观测的小批次**，每批之后调度器都能按实测产出重新选臂。

| 单元 | 切片方式 | 依据（实测） |
| --- | --- | --- |
| S1 词表 × 规则链 | **切片词表文件**（本批次只喂这一段的词） | hashcat 的 `-s/-l` 与"多个规则文件 / 掩码文件"冲突：`Use of --skip/--limit is not supported with --increment, mask files, multiple dictionaries, or --stdout.`（实测 S7 因此整体失败）。切片成独立词表后就是普通单字典攻击，规则链与掩码文件都不受限，且切片键空间 = 文件行数 × 放大倍数 |
| S7 词表 × 掩码 | 同上（掩码阶梯整体保留，按词切） | 放大倍数 = Σ 掩码键空间 |
| S6 掩码阶梯 | **拆子掩码**：`?d?d?d?d` → `0?d?d?d`… | 掩码的 `-s/-l` 是 base/mod 语义（实测 `?d?d?d?d -s 0 -l 100` 连首候选都没测到），只能拆掩码 |
| S2/S3/S4/S5 | 维持流批次 | 已有能力 |

切片大小策略（`SAGE_ADAPTIVE_*`）：

- 首批 = **探针**：按"目标 GPU 工作时长"估算（`adaptive_probe_seconds` × 实测速率），
  未知速率时用 `adaptive_probe_keys`（默认 200 万），且不超过整段的 `1/probe_divisor`；
- 被重新调度时**提交剩余全部**（受该单元剩余时间预算约束，hashcat 用 `--runtime` 截断）；
- 之后若仍被重新调度，按 `adaptive_growth`（默认 8）继续放大；
- 最小切片 = `max(adaptive_min_slice_keys, 一个词的放大倍数)`：**一条词是不可分单位**，
  切片小于它就等于跳过该单元（真实故障：探针 200 万键 < 一个词的规则链 225 万键，
  导致整个运行 0 命中）。

两个关键正确性点：

1. **切片进度按实测条数推进**：hashcat 可能被时间预算截断，若按"请求的切片"推进就会
   **跳过没测过的词/掩码**；现在用 `tested // 放大倍数` 推进（保守：允许重测被截断的那一条）。
2. **记账不变量保持**：`tested ≤ 提交量 ≤ 该批次分配预算`（切片键空间 ≤ 分配量；
   退化到"一条词"时可能略超批次分配量，但仍在该单元的预算内）。

### 重要环境事实：规则链的每次启动固定开销很大

本机实测（RTX 4060 Laptop + `best66 × d3ad0ne` = 225 万条规则/词）：

- 单次 `hashcat` 启动 + 规则链加载 ≈ **5～9 秒**（几乎与"测试 225 万键"同量级）；
- 因此"探针 + 提交"两批 ≈ 2 次启动开销，只有当**单元时间预算 ≥ 约 2 倍固定开销**时才划算；
- 评测脚本据此把整轮时间预算提到 120 秒（每个单元约 24 秒），让探针与提交都有空间；
  时间预算很紧（如 30 秒）时切片会挤掉覆盖，此时应关闭切片（见下"开关"）。

## 3. 验证

单元/集成测试（新增 `tests/test_adaptive_slicing_keyspace.py`、
`tests/test_adaptive_slicing_executor.py`，共 16 项）：

- 掩码拆分可预测（含字面量前缀、自定义字符集、不可拆退化、切片数上限，且**不丢键空间**）；
- 词区间切片计数（最小 1 条、不越过词表末尾）；
- 适配层 `-s/-l` argv 与非法用法（缺 limit、skip≥limit、无词表）报错；
- 执行层：S1 按"探针 → 放大"切片且**不重不漏**；每批 `tested ≤ 提交量 ≤ 分配预算`；
  S6 拆成可预测子掩码；**被截断时按实测条数推进**；关闭开关退回"一次跑完"。

端到端对照（同一语料、同一配置，仅调度器/切片不同）：见下节。

## 4. 端到端对照（30 条口径，时间预算 120s）

命令：

```powershell
# 自适应（bandit + 切片）
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\benchmark_100.py --limit 30 `
    --scheduler heuristic_bandit --tag adaptive --out docs/experiments
# 基线（固定顺序 + 不切片 = 改动前行为）
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\benchmark_100.py --limit 30 `
    --scheduler fixed --no-adaptive-slicing --tag fixed-noslice --out docs/experiments
# 对照汇总（命中 / 命中策略数 / 决策数 / 探索-利用 / 各臂拉取与命中）
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe scripts\compare_schedulers.py
```

| 指标 | 自适应（bandit + 切片） | 基线（固定顺序 + 不切片） |
| --- | --- | --- |
| 命中 | **25/30** | **25/30** |
| 命中来自的策略数 | **5**（S1 12、S7 5、S5 4、S3 3、S2 1） | **3**（S1 23、S2 1、S3 1） |
| 基线占比 | S1 占 48% | **S1 占 92%** |
| 平均耗时 | 32.3s | 14.3s |
| 决策数（探索/利用） | 233（207/26） | 139（46/93） |
| 累计测试候选 | 13.1 亿 | 19.5 亿 |
| 归档 | `docs/experiments/benchmark-100-20260919-1042-adaptive.*` | `docs/experiments/benchmark-100-20260919-1049-fixed-noslice.*` |

结论：

1. **"不再只靠基线解码"达成**：命中来源从"S1 占 92%"变为"S1 占 48%"，
   其余由 S7（词表 × 掩码）、S5（迁移）、S3（统计）、S2（规则）贡献；
2. **命中率不降**：同一批 30 条口令两侧都是 25/30（用 120 秒预算；30 秒预算下切片会挤掉覆盖）；
3. **代价是延迟**：平均 32.3s vs 14.3s。原因是每个原生单元多一次"探针"启动，而本机
   规则链单次启动就要 5～9 秒；时间预算紧时应关掉切片（或调大 `adaptive_probe_seconds`
   让探针更"值钱"）。
4. 决策结构也变了：基线几乎只在"利用"（93 次）而探索只有 46 次；自适应探索 207 次、
   利用 26 次——因为每个单元都被切成多片，调度器每片之后都在重新评估。

## 5. 开关与调参

```powershell
SAGE_ADAPTIVE_SLICING=true        # 默认开启；false = 回到"一次拉完整段键空间"
SAGE_ADAPTIVE_PROBE_SECONDS=1.0   # 探针目标 GPU 工作时长（按实测速率换算成键空间）
SAGE_ADAPTIVE_PROBE_KEYS=2000000  # 未知速率时的探针键空间
SAGE_ADAPTIVE_PROBE_DIVISOR=8     # 探针不超过整段的 1/N
SAGE_ADAPTIVE_MIN_SLICE_KEYS=100000
SAGE_ADAPTIVE_GROWTH=8.0          # 探针之后的放大倍数
SAGE_SCHEDULER_TYPE=heuristic_bandit  # 自适应调度；fixed 为固定顺序基线
```

