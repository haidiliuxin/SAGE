# 第 2 周乙任务交接

交接日期：2026-09-06
交付版本：0.2.0
范围：S1 Baseline、S2 Rule、候选去重与批次输出、Hashcat 执行接入、策略执行统计

## 交付结论

乙负责的第 2 周候选生成与执行接入已经完成，并由使用方在 Conda `sage` 环境中确认候选模块测试、真实执行链测试和完整 pytest 回归均通过。

甲负责的 Hashcat Adapter、ZIP（WinZip AES）接入、进程停止、超时与结果解析参照 [第 2 周甲交接](week2-A.md)。丙负责的 S3 PCFG-lite、S4 Context、LLM/Rule Planner 和网页接入参照丙交接内容；本交接只说明乙新增部分。

## 主要交付物

- `src/sage_pass/candidate_generator.py`：实现 `CandidateGenerator` 与 `CandidateBatch`，生成 S1/S2 候选并提供批次迭代；
- `src/sage_pass/real_executor.py`：接入后端候选生成，按策略批次调用甲的 `HashcatAdapter`，累计真实执行统计；
- `tests/test_candidate_generator.py`：覆盖 S1、S2 参数规则、稳定去重、预算截断、批次输出、输入约束和仿真 Hashcat 统计；
- `tests/test_week2_real_execution.py`：覆盖不传前端候选时的后端自动生成，以及 S1/S2 策略统计和完整真实执行 API 链；
- `docs/API.md`：同步候选来源、规则、预算、批次和统计语义。

## 实现说明

1. S1 按固定顺序输出基础高频候选；调用方仍可通过现有 `candidates` 字段提供可选补充候选，补充项优先但不再是启动真实执行的必要条件。
2. S2 严格读取计划中的七个布尔参数，支持首字母大写、全大写、全小写、常见数字后缀、年份后缀、常见字符替换和符号后缀。
3. 候选在 S1/S2 之间全局稳定去重，保留第一次出现的值并区分大小写；非法空值、换行文本和超过 1024 字符的输入会被拒绝。
4. 每个策略的输出不超过其 `candidate_budget`，全次执行不超过现有 API 的 100000 条上限；默认按每批 1000 条交给执行层。
5. 同一策略的多个批次共用策略时间预算。时间耗尽后不再启动后续批次，甲的 Hashcat 超时与停止机制继续作为底层保障。
6. 多批次结果累计到同一策略的 `tested`、`recovered`、`time` 和 `success_rate`；恢复结果在策略内及总结果中去重。
7. `POST /api/tasks/{task_id}/execute`、Run Status 和 Run Result 的路径与响应模型保持不变。

## 接口行为变化

真实执行现在允许不传前端候选：

```json
{
  "mode": "real",
  "hashcat_mode": 0,
  "timeout": 60
}
```

后端会使用执行阶段取得的 `StrategyPlan` 自动生成 S1/S2 候选。原有 `candidates` 字段保留为可选补充输入，因此旧调用方式仍然兼容。

执行链如下：

```text
StrategyPlan
-> S1/S2 候选生成
-> 稳定去重与策略预算截断
-> CandidateBatch
-> HashcatJob
-> RunStatus / RunResult 策略统计
```

## 验证结果

使用方已确认以下命令全部通过：

```powershell
python -m pytest tests/test_candidate_generator.py
python -m pytest tests/test_week2_real_execution.py
python -m pytest
```

自动化联调使用甲提供的可控仿真 Hashcat，不要求测试机安装真实 Hashcat；真实工具安装和路径配置参照甲交接内容。

## 后续接入说明

- S3/S4 候选生成接入时应复用 `CandidateBatch` 的策略标识和批次结构，以便 Real Executor 继续按策略累计统计；
- 网页调用真实执行时只需发送 `mode: real`，无需自行构造 S1/S2 候选；
- 页面可继续使用现有 `strategy_results` 和 `recovered_items` 展示策略统计及真实恢复结果；
- 本模块只用于经过授权的离线口令安全评测。
