# 第 3 周甲任务交接

交接日期：2026-09-07
交付版本：0.2.0
范围：分批执行控制（暂停/继续/取消）与实时状态；任务状态持久化与异常恢复（重启收尾 v1）

## 交付结论

甲负责的第 3 周前半段能力已实现并经自动化验证：

- **分批执行控制**：真实执行以候选批次为最小调度单元（沿用第 2 周乙的批次管线），新增**暂停/继续/取消**；暂停在**批次边界**生效（当前批次跑完后进入暂停，避免半途杀掉 Hashcat 导致进度不可续），取消可在任意时刻停止（含暂停中取消）。
- **实时状态返回**：`GET /runs/{run_id}/status` 新增 `paused` 状态与对应消息（mock 为冻结暂停；real 在批内受理中会先返回“等待当前批次结束”再进入 `paused`）。
- **状态持久化与异常恢复 v1**：任务新增 `paused` 状态及受控状态机（`running ↔ paused`，可转 `completed/failed/cancelled`）；进程重启/中断后由启动时的 `finalize_interrupted_tasks` 把残留的 `running/paused` 任务与策略行收尾（全部策略行已完成 → 任务 `completed`，否则 `failed`），杜绝状态悬挂。

## 主要交付物

- `src/sage_pass/run_control.py`：进程内暂停/继续记账原语（`RunControl`），供 mock 与 real 共用；挂载于 `app.state.run_control`。
- `src/sage_pass/enums.py` / `service.py`：`TaskStatus.PAUSED` 与状态机迁移；`finalize_interrupted_tasks(session_factory)` 启动收尾（幂等）。
- `src/sage_pass/executor.py`：Mock 执行暂停冻结（进度/已测数/耗时在暂停期间保持不变），状态返回 `paused`。
- `src/sage_pass/real_executor.py`：批次边界暂停等待（轮询 RunControl，被取消时立即退出）；`status()` 暴露 `paused` 与“受理中”消息；`shutdown` 清理控制状态。
- `src/sage_pass/routes.py`：`PATCH /tasks/{id}/status` 支持 `paused`/恢复 `running` 并联动控制器；run status/result 在任务 `running|paused` 时向终态同步（`_sync_task_status_after_run`）。
- `src/sage_pass/main.py`：启动时执行异常收尾并创建 `RunControl`。
- `src/sage_pass/repository.py`：`active_run_ids(task_id)` 查询活动 run。
- `tests/test_week3_controls.py`：状态机、mock 冻结/继续、real 暂停/继续/取消、两类异常收尾（挂起→failed、行完成→completed）。

## 接口与行为变化

- 状态：新增 `paused`。任务处于 `running` 时 `PATCH status {"status":"paused"}` 暂停；处于 `paused` 时 `{"status":"running"}` 继续、`{"status":"cancelled"}` 取消。
- Mock：暂停期间进度冻结（冻结点在暂停请求受理时）。
- Real：暂停在批次边界生效；当前批次结束后进入 `paused`；暂停中可取消；继续后从下一批候选恢复。运行记录（目标、计划、候选批次、逐批进度与 Bandit 统计检查点）持久化在 `RunRecordModel`，服务重启后自动从断点续跑（见下方“甲后半：持久化与断点续跑”）。

## 甲后半：持久化与断点续跑（v2）

- 新增 `RunRecordModel`（`run_records` 表）与 `RunRecordRepository`：真实执行启动时写入静态快照（目标、计划、各策略候选批次），每个批次完成后写进度检查点（各策略已消费批次、累计 tested/recovered/time_cost、恢复明文、Bandit 统计）。
- 服务启动（lifespan）先调用 `RealExecutor.recover_after_restart()`：把上次中断残留的 running/paused 真实运行重建状态并自动续跑（跳过已消费批次，恢复调度统计）；返回被续跑的任务集合，其余残留任务仍由 `finalize_interrupted_tasks(..., exclude_task_ids=resumed)` 收尾，避免重复处理。
- 无法重建的记录（数据损坏/格式不兼容）会连同策略行与任务收尾为 failed，并写入 durable 的失败原因。
- 语义说明：进程中断时“正在执行的那一批”尚未计入检查点，恢复后会整批重跑一次（至多重复一个批次）；暂停状态在重启后被自动继续（服务重启视为基础设施事件，不保留“已暂停”状态）。

## 验证结果

- `pytest`：97 项通过（第 3 周甲控制/恢复测试 9 项：暂停/继续/取消、冻结、收尾、断点续跑×2、恢复失败、排除参数）；
- 前端 `npm test` 9 项、`npm run build` 通过（`paused` 类型与中文标签已同步）；
- OpenAPI 与运行时一致；`compileall`、`git diff --check` 通过。

## 边界与下一步

- 暂停语义：real 在批次边界生效；单个超长批次无法中途挂起（受该批时间切片上限约束）。
- 断点续跑以“批”为粒度：崩溃时在跑批次会整批重跑一次；候选快照随记录落库（候选较多时记录体积较大，后续可改为词表文件引用）。
- 跨任务的历史学习（S5/Feedback）与调度联调事件展示仍在乙/丙侧推进。
- 本系统仅用于经过授权的离线口令安全评测。
