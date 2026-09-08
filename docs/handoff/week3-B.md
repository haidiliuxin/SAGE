# 第 3 周乙任务交接

交接日期：2026-09-08
交付版本：0.2.0
范围：Bandit Scheduler、评分函数、探索与停止条件、Real Executor 联调和动态批次预算分配

## 交付结论

乙负责的第 3 周自适应调度代码已经完成，并由使用方在 Conda `sage` 环境中确认测试通过。甲负责的暂停、继续、取消及异常收尾参照 [第 3 周甲交接](week3-A.md)；本交接只说明乙新增的 Bandit 调度能力。

本次保持团队统一 HTTP API 和 Pydantic 响应结构不变。Bandit Scheduler 作为 Real Executor 的内部批次调度组件接入，不新增前端必传字段，也不改变 `POST /execute`、Run Status 和 Run Result 路径。

## 主要交付物

- `src/sage_pass/scheduler.py`：Bandit Arm、运行统计、评分分解、首轮探索、批次选择和预算停止判断；
- `src/sage_pass/real_executor.py`：将固定逐策略执行改为批次级动态调度，把每批 Hashcat 统计反馈给 Scheduler；
- `tests/test_scheduler.py`：覆盖探索顺序、评分收益与成本、预算截断、停止条件及 Real Executor 动态选批联调；
- `docs/API.md`：补充内部调度规则、评分公式、预算和统计语义；
- `README.md`：更新已完成能力、测试范围、交接索引和目录说明。

## 已统一的调度规则

1. 同一个真实执行 run 中，“目标组 + 策略”作为一个 Arm。当前每个 run 的目标组固定，因此运行内以 `strategy_id` 标识 Arm。
2. Scheduler 先按 Planner 给出的 `priority`，为每个可执行策略探索一个候选批次。
3. 每批 Hashcat 完成后，使用实际 `tested`、该策略新增且去重后的 `recovered` 和 `duration` 更新 Arm 统计。
4. 探索完成后按评分选择下一批，不再固定把某个策略全部跑完后才进入下一个策略。
5. Planner 的策略候选预算和时间预算是硬上限；任务候选预算和总时间预算也是全局硬上限。
6. 暂停仍在批次边界生效；继续后 Scheduler 从剩余候选批次继续选择；取消仍会停止当前 Hashcat 进程。

## 评分函数

```text
Score = 0.45 * P_success
      + 0.30 * Gain
      + 0.15 * Transfer
      - 0.10 * Cost
```

- `P_success`：使用 Jeffreys 先验平滑候选成功率，并估计下一批至少成功一次的概率；
- `Gain`：近期每千个测试候选的恢复收益，使用指数移动平均，范围限制为 0～1；
- `Transfer`：当前没有开放 S5 历史迁移策略，因此使用 Planner 优先级作为先验，即 `1 / priority`；
- `Cost`：该策略累计实际执行时间占其时间预算的比例，范围限制为 0～1。

同分时继续按 Planner 优先级和策略编号稳定选择，保证相同输入与反馈下调度顺序可复现。

## 停止条件

- 达到任务总 `candidate_budget`；
- 达到任务 `total_time_budget`；
- 策略达到自身 `candidate_budget`；
- 策略达到自身 `time_budget`，或达到请求中 `timeout` 覆盖后的上限；
- 所有策略候选批次均已耗尽；
- 用户取消任务。

候选预算按实际提交给 Hashcat 的候选数量扣减；`tested` 仍以 Hashcat 实际返回值为准。这样即使某批因超时只测试了一部分，也不会重复提交同一批并突破候选预算。

## 执行链

```text
StrategyPlan
-> S1～S4 候选生成与稳定去重
-> CandidateBatch
-> 每个 Arm 首批探索
-> HashcatResult(tested/recovered/duration)
-> 更新评分并选择下一批
-> 预算或候选耗尽后停止
-> RunStatus / RunResult
```

结果继续复用既有 `StrategyResult`：`tested` 和 `recovered` 按策略累计，`time` 为该策略实际执行批次耗时之和，`success_rate = recovered / tested`。动态调度期间 `current_strategy` 返回当前真正执行的策略。

## 验证

使用方已确认测试通过。重点测试命令如下：

```powershell
python -m pytest tests/test_scheduler.py
python -m pytest tests/test_week2_real_execution.py tests/test_week3_controls.py
python -m pytest
```

## 边界说明

- 本周不开放 S5，也不实现跨任务的历史恢复结果学习；`Transfer` 暂以 Planner 优先级先验落地。
- Bandit 统计和剩余批次游标保存在当前 Real Executor 进程内；进程中断后仍按甲的异常恢复 v1 收尾，不在本周实现跨进程续跑。
- 本次没有新增 API 字段，因此 `docs/openapi.json` 无需变更。
- 本系统仅用于经过授权的离线口令安全评测。
