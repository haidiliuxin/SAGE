# B 第四步交接：统一奖励与完整日志

## 交付

- `decision/rewards.py`：统一目标收益、时间/候选/重复惩罚、完整分解、缺测标记、固定版本和权重。
- `decision/research_log.py`：SQLite 全量事件、开始/完成/中断/终态、只含白名单字段、幂等、流式 JSONL 导出。
- `experiments/replay.py` 与 CLI：统一奖励与逐轮落盘、默认日志库、奖励配置、version 2 检查点续跑。
- `real_executor.py`：执行前记录选择、执行后记录反馈与奖励、全局新增目标去重、保留最近摘要、恢复轮次和配置。
- `scheduler.py`：只读决策诊断，按预算裁剪批量记录各可用 Arm 的评分，不改变选臂规则。

参考论文、设计解释、完整字段和操作命令见 [奖励与日志说明](../decision/reward-and-logging.md)。第五步 UCB 和成本模型尚未实现；页面摘要由 A 接线。

## 用户执行的验收

更新：用户已在 `sage` 环境报告本步两组测试 **55 passed（28.62s）+ 24 passed（7.78s）**；两组只有依赖弃用警告。以下保留交付时的测试说明。第五步改动需另行回归，见 [第五步交接](b-milestone5.md)。

本次**没有运行 Python、pytest 或真实 Hashcat**。仅检查代码与 Git 差异。用户先前报告的 31 项回放测试及 24 项原有回归通过，属于第一至第三步版本，不能视为本次改动的验证结果。

在项目根目录和 `sage` 环境执行：

```powershell
python -m pytest tests/test_reward_logging.py tests/test_real_research_logging.py tests/test_decision_replay.py
python -m pytest tests/test_scheduler.py tests/test_a_week1_core.py tests/test_week3_recovery.py
```

新增用例覆盖奖励计算/多目标/负值/超时/缺测、301 轮完整历史、研究日志脱敏、并发重试、续跑不重复落盘、权重不匹配拒绝恢复、执行启动失败/取消以及日志失败阻止无记录执行。真实执行用例使用进程内替身，不调用真实 Hashcat。

随后可运行说明文档中的三个基线比较命令，检查 JSONL 中的 `decision_completed` 数量与报告轮数一致，逐轮包含原始反馈、奖励分解和前后状态。

## 需要了解的变化

1. 回放旧版检查点不能跨版本继续，需重新生成 version 2。
2. 默认奖励权重仅为实验初值，需预实验后固定，不在测试集上调参。
3. 真实执行缺少逐批重复量，日志明确标为未知；与回放比较前要对齐重复测量/权重。
4. 业务检查点和研究库不是同一事务；恢复分析必须结合 `attempt_id`、`resume_from_round` 与 checkpoint 标记。
5. 研究库含完整事件，200/50 条仅是原页面/检查点摘要上限。
