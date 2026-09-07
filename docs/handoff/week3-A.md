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
- Real：暂停在批次边界生效；当前批次结束后进入 `paused`；暂停中可取消；继续后从下一批候选恢复。运行期内存状态（候选批次、恢复明细）跨进程恢复仍属后续工作（与乙的调度器一起持久化候选状态）。

## 验证结果

- `pytest`：88 项通过（含新增 5 项第三周控制/恢复测试）；
- 前端 `npm test` 9 项、`npm run build` 通过（`paused` 类型与中文标签已同步）；
- OpenAPI 与运行时一致；`compileall`、`git diff --check` 通过。

## 边界与下一步

- 暂停语义：real 在批次边界生效；单个超长批次无法中途挂起（受该批时间切片上限约束）。
- 异常恢复 v1 只“收尾不续跑”；跨重启续跑需把候选批次/进度落库，拟与乙的动态调度联调时一并设计。
- 本系统仅用于经过授权的离线口令安全评测。
