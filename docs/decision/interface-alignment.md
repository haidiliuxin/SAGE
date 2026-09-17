# B 第一阶段接口对齐方案

本文件用于 A、B、C 集成评审。依据《任务安排.docx》《BC参考(1).docx》和当前 0.4.0 代码，列出已实现的 B 侧适配与仍需集中接线的公共接口变更。文档中的建议字段尚不等于团队已批准的公共契约。

## 本次交付范围

- B 新增 `decision/` 兼容层和 `experiments/` 回放环境。
- 复用 `scheduler.py` 的 Fixed Order、Round Robin、Heuristic Bandit；新增 Round Robin 规范快照中的轮转游标。
- 对外业务策略 ID 与适配器调度 Arm ID 分离；原 HTTP API、数据库和 RealExecutor 接线由 A 后续集成。
- 当前回放支持一个目标组中的单目标或多目标、有限候选流、全局去重、提交候选预算、执行时间预算、耗尽停止和批次边界恢复。

## 责任与依赖

| 接口或行为 | B 提供 | A 接线 | C 配合 |
|---|---|---|---|
| Arm 身份 | `DecisionArm`、业务 ID 与调度 ID 映射 | 公共 ArmSpec、运行状态、结果聚合、快照迁移 | generator_id、参数版本、候选游标 |
| 选批与反馈 | `BaselinePolicy`、`PolicyDecision`、反馈校验 | 统一调用 DecisionPolicy 方法 | 按指定候选数取批 |
| 目标计数 | 新增目标数与命中候选数分离 | Hashcat 结果转目标匿名 ID、run 内全局去重 | 候选来源元数据 |
| 游标与去重 | 回放中的前缀消费模型 | 真实批内偏移及检查点 | 流式生成及稳定恢复 |
| 研究轨迹 | 回放中的完整批次轨迹 | 真实事件落盘与查询 | 提供重复数和来源摘要 |
| 恢复 | B 规范快照及恢复语义 | 生产版本迁移、原子保存执行与生成状态 | Generator 缓存版本与游标 |

## 公共接口差异清单

### ArmSpec 与候选批次

目前 `interfaces.ArmSpec` 实际重导出 `scheduler.ArmSpec`，后者仍只包含 `strategy_id`。`CandidateBatch` 也只有业务策略 ID。同一策略下的两个生成器或参数组合会发生标识冲突。

建议公共 ArmSpec 增加以下字段。本次先用 `decision.DecisionArm` 承载，避免独自改动公共结构和持久化格式。

| 字段 | 类型 | 语义 |
|---|---|---|
| arm_id | str | run 内唯一、可稳定重建的调度标识 |
| strategy_id | str | 业务策略标签，可被多个 Arm 共用 |
| generator_id | str | 生成器实现 ID |
| target_group_id | str | 共享验证条件的目标组 ID |
| parameter_profile | str | 固定参数配置/版本 ID；本次不是自由参数字典 |
| priority | int | Planner 初始优先级；同优先级按 arm_id 稳定排序 |
| candidate_budget | int | Arm 的提交候选上限，允许为 0 |
| time_budget | float | Arm 的执行时间上限，允许为 0 |
| transfer_score | float 或 null | [0,1] 的迁移先验；为空沿用 1/priority |

公共 CandidateBatch 后续应包含 `arm_id`、稳定 `batch_id`、生成器缓存版本、起止游标，以及去重前数量与重复数量。重复来源保留在 C 的内部元数据中；研究日志仅使用匿名 ID 和聚合量。

### DecisionPolicy 的实际调用

当前协议为构造函数初始化，加 `select`、`observe_outcome`、`stop_reason`、`snapshot`、`restore`。本次保留此形式，不额外引入重复的 initialize 生命周期。

`BaselinePolicy` 实现同名方法，并在 `select` 返回中显式提供 arm_id。它内部把旧引擎名为 strategy_id 的键映射为 arm_id；业务 strategy_id 保留在适配器边界。它没有复制三个算法的选臂公式。

A 的 RealExecutor 目前仍调用 `observe`、`snapshot_statistics`、`restore_statistics`，且按 strategy_id 查找执行状态。需要集中迁移为：

```python
decision = policy.select(available_batch_sizes_by_arm_id)
if decision is None:
    reason = policy.stop_reason(available_batch_sizes_by_arm_id)
else:
    batch = generators.take(decision.arm_id, decision.candidate_limit)
    outcome = executor.execute(batch, time_limit=decision.time_limit)
    policy.observe_outcome(decision.arm_id, outcome)
    checkpoint = policy.snapshot()
```

上面是集成伪代码，`generators.take` 不是已交付的 C API。每次 select 必须对应一次反馈；未完成反馈时禁止再次 select 或保存检查点。终止与超时反馈也必须完成记账。

### BatchOutcome 的统计语义

