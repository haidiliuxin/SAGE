# Feedback Engine v2 交接

交接日期：2026-09-08  
交付版本：0.3.0  
范围：跨任务抽象模式学习、S5 Transfer Strategy、Planner/Policy/Scheduler 集成和只读知识 API

## 交付结论

Feedback Engine v2 已形成完整闭环：真实评测任务完成后，从最终去重恢复结果提取抽象结构，在单一数据库事务中聚合 Pattern Knowledge 并写入 run 幂等标记；新任务按 `target_type + algorithm` 加载达到支持度的模式，Rule/LLM Planner 可以选择 S5，候选生成器把模式应用于当前任务授权种子，现有 Bandit Scheduler 使用学习得到的 `transfer_score` 并继续执行首批探索。

本实现只面向经过授权的离线口令安全评测。没有增加在线登录、远程凭据尝试、访问控制绕过或远程目标能力。

## 模块职责

- `patterns.py`：集中定义字符类别编码、年份范围和输入上限；`PatternExtractor` 产生确定性结构特征。
- `feedback.py`：定义支持度、容量、时效配置和置信度；`FeedbackService` 完成去重、聚合和事务幂等处理。
- `transfer.py`：加载同作用域知识、生成脱敏 Planner 摘要、计算迁移评分并惰性生成 S5 候选。
- `models.py` / `repository.py`：持久化与查询 `pattern_knowledge`、`feedback_runs` 和 `pattern_task_observations`。
- `real_executor.py`：completed 真实 run 持久化终态后自动反馈；反馈异常不改变任务终态。

## Pattern Knowledge 数据结构

`pattern_knowledge` 保存：`scope`、`target_type`、`algorithm`、`pattern_type`、`pattern_signature`、抽象 `feature_data`、`observation_count`、`task_count`、`confidence` 和首次/最近观察时间。唯一键为 `scope + pattern_type + pattern_signature`。

`pattern_task_observations` 以 `pattern_id + task_id` 唯一，保证同一任务即使有多个 run，某模式的 `task_count` 也只增加一次。模式在同一任务中的多次出现仍会增加 `observation_count`。

`feedback_runs` 以 `run_id` 唯一，保存处理时间、模式数量和 schema 版本。模式更新、task 去重行和 FeedbackRun 标记在同一次 commit 中完成；失败时整体 rollback。

## 抽象特征与安全边界

抽取项包括总长度、字符类别、连续类别压缩签名、数字位置、抽象前后缀、大小写模式、1900～2099 年份和上下文约束的常见字符替换。一次命中的 confidence 为 0.30，并且默认至少需要 2 次观察和 2 个独立任务才可用于 S5。

Pattern Knowledge、Planner 摘要、Feedback 错误日志和知识查询 API 均不保存或返回历史恢复明文、目标 Hash、文件内容和原始上下文。既有 Run Result/RunRecord 仍按原接口保存当前授权 run 的恢复结果，以支持结果查询和断点续跑；它不会被当作跨任务 S5 种子。

## S5 候选与调度

S5 支持有限结构：word + year、CapitalizedWord + digits、acronym + digits、word + digits + symbol、数字前缀和受控替换加后缀。输入只来自请求方补充候选、S1 基线及当前任务关键词、地区和组织字段。候选惰性生成，按模式置信度、任务数、观察数、类型、签名和 id 稳定排序，并复用现有全局去重、单策略预算、任务总预算、1024 字符与单行约束。

S5 的 transfer score 为 0～1：45% 模式置信度、25% 对数频次、20% 独立任务覆盖、10% 指数时效；最多五个高分模式按最高分 70% 和均值 30% 聚合。S1～S4 继续使用 `1 / priority`。Bandit 评分公式未修改，S5 仍必须先执行一个探索批次。

## Planner 与 API

LLM 只接收 `available`、`pattern_count`、主要模式类型、最高置信度和建议 S5 最大预算。无知识时 Policy Validator 返回 S5 未启用及缺少迁移知识问题；LLM 非法输出按原机制降级 Rule Planner。

知识查询接口为 `GET /api/feedback/patterns`，支持 `target_type`、`algorithm`、`pattern_type`、`minimum_confidence` 和 `limit`。

## 测试覆盖

- PatternExtractor 示例、空串、Unicode、超长边界、年份上下界和替换上下文；
- run 幂等、task 去重、事务回滚、支持度过滤、稳定排序、置信度范围和数据库脱敏；
- S5 无知识/有知识、当前种子、稳定顺序、全局去重、两级预算、来源、长度和换行；
- Rule/LLM Planner、Policy 门控、LLM 摘要脱敏和非法 S5 降级；
- S5 首轮探索、高低收益调度、真实 transfer score、自动反馈、重复 finalize 和数据库重启。

最终验收命令与结果记录在本次任务报告中。
