# 第五步：UCB、在线成本模型与 Cost-aware UCB

后续更新：成本标定、近期窗口预测、盐条件与回放 v3 已补齐，当前行为以 [成本标定器与盐条件模型](calibration-and-salts.md) 为准；以下保留第五步初次交付的方法说明。

## 借鉴来源

| 原始来源 | 借鉴内容 | 本项目的变化 |
|---|---|---|
| Auer、Cesa-Bianchi、Fischer，2002，[Finite-time Analysis of the Multiarmed Bandit Problem](https://link.springer.com/content/pdf/10.1023/A:1013689704352.pdf)，Figure 1 | UCB1 的经验均值加探索项、未试 Arm 先探索 | 一次拉臂对应执行一批，学习奖励采用本批全局新增目标数除以初始目标数；加入 Arm 可用性与双硬预算 |
| Tran-Thanh 等，2012，[Knapsack based Optimal Policies for Budget-Limited Multi-Armed Bandits](https://arxiv.org/abs/1204.1909)，§3.3 | fractional KUBE 的收益上置信界/成本评分 | 分母改为在线预测的当前批次耗时；成本未知且批量可变，因此称为项目的 Cost-aware UCB 适配，未声称完整复现 fractional KUBE |
| Lilian Besson，[SMPyBandits Policies.UCB 源码页](https://smpybandits.github.io/_modules/Policies/UCB.html)，`computeIndex` | 核对累计收益/拉臂次数的索引实现及冷启动约定 | 使用本项目的接口与统计结构；不引入 NumPy、不采用 infinity 日志值，冷启动单独处理 |

查阅日期：2026-09-16。SMPyBandits 的[仓库许可证](https://github.com/SMPyBandits/SMPyBandits/blob/master/LICENSE)为 MIT。本次只参考公式和实现约定，没有复制仓库源码、没有新增依赖。在线非负线性成本拟合、序列化、隐私投影、预算接入及测试为本项目实现。代码中亦注明参考出处。

## 1. 两种 UCB 的学习奖励

```text
y_t = 本批全局新增恢复目标数 / 初始目标数              # [0, 1]
mean_i = sum(y_i) / n_i
bonus_i = c × sqrt(log(max(1, 已完成批次数)) / n_i)
upper_i = mean_i + bonus_i

UCB:            score_i = upper_i
Cost-aware UCB: score_i = upper_i / max(minimum_cost, predicted_seconds_i(b))
```

默认 `c=sqrt(2)`。先从仍可执行的未试 Arm 中按优先级、Arm ID 排序选择；之后取最高分，同分仍按该稳定顺序。耗尽或预算不足的 Arm 不参与选择。初始探索标记 `untried=1`，日志中的尚未估计分数为有限值零，选臂逻辑明确优先处理未试 Arm。

学习奖励和第四步评价奖励用途不同：`learning_reward` 用于 UCB；`reward` 与 `reward_breakdown` 继续统一报告目标、时间、候选和重复项。成本感知分母使用执行秒数，候选预算保持独立硬约束，没有再给学习奖励叠加时间惩罚。

学习以批次为单位；奖励上界可大于 1，不剪裁探索项。收益不与候选数作 Bernoulli 对应，所以同一候选恢复多个目标不会被拒绝。实际新增目标仍由执行环境全局去重；调度器只校验累计新增量不超过初始目标数，不能仅凭计数识别调用方误报的重复目标。

## 2. 在线成本模型

`decision/costs.py` 为每个运行内的每个 Arm 保存独立模型：

```text
预测耗时(b) = 启动开销 a + 每候选耗时 k × 提交批量 b
a >= 0, k >= 0
```

初始值：`a=0 秒`，`k=0.01 秒/候选`，预测下限 `1e-6 秒`。这是可配置的冷启动先验，不是实测标定值，也不从回放环境的隐藏成本参数读取。

完整成功批次在线累计 `n, sum_x, sum_y, sum_xx, sum_xy`，使用非负最小二乘的二维解析候选解（内部解及两个边界解）选择误差最小者，不保存逐样本历史。批量没有变化时无法同时识别 a/k：固定启动项为先验与实测平均耗时中的较小值，仅估计斜率。这一限制会记录在方法说明中，不能把单一批量下的两项当成分别已标定。

只有 `status=completed` 且 `tested=candidate_count` 的批次进入拟合。部分测试、失败或取消归入 `censored_samples`；它们仍更新已消耗预算与本批学习奖励，但不被当成低成本完整样本。全部候选已测完的零耗时反馈可进入拟合，预测下限防止除零。

这是保守排除非完整样本的实现，不是删失数据统计模型：长期超时可能导致成本模型停留在先验或旧估计，完整样本也可能有选择偏差。未实现启动/内核耗时的独立测量、跨运行成本迁移或特征回归。每个运行处理固定目标组，算法、设备等影响由该运行实际批次反馈间接反映。

预测只用于排名，不用于提前认定某臂“不可执行”，也不缩小提交批量；仍由原有硬预算裁剪批量和执行时限。这样不会让不准的冷启动先验使某个 Arm 永远失去探索机会。时间记账沿用执行器的批次 duration，不含生成、日志写入及 Web 请求耗时。

## 3. 接口、日志与恢复

- `build_scheduler(..., initial_targets=N, ucb_config=UCBConfig(...))` 新增支持 `ucb` 与 `cost_aware_ucb`。N 必须为正整数，不能猜成一个目标。
- `BaselinePolicy` 继续提供统一的 select/observe/diagnostics/snapshot/restore；名称沿用已有接口，现支持五种算法。
- 真实执行对两种新算法传递目标级 `BatchOutcome`，保持原有三基线更新语义。
- 评分分解新增 `mean_reward / exploration_bonus / upper_bound / predicted_seconds / cost_samples / censored_samples / untried`。旧字段 `success_probability/transfer` 在 UCB 中为零占位；`cost` 为预测秒数，不是旧启发式的预算消耗比例。
- 状态新增目标收益累计量、成本样本数、删失样本数、预测启动项/斜率。研究日志仍不保存任何候选或恢复明文。
- 完整 UCB 快照包括静态配置、实际资源统计、目标收益与成本充分统计量。恢复先完整校验后替换；不同目标数、预算、Arm、算法模式或先验配置不能混用。
- 真实执行检查点新增 `scheduler_snapshot`；优先用完整快照恢复，也保存旧 `scheduler_stats` 摘要。旧三基线可回退旧摘要；执行过的 UCB 缺少完整快照时明确拒绝，防止悄悄丢失学习状态。该接入也保留了新 RR 快照的游标。
- 回放检查点仍为第四步的 version 2，UCB 配置包含在内层 policy 配置中；前缀重建保持同种子并不重复落盘。旧三基线的第四步 v2 轨迹字段保持兼容。

## 4. 运行方式

比较全部五种算法（`--policy all` 为默认值）：

```powershell
python -m sage_pass.experiments --scenario examples/replay/baseline-small.json --output data/replay/step5-comparison.json --ucb-config examples/replay/ucb-config.json
```

另提供 `examples/replay/cost-aware-small.json`：两条有限候选流、不同人工耗时与紧时间预算。可替换上述 scenario 路径观察成本对选择的影响；它是合成说明场景，不是训练数据、真实设备标定或性能结论。环境的 `arm_costs` 不会作为先验泄露给策略。

单独运行并导出完整研究日志（导出路径必须尚不存在）：

```powershell
python -m sage_pass.experiments --scenario examples/replay/baseline-small.json --policy cost_aware_ucb --output data/replay/cost-ucb.json --export-research data/replay/cost-ucb-events.jsonl
```

检查点参数沿用 `--max-steps / --checkpoint / --resume`，续跑提供相同 `--ucb-config`。为保持原库调用兼容，`compare_baselines(scenario)` 仍返回三个旧基线；传 `include_ucb=True` 返回五种。

真实服务启用新调度器后重启进程：

```powershell
$env:SAGE_SCHEDULER_TYPE = "cost_aware_ucb"
python -m uvicorn sage_pass.main:app --app-dir src --reload
```

也可设置为 `ucb`。自定义真实运行超参数通过 `RealExecutor(..., ucb_config=UCBConfig(...))` 注入；每次新运行将配置冻结在运行快照中，恢复不受服务默认值变化影响。当前未增加公共 HTTP 配置或页面选择器。

## 5. 理论与实验边界

SAGE 的候选流有序、彼此重叠、会耗尽，已恢复目标还会减少后续收益；可变批量使各次拉臂也不完全同分布。因此这里使用 UCB1 公式作为实验基线，不声明其经典平稳独立模型的对数遗憾界在本项目成立。成本感知版本额外使用了学习得到的成本点估计，也不是带成本置信区间的算法。

比较时固定目标组、候选流、初始预算、批量配置、种子与奖励权重，报告恢复数/实际耗时/提交量及统一评价奖励。批量变化特别大时，历史每批收益和当前批量预测成本可能不匹配；这是本版批次建模的限制。冷启动先验与 c 应在预实验/验证集调节后固定，当前没有实测性能优于旧基线的结论。
