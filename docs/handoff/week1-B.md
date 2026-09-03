# 第 1 周乙任务交接

交接日期：2026-09-03
交付版本：0.1.0
范围：Analyzer、Mock Planner、Mock Executor、预留 HTTP 路由接入

## 交付结论

乙负责的第一周 Mock 链路已接入现有后端。甲已完成的项目骨架、任务 API、文件 API、数据库基础和统一错误格式参照甲交接内容；本交接只说明乙新增部分。

当前服务可以从已创建任务继续执行：

```text
POST /api/tasks/{task_id}/analyze
-> POST /api/tasks/{task_id}/plan
-> POST /api/tasks/{task_id}/execute
-> GET /api/runs/{run_id}/status
-> GET /api/runs/{run_id}/result
```

## 主要交付物

- `src/sage_pass/analyzer.py`：实现第一周 Mock Analyzer，将 `TaskDetail` 转换为 `PRIR`，并写入 `PRIRModel`；
- `src/sage_pass/planner.py`：实现第一周 Mock Planner，复用 `StrategyPlan` 输出 `S1` 与可选 `S4`；
- `src/sage_pass/executor.py`：实现 Mock Executor，写入 `StrategyRunModel` 并返回模拟状态和结果；
- `src/sage_pass/routes.py`：注册 Analyzer、Planner、Executor、Run Status、Run Result 五个统一接口；
- `src/sage_pass/repository.py`：补充 PRIR 与策略运行记录的数据访问方法；
- `src/sage_pass/database.py`：兼容旧 SQLite 表结构，允许一个 `run_id` 对应多条策略记录；
- `tests/test_week1_flow.py`：覆盖 Hash 任务链路、文件元数据 PRIR、执行状态约束。

## 实现说明

1. Analyzer 支持 Hash 文本任务和文件元数据任务。
2. Hash 任务优先使用 `known_algorithm`；未提供时按常见 Hash 形态识别 `bcrypt`、`argon2`、`md5`、`sha1`、`sha256`、`sha512`。
3. 文件任务第一周只生成元数据级 PRIR，不解析 ZIP/PDF/Office 加密结构；不确定字段返回 `unknown` 或 `null`。
4. Planner 当前为 `mock`，固定生成 `S1 Baseline`；如果 PRIR 表明有上下文且任务预算足够，则追加 `S4 Context`。极小预算下保留 S1，并通过 `warnings` 说明未加入 S4。
5. Executor 当前只支持 `{"mode": "mock"}`，会生成 `run_id`，按策略写入一条或多条 `StrategyRunModel`。
6. 任务状态按 `created -> analyzed -> planned -> running -> completed` 推进，非法状态调用返回统一错误包络。

## 丙联调说明

- 前端创建任务和上传文件方式参照甲交接内容。
- 创建任务后，按顺序调用乙新增的五个接口即可完成第一周端到端 Mock 链路。
- `POST /api/tasks/{task_id}/analyze` 返回 PRIR，可直接用于页面展示。
- `POST /api/tasks/{task_id}/plan` 返回策略计划，可展示策略编号、优先级、时间预算、候选预算和原因。
- `POST /api/tasks/{task_id}/execute` 返回 `run_id`，后续轮询 `/api/runs/{run_id}/status`。
- `/api/runs/{run_id}/result` 返回最终模拟结果和 `strategy_results`，可用于结果页展示。

## 验证结果

乙方本地验证结果：

- `pytest`：12 项通过；
- 已通过 Swagger UI 完整跑通创建任务、分析、规划、执行、状态查询和结果查询链路。

## 当前限制与下一步

- 当前 Analyzer 为规则和元数据级实现，不做真实文件解析。
- 当前 Planner 为 Mock 计划，不包含真实评分、动态调度或 LLM。
- 当前 Executor 为模拟执行，不调用 Hashcat/JtR，也不生成真实候选。
- 丙接入页面时只依赖 `docs/API.md` 中的 HTTP 接口，不需要依赖模块内部代码。
