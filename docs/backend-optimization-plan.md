# SAGE-Pass 后端优化建议（2026-09 评审整理）

> 范围：仅后端（`src/sage_pass`、数据库、执行/持久化链路与 API）。
> 依据：main 分支（e21c682，含执行控制、Bandit 调度、断点续跑与 Feedback/S5 合入）代码审查。
> 说明：以下按“风险 / 性价比”排序，P0 建议优先处理，P2 主要服务第 4 周实验与丙的实时看板。

---

## P0 · 建议优先修复（可靠性）

| # | 问题 | 现状 | 建议 |
|---|---|---|---|
| P0-1 | 重启续跑在多进程/多副本下会**重复执行同一 run** | `RealExecutor.recover_after_restart()` 读取 `running/paused` 记录后直接各自 `spawn` 恢复线程，无原子认领；`uvicorn --workers N` 或 K8s 多副本时每个进程都会续跑同一批候选 | 恢复前把记录 CAS 式更新为 `recovering`（`UPDATE ... WHERE status='running'`），失败则跳过；恢复成功后再置 `running`，终态后写 `completed/failed/cancelled` |
| P0-2 | 数据库**没有正式迁移机制** | 仅 `Base.metadata.create_all()` + 一条手写 sqlite 迁移（strategy_runs）；加列/改列对已有 `data/*.db` 会失败 | 引入 Alembic（保留测试环境的自动建表）；在建表/迁移改动前落地 |
| P0-3 | SQLite **并发写放大** | 真实执行每完成一个批次就开一个新 session 写 `run_records.progress`（100k 候选 ≈ 每 run 约 100 次写库），叠加状态 PATCH 与轮询易触发 `database is locked` | 引擎开启 **WAL + busy_timeout**；检查点**降频合并**（每 K 批或按时间窗写一次） |
| P0-4 | 进程内 run registry **只增不减** | `RealExecutor._registry/_task_runs` 仅在 shutdown 清理，长驻进程内存缓慢增长 | 终态 run 延迟清理或加容量上限；已完成 run 的状态/结果改由 DB 记录支持查询 |

## P1 · 规模与性能（第 4 周实验前建议）

| # | 问题 | 现状 | 建议 |
|---|---|---|---|
| P1-1 | `RunRecordModel.snapshot.batches` **把全部候选字符串以 JSON 落库** | 已改为候选流游标 + 去重 digest 索引；仅旧记录保留 `batches` 兼容读取 | 后续关注 generator 版本漂移与更大预算下 digest 索引压缩 |
| P1-2 | **每个批次一次 hashcat 进程启动** | 批量 1000 时每次拉起进程约 0.2~1s，慢 Hash 场景浪费明显 | 批次大小做成 env 配置并文档化权衡；同策略顺序候选可合并更大批次 |
| P1-3 | **接口一致性检查靠人肉脚本** | OpenAPI 与运行时一致性目前在合并流程手动执行 | 提升为 pytest；为 checkpoint/断点续跑加大 run 基准测试，防第 4 周实验回归 |

## P2 · 为第 4 周实验 + 看板 + 演示铺路

| # | 事项 | 说明 |
|---|---|---|
| P2-1 | **运行结果导出接口** | `GET /runs/{run_id}/export`（JSON/CSV）：per-strategy tested/recovered/time、恢复明文、停止原因；直接支撑第 4 周实验与答辩 |
| P2-2 | **调度事件/轮次快照端点** | 暴露每轮“选中策略、本轮候选预算/时间、评分分解、探索与否、停止原因”，供丙实时看板与“高收益 / 先有效后衰减”场景可视化 |
| P2-3 | **任务重试语义** | FAILED/CANCELLED 目前是终态，中断后只能重建任务；可加 `POST /tasks/{id}/retry`（复用目标/上下文重新 analyze→execute）提升演示与恢复体验 |
| P2-4 | **`GET /feedback/patterns` 参数对齐** | 列表端点未应用 `minimum_observations/minimum_tasks` 阈值（与 transfer 查询不一致），建议统一并加分页游标 |

## P3 · 整洁与工程

| # | 事项 | 说明 |
|---|---|---|
| P3-1 | 去掉 `routes._plan_with_knowledge` 的 `inspect.signature` 反射 | 把 `knowledge_summary` 统一进 `Planner.plan` 接口；顺带清理 `MockPlanner.plan` 中的 `del knowledge_summary` |
| P3-2 | 版本一致性自动校验 | `__version__` / pyproject / OpenAPI `info.version` 三处用一个小测试断言一致 |
| P3-3 | 部署说明 | uvicorn 固定单进程并在文档说明原因（与 P0-1 绑定）；对外演示可加极简鉴权/限流 |

---

## 建议执行顺序

1. **P0-1 + P0-3**：续跑原子认领、SQLite WAL 与检查点降频——直接决定“第 3 周已交付的持久化续跑”在多进程/真实部署下是否可靠；
2. **P2-1 + P2-2**：运行统计导出 + 调度事件快照——第 4 周实验与丙看板的地基；
3. **P0-2 Alembic**：在下一轮改表之前引入；
4. 其余 P1/P3 视迭代节奏按需推进。

## 验收方式（每一项都应满足）

- 对应新增/修改均有 pytest 覆盖（续跑原子性、WAL/降频行为、导出格式、事件快照结构等）；
- `pytest`、`compileall`、OpenAPI 与运行时一致性、`git diff --check` 全绿；
- 真实工具冒烟不受影响（hashcat/zip2john 授权测试集）。