| 字段 | 约定 |
|---|---|
| candidate_count | 全局去重后实际提交数；提交即消耗预算 |
| tested | 本批完成验证的不同候选数；多盐组的内部哈希运算次数单独计量 |
| recovered | 相对于整个 run 之前状态新增恢复的目标数 |
| successful_candidates | 完成测试且至少新增恢复一个目标的候选数 |
| recovered_target_ids | 本批新增目标的匿名 ID；run 内不可重复 |
| duration | 包含一次启动开销的执行耗时；统一单位为秒 |
| duplicate_count | 为形成当前提交前缀而跳过的重复条目数 |
| offered_count | 本次扫描前缀的原始条目数 = candidate_count + duplicate_count |

本次新增 `DecisionFeedback(BatchOutcome)`，保持原字段并扩展后四类计量所需字段。其 `as_dict()` 包含扩展字段。未来统一奖励必须使用 recovered 的目标语义，不能把 successful_candidates 当作主评价指标。

多目标例子：一条候选恢复用户 u01、u02，则 candidate_count=1、tested=1、successful_candidates=1、recovered=2。原来的 `recovered <= tested` 校验不能用于这个目标计数。

兼容说明：当前启发式公式把恢复数当成候选 Bernoulli 命中数。适配器用 successful_candidates 更新旧引擎，独立累计真实 recovered_targets。单目标行为保留；多目标时它被明确标记为“候选命中启发式基线”，还不是以目标数训练的新奖励算法。普通 BatchOutcome 仅用于可满足单目标计数假设的兼容输入；多目标调用必须传扩展反馈。

Hashcat 的 tested/progress 是否表示候选数，A 必须针对多目标/盐模式核实并转换，不能不经确认直接复用。

### 动态批次与重复归属

RealExecutor 当前执行 `source_batch[:candidate_limit]` 后就递增整批游标。若切出 1000 条中的 100 条，后 900 条被跳过。A/C 必须改成批内偏移或按需生成。

本次回放从有序流中扫描实际选择的前缀；只推进该前缀的游标。区别如下：

- 未被选取的后缀：保留，未来可以调度。
- 已提交但超时未测试的后缀：扣候选预算并计入已提交集合，本次不重试，与当前生产提交预算约定一致。
- 重复条目：跳过，不扣提交预算；第一次实际提交的 Arm 获得执行归属。
- 已恢复目标再次命中：不重复奖励。

当前生产生成器在执行前按 Planner 优先级全局去重；本次回放按实际调度提交顺序去重。它们在跨生成器归属上并不相同。集成前必须选定统一方式；实验比较保持相同输入流和同一套去重规则。不能把两种规则的曲线直接当作调度收益差异。

### 快照与决策日志

RoundRobinScheduler 的 `snapshot()` 现在在原有每 Arm 统计旁增加 `__round_robin__.next_index`；`restore()` 同时支持新结构和旧统计结构。旧结构缺少游标时从首个 Arm 开始，不能保证旧快照的轮转连续性。`snapshot_statistics()` 的旧格式保留。

B 适配器快照包含版本、算法、Arm 配置、预算、引擎状态、新增目标计数和匿名已恢复集合。禁止跨配置恢复。

回放检查点另外包含场景指纹、轮次、候选游标、已提交集合、逐 Arm 批次计数和完整轨迹。当前采用同种子前缀重放验证和重建，再调用 policy.restore；恢复成本随已执行前缀增长，不是生产用常数时间恢复。

第四步已通过独立 `ResearchRecorder` 补全可用 Arm、各可用 Arm 评分分解、决策前/后状态、奖励分量、切换原因、终止事件及配置版本，并在真实执行批次边界接线。SQLite 保存全量历史，原 `DecisionEvent` 及最近 50 条检查点事件保留为摘要；页面由 A 接线。详细契约及恢复边界见 [奖励与日志说明](reward-and-logging.md)。

## 可运行的 B 侧调用样例

```python
from sage_pass.decision import BaselinePolicy, DecisionArm, DecisionFeedback
from sage_pass.enums import TaskStatus

arms = [DecisionArm("pcfg-main", "S3", "pcfg", 1, 100, 10.0)]
policy = BaselinePolicy("heuristic_bandit", arms,
                        total_candidate_budget=100, total_time_budget=10.0)
decision = policy.select({"pcfg-main": 2})
outcome = DecisionFeedback(
    run_id="demo", arm_id="pcfg-main", strategy_id="S3", batch_index=1,
    candidate_count=2, tested=2, recovered=2, duration=0.5,
    status=TaskStatus.COMPLETED, successful_candidates=1,
    recovered_target_ids=("u01", "u02"), offered_count=2,
)
policy.observe_outcome(decision.arm_id, outcome)
checkpoint = policy.snapshot()
policy.restore(checkpoint)
```

## A/C 集成验收清单

1. 两个相同 strategy_id、不同 arm_id 的生成器可同时运行。
2. 真实执行使用规范反馈与快照方法，返回结果仍可按业务策略聚合。
3. 取小批次不会跳过未提交后缀。
4. 多目标收益按目标匿名 ID 全局去重；命中候选数与恢复目标数分离。
5. 运行、生成器与调度器检查点在同一批次边界保持一致。
6. 全部目标恢复时立即停止提交下一批；失败/取消另有显式终态。
7. 去重归属、超时未测候选处理及时间预算口径在真实与回放中对齐。
8. 原始个人信息、历史口令和恢复明文不进入研究轨迹；检查点仅在受控存储中使用。
