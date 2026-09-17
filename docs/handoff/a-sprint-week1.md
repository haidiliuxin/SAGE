# A 两周冲刺 · 第 1 周交接（执行链修复与公共接口）

交接日期：2026-09-14
交付版本：0.4.0
范围：公共接口冻结、算法识别链修复、Argon2 真实执行、adaptive 语义修正、跨重启结果查询、代码清理、TargetExtractor 基础

## 交付结论

按《任务安排.docx》第 1 周（第 1～7 天）的 A 侧范围完成，全部改动有自动化测试覆盖并通过全量回归：

- 冻结的公共接口集中导出于 `src/sage_pass/interfaces.py`；
- 真实执行的算法识别链打通（Analyzer 识别结果可直接执行）；
- Argon2 系列可自动真实执行（模式 34000 / 70000，参数化 Hash 完整透传）；
- `adaptive` 不再名不副实：新增调度模式配置，旧配置给出明确迁移错误；
- 已完成真实运行在服务重启后仍可查询原始结果；
- `transfer.py` 重复函数清理；
- `TargetExtractor` 统一接口与 Hash/ZIP 实现（PDF/Office 下一阶段交付，返回明确错误）。

## 主要交付物

| 项 | 交付 |
|---|---|
| ① 公共接口 | `interfaces.py`：`ArmSpec` / `CandidateBatch` / `BatchOutcome` / `InformationProfile` / `DecisionEvent` / `DecisionPolicy`；`BatchOutcome` 已接入执行循环，`DecisionEvent` 以研究日志形式记录每轮决策（评分快照、先验状态、反馈、奖励、停止原因，最多保留 200 条并随检查点持久化最近 50 条） |
| ② 算法识别链 | `hashcat_adapter.normalize_algorithm_name()` + `ALGORITHM_ALIASES`；`RealExecutor._resolve_targets()` 判定顺序：显式 `hashcat_mode` → `known_algorithm` → `PRIR.algorithm` → 明确 422 |
| ③ Argon2 | `Analyzer` 变体识别（`argon2id/argon2i/argon2d/argon2`）+ 模式映射（34000/70000，取自本机 `hashcat -hh`）；参数化 Hash 行原样写入 hashcat 目标文件 |
| ④ 调度语义 | `SchedulerType`（fixed / round_robin / heuristic_bandit / ucb / cost_aware_ucb / thompson）+ `SAGE_SCHEDULER_TYPE`；`PlannerType.ADAPTIVE` 移除；`SAGE_PLANNER_TYPE=adaptive` 触发明确迁移错误；`GET /api/system/config` 暴露 planner/scheduler 两种模式 |
| ⑤ 跨重启查询 | `RealExecutor.load_persisted_status()/load_persisted_result()`；路由顺序：内存 registry → RunRecord 持久化记录 → Mock；损坏记录 409 明确报错 |
| ⑥ 清理 | `transfer.current_task_transfer_seeds()` 只保留一份（`Iterable` 入参 + 文档字符串） |
| ⑦ 目标提取 | `targets.py`：`TargetExtractor` 协议、`ExtractedTarget`、Hash/ZIP 提取器、PDF/Office 明确未交付错误；`RealExecutor` 通过提取器链解析目标 |
| 其他 | `SchedulerType` 快照/恢复（续跑保持调度模式）；`docs/API.md`、`.env.example`、README 同步；版本升至 0.4.0 |

## 验证结果

- 后端：**443 项测试通过**（原 430 + 新增 13 项 `tests/test_a_week1_core.py`），`compileall` 通过；
- 覆盖点：公共结构导出与序列化、别名归一、PRIR 算法链真实执行、未知算法报错与显式覆盖、Argon2id 模式与 Hash 透传（仿真 hashcat 参数日志断言）、`adaptive` 迁移报错与非法配置、fixed/round_robin/bandit 工厂与未交付算法报错、`/api/system/config`、重启后真实结果重建（实测 tested/recovered/recovered_items 一致）、损坏记录明确错误、transfer 单一定义、目标提取器矩阵；
- 前端：**12 项测试通过** + `npm run build` 成功（新增“规划/调度模式分别展示”断言，已移除 adaptive 规划类型）；
- OpenAPI 与运行时一致；`git diff --check` 干净。

## 下一阶段（第 8～14 天）A 侧

1. PDF/Office MVP：接入成熟开源提取工具（模式参考：PDF 10400/10500/10600/10700，Office 9400/9500/9600，均以 `hashcat -hh` 为准）；
2. 接入 B 的 `DecisionPolicy`（UCB / cost-aware UCB / Thompson）并把 `DecisionEvent` 落到查询接口；
3. 接入 C 的 `InformationProfile` 与 Generator Registry；
4. 数据库迁移（Alembic）、OpenAPI/前端类型集中同步与全量端到端测试；
5. 旧版本记录兼容：必要时为 `RunRecordModel` 增加 schema 版本字段与迁移路径。

## 边界说明

- 暂停仍在批次边界生效；`round_robin`/`fixed` 为消融基线，默认仍是 `heuristic_bandit`；
- PDF/Office 真实执行在提取器交付前会返回明确 `422`，Mock 链路不受影响；
- 研究日志与决策事件不记录恢复明文；`RunRecord.snapshot/progress` 仍按设计保存当前授权任务的候选与恢复结果（知识库本身不含明文）。
