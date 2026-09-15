# B 第一至第三步交接

本次交付接口对齐方案、数学模型和最小离线回放闭环。复用当前三个调度算法，建立下一阶段奖励、UCB、成本模型与实验比较的基础。

按使用方要求，本次没有运行 pytest、演示命令、编译检查或真实 Hashcat。以下命令和用例交给使用方在已配置的项目环境中验证，文档不包含未经运行的成功结果或性能结论。

## 阅读入口

1. [接口对齐方案](../decision/interface-alignment.md)：字段语义、A/C 接线清单、多目标计数、动态批次和快照迁移。
2. [数学模型](../decision/mathematical-model.md)：符号表、状态、动作、奖励建议、预算、更新、停止性和模型边界。
3. 本文：代码结构、运行命令、验收用例和后续工作。

## 实现文件

| 文件 | 用途 |
|---|---|
| `src/sage_pass/decision/types.py` | DecisionArm、PolicyDecision、扩展 BatchOutcome 的 DecisionFeedback |
| `src/sage_pass/decision/baselines.py` | 复用三个生产调度算法，映射 arm_id，独立累计目标收益，统一规范方法 |
| `src/sage_pass/scheduler.py` | RoundRobinScheduler 的 snapshot/restore 补充轮转位置；旧统计快照方法保留 |
| `src/sage_pass/experiments/replay.py` | 有限流、全局去重、预算、固定种子、成本参数、完整轨迹、检查点、三基线比较 |
| `src/sage_pass/experiments/__main__.py` | 离线 CLI |
| `examples/replay/baseline-small.json` | 纯合成候选 ID 和匿名目标的演示场景 |
| `tests/test_decision_replay.py` | 新增验收测试，尚未运行 |

没有新增第三方依赖；仍需使用装好项目原有依赖的 Python 环境，因为公共接口导入现有候选模块。没有修改公共 Schema、数据库、路由、前端和真实执行接线。

## 快速运行

在仓库根目录、已安装本项目的环境中执行：

```powershell
python -m sage_pass.experiments --scenario examples/replay/baseline-small.json --output data/replay/baseline-comparison.json
```

如果项目未做 editable 安装，可以在当前 PowerShell 会话设置源码路径后执行同一命令：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
```

默认比较 fixed、round_robin、heuristic_bandit，输出 JSON 包含每种算法的配置指纹、提交量、测试量、新增恢复目标数、仿真时间、停止原因、各 Arm 统计和完整逐批轨迹。该场景只用于机制演示，不能用来证明某算法优于其他算法。

### 保存和恢复检查点

```powershell
python -m sage_pass.experiments --scenario examples/replay/baseline-small.json --policy round_robin --max-steps 1 --checkpoint data/replay/rr-checkpoint.json --output data/replay/rr-partial.json
python -m sage_pass.experiments --scenario examples/replay/baseline-small.json --policy round_robin --resume data/replay/rr-checkpoint.json --output data/replay/rr-resumed.json
python -m sage_pass.experiments --scenario examples/replay/baseline-small.json --policy round_robin --output data/replay/rr-full.json
```

验收时比较 `rr-resumed.json` 和 `rr-full.json` 的 JSON 内容，应完全一致。`--max-steps` 表示本次调用最多新增多少轮；分段运行尚未结束时 stop_reason 为 null。场景和策略必须与检查点一致；损坏检查点应明确失败。

检查点通过重新计算确定性的历史前缀恢复状态，并调用策略 restore，不运行真实验证器。恢复成本随历史轮数增长。研究报告不会输出候选 token；检查点为去重保存 candidate token，因此使用真实候选时应先换成不携带明文的稳定匿名 ID，并保管好原始映射。

### 场景格式

- `arms`：DecisionArm 字段；允许不同 arm_id 共享 strategy_id。
- `candidate_streams`：每个 Arm 的有序候选 ID 数组，允许重复。
- `target_ids`：匿名目标集合，至少一个。
- `matches`：候选 ID 到命中目标 ID 数组的映射，只有环境可以读。
- `total_candidate_budget`、`total_time_budget`：提交候选和执行时间上限。
- `batch_size`：第一版固定批次上限，仍受局部和全局预算截断。
- `cost`：默认 startup_cost、seconds_per_candidate；`arm_costs` 可按 Arm 覆盖。
- `seed`：固定随机种子；`time_jitter` 默认为 0，可设为 [0,1) 的比例模拟批次速度波动。

快/慢 Hash 使用不同的仿真每候选成本。这里的参数由实验人员输入，未提供实际标定器或自动盐模型；不要把这些参数标记为真实测量值。多个目标共享同一目标组，成本表示该组整体验证一个候选的成本。

## 使用方测试命令

```powershell
python -m pytest tests/test_decision_replay.py
python -m pytest tests/test_scheduler.py tests/test_a_week1_core.py tests/test_week3_recovery.py
```

第一条验证新增模块；第二条核对旧调度和公共结构、恢复契约兼容性。项目需要时再由 A 运行全量回归。

### 验收重点

| 类别 | 验证内容 |
|---|---|
| 确定性 | 三种策略同输入同种子生成相同轨迹 |
| 调度差异 | Fixed 优先顺序、RR 轮转位置、启发式首轮探索保留 |
| 多目标 | 一候选恢复两目标仍只记一个命中候选，重复目标不再奖励 |
| 预算 | 总/局部预算、最后一个短批次、启动即超时、tested 小于 submitted |
| 候选流 | Arm 内和跨 Arm 去重、未选后缀不丢失、空流与耗尽停止 |
| 恢复 | JSON 往返、三策略续跑与完整运行一致、场景/策略/损坏记录拒绝 |
| 日志 | 完整轨迹不含候选 token 与命中表，报告返回副本 |
| 兼容 | 旧单目标行为、旧 RR 统计快照可读取、公共结构形状保留 |

## 已知限制与下一步

1. 新接口是 B 侧适配结构，尚未由 A/C 合并成公共协议；真实执行仍是原调用路径。
2. 旧启发式以命中候选统计计算分数；目标级新增恢复量单独保存用于评价。新的统一奖励计算器和 UCB 均未在本轮实现。
3. 只有固定配置批次上限与预算截断；最优动态批次选择待成本模型完成后实现。
4. 回放按实际提交顺序去重，生产候选目前按 Planner 顺序预去重；集成时必须统一。
5. 当前暂停/恢复只针对回放批次边界。生产中正在执行的批次、取消、工具失败和重启事务仍由 A 负责。
6. 当前回放以有限内存候选流和完整内存轨迹实现；大规模流式缓存、直接恢复及长期日志落盘属于后续优化。
7. PDF 原文未提供，q 的似然估计与理论性质不作原文级结论。模型文档将已实现能力与后续假设分别说明。

建议下一阶段先统一奖励和完整生产事件，再接 UCB、实测成本标定及 Cost-aware UCB；A/C 可直接按接口清单开展并行模块接线。
