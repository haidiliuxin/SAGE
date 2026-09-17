# 第四步：统一奖励与完整研究日志

## 1. 借鉴来源与实现边界

| 来源 | 本项目采用的内容 | 不作出的假设或声明 |
|---|---|---|
| Badanidiyuru、Kleinberg、Slivkins，[Bandits with Knapsacks](https://arxiv.org/abs/1305.2545)，§1.1 | 将收益、候选消耗、时间消耗分别保留；硬预算独立于奖励 | 本项目的有序候选、重叠和耗尽不满足其平稳独立采样模型；本步没有复现 BwK 算法或理论界 |
| Saito 等，[Open Bandit Dataset and Pipeline](https://arxiv.org/abs/2008.07146)，§2.1；[官方代码](https://github.com/st-tech/zr-obp) | 明确记录决策前观测、动作、反馈、行为策略选择概率及实验配置 | Arm 评分不是选择概率；确定性日志不能支持任意策略的反事实评估 |
| Dudík、Langford、Li，[Doubly Robust Policy Evaluation and Learning](https://arxiv.org/abs/1103.4601) | 为未来评估区分行为概率与预测分数 | 本步没有实现 DR/IPS，也没有声称普通日志回放就是无偏 OPE |

奖励公式沿用本项目第三步数学模型，权重是实验初值；SQLite 存储、隐私投影和恢复分段是本项目的工程实现。未复制上述仓库源码，也没有新增第三方依赖。此前提及的 `sb-ai-lab/sb-obp` 是分叉仓库，官方项目以本表链接为准。

## 2. 奖励口径

`decision/rewards.py` 提供纯函数 `calculate_reward`、不可变配置 `RewardWeights`、`RewardContext` 和完整结果 `RewardBreakdown`。

```text
reward = recovery × 本批全局新增恢复目标数 / 初始目标数
       - time × 本批实际执行秒数 / 初始总时间预算
       - candidates × 本批提交候选数 / 初始总候选预算
       - duplicates × 本批重复数 / max(1, 本批扫描条目数)
```

默认权重依次为 `1.0 / 0.1 / 0.1 / 0.1`，版本为 `target-budget-v1`。

- 分母始终使用初始预算；改变剩余预算不会改变相同反馈的奖励解释。
- `recovered` 表示全局新增目标数，可大于 `tested`：一个候选可能恢复多个目标。
- 提交但未完成测试的候选仍扣候选预算；日志分别保存 submitted 与 tested。
- 实际超时按实际耗时记录；时间比例可大于 1，负奖励不截断。奖励不能代替硬预算判断。
- 重复惩罚只在重复量确实可观测时计算。回放可测量；真实生成器目前在执行前去重，批次反馈缺少原始扫描量，因此记录 `null` 和 `duplicate_measurement_available=false`，标量中暂时省略此项，绝不伪称实测为零。
- 同时记录每千提交候选收益和每秒收益；零耗时的每秒收益为 `null`。
- 权重在运行开始时固定并随日志保存。真实运行把权重存入运行快照；回放检查点拒绝不同权重的恢复。

例如初始 10 个目标、100 个候选、20 秒，本批提交并测试 1 条、新增恢复 2 个目标、耗时 2 秒，同时跳过 1 条重复，则奖励为 `0.2 - 0.01 - 0.001 - 0.05 = 0.139`。

重复比例逐批求和受批次划分影响；比较不同批次大小时仍以恢复目标数和资源曲线为主要指标。真实与回放的去重归属规则原本也不同：跨模式比较应先统一规则与测量口径；缺少真实重复量时，将两侧重复权重统一设为零，并披露缺测。

## 3. 每轮记录什么

`decision/research_log.py` 的 `ResearchRecorder` 只输出白名单字段，`SqliteResearchLog` 负责事务落盘。

外层字段：`schema_version=1`、`run_id`、`attempt_id`、`round_index`、`event_type`；读取/导出时附加全库递增 `sequence`。

| 事件 | 内容 |
|---|---|
| `run_started` | 模式、策略类型/版本/参数、奖励版本/权重、初始预算与目标数、种子、续跑起点 |
| `decision_started` | 可用 Arm、预算裁剪后的可用批量、决策前统计/剩余预算、各可用 Arm 的完整评分分解、选择的 Arm/批量/时限、选择规则/概率、前一 Arm 与切换原因 |
| `decision_completed` | 上述决策 + 实际反馈、奖励分解、更新后统计/剩余预算、停止原因 |
| `decision_interrupted` | 正常异常处理能捕获的未完成决策；反馈和奖励为 `null`，结果标为未知 |
| `checkpoint` | 业务检查点成功保存后的已提交轮次标记 |
| `run_finished` | 预算耗尽、候选耗尽、取消、执行失败等固定原因码与最终统计 |

不可用 Arm 在 `available_batches` 中为零，不计算无意义的候选评分。Fixed/Round Robin 的分数仅供诊断，`scores_used_for_selection=false`；实际规则分别是优先级与轮转。现有三种基线都是确定性策略，所选动作概率为 `1.0`，不是将评分归一化得到的数值。未来随机策略必须提供真实概率，不能复用这个默认值。

研究日志不保存候选文本、目标哈希/目标 ID、恢复明文、个人信息、自由文本 message、stdout/stderr、完整业务对象或原始检查点。Arm/run 等标识应使用系统生成的不敏感 ID。参数仅接受预定义数值字段；原始错误信息只走已有业务错误渠道。

## 4. 长实验、异常和续跑

SQLite 是完整事件的权威存储，每个事件独立事务提交，`synchronous=FULL`。同一 `(run_id, attempt_id, round_index, event_type)` 的相同重试幂等，不同内容拒绝覆盖。数据库可由多个执行线程共享，记录器本身遵循单运行单批次顺序。

批次启动前先写 `decision_started`，失败则不启动该批。完成事件写入失败会使真实运行失败，不继续执行下一批。内存最多 200 条、业务检查点最近 50 条的旧摘要仍保留，完整历史由独立研究库保存，没有这两个条数限制。

正常错误路径写入终态；进程被强杀或机器断电时无法承诺写出终态，未配对的开始记录表示结果未知。日志、Hashcat 外部执行、业务运行检查点属于不同提交边界，没有跨三者的原子事务保证。磁盘不可写时也无法承诺新增事件持久化。

每次续跑生成新的 `attempt_id`，携带 `resume_from_round`，保留旧尝试。回放恢复校验历史前缀时不会向外部日志库重复写入。业务检查点落后于已执行批次时，新尝试可能再次出现相同逻辑轮次；统计时按尝试与恢复链分析，不能把所有尝试简单相加。旧研究历史缺失时，恢复只记录之后的过程，不伪造历史。

完整回放报告仍包含全部轨迹，内存占用随轮数增长；SQLite 写入组件仅保留当前待完成批次，JSONL 导出逐条读取。运行检查点是受控内部状态，可能包含候选/目标标识，不能作为脱敏研究日志发布。

## 5. 使用方法

回放默认把日志写到输出报告同目录的 `<报告名>.research.sqlite3`。重复运行会追加新尝试。可指定库、奖励配置以及新建 JSONL 导出文件：

```powershell
python -m sage_pass.experiments --scenario examples/replay/baseline-small.json --output data/replay/step4.json --research-log data/replay/step4.sqlite3 --reward-weights examples/replay/reward-weights.json --export-research data/replay/step4.jsonl
```

断点与续跑（路径不可相同；续跑用相同场景、策略、奖励配置）：

```powershell
python -m sage_pass.experiments --scenario examples/replay/baseline-small.json --policy round_robin --max-steps 2 --checkpoint data/replay/partial-checkpoint.json --output data/replay/partial.json --research-log data/replay/resume.sqlite3
python -m sage_pass.experiments --scenario examples/replay/baseline-small.json --policy round_robin --resume data/replay/partial-checkpoint.json --output data/replay/resumed.json --research-log data/replay/resume.sqlite3
```

回放检查点升为 **version 2**，增加奖励配置与新轨迹；第三步的 version 1 检查点明确拒绝，需重新生成。`--max-steps` 表示主动截取前缀，不会伪造任务完成事件。

真实执行默认日志路径为 `settings.upload_dir.parent / "research" / "real.sqlite3"`。构造 `RealExecutor` 时可注入 `research_log` 和 `reward_weights`。独立导出命令只读打开已有库，导出目标必须尚不存在：

```powershell
python -m sage_pass.experiments.export_research --database data/research/real.sqlite3 --output data/replay/real-export.jsonl
# 可加 --run-id <实际run_id> 只导出一个运行；使用自定义 upload_dir 时对应调整数据库路径。
```

库级调用 `ReplayEnvironment(..., research_log=SqliteResearchLog(path))` 才开启落盘；不传 store 仍可生成内存报告，方便单元测试。CLI 和真实执行入口默认开启落盘。

## 6. 与第五步及 A/C 的边界

第五步现已实现，具体接入、学习奖励及成本模型见 [UCB 与成本模型](ucb-and-cost.md)。本节其余内容说明第四步原有边界。

统一奖励已接入真实执行的事件摘要和回放报告。现有基线的内部学习/更新公式不变：这是统一评价口径，不是声称三种旧基线已经改成新奖励学习算法。真实引擎状态里的 `recovered` 仍为旧统计；研究反馈里的 `recovered` 是全局新增目标，两者不要混用。

真实执行已为研究奖励对目标去重；原有多目标 Hashcat progress 单位、Bernoulli 命中计数适配、批内后缀保留及生产 RR 检查点游标等仍按接口对齐文档推进，不因新增日志而自动解决。回放已具备对应的有限流与独立目标计数口径。

A 后续接页面最近事件摘要，可读取完成事件中的 `payload`，无需把全量历史塞进运行接口。C 若提供每批 `duplicate_count/offered_count`，需保持二者与实际提交前缀对应。第五步实现 UCB/成本模型时再接入新学习规则，不能直接把可为负、可能超界的本奖励当作经典 `[0,1]` UCB 奖励而沿用原置信界。
