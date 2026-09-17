# B 部分前端添加文档

## 1. 本次范围与交付状态

本次完成三个接入点：研究数据只读接口、执行工作台研究面板、历史运行详情与完整日志下载。

未增加调度算法选择表单、盐条件编辑、回放实验页面或成本标定页面；调度器仍使用现有服务端配置。B 的算法、奖励公式、成本拟合和实际执行流程保持既有实现。

代码、接口约定和验收用例已补齐；按用户要求，未运行 pytest、前端测试、构建、浏览器验收或真实 Hashcat。此前的测试通过记录不代表本次接入已通过验收。

## 2. 页面入口

### 执行工作台

真实／Mock 执行取得 `run_id` 后，工作台增加“调度与研究记录”面板。研究展示独立请求，不因查询失败而中断原有执行流程。

| 区域 | 内容 |
| --- | --- |
| 本次配置 | 从研究日志读取实际 policy、执行模式、版本、奖励权重、初始预算；展开查看完整脱敏配置 |
| 状态与预算 | 持久化运行状态、最近决策轮次、剩余时间与候选预算、停止原因 |
| 结果汇总 | 有完整反馈的轮次、提交量、测试量、新增恢复目标、批次耗时、累计评价奖励 |
| 最近决策 | 调度单元 Arm、策略、批量、时间上限、切换原因和该批次的事件状态 |
| 评分表 | 可用批量、决策前已选次数、是否选中、各算法已有的评分分解；未评分不补造数值 |
| 反馈与奖励 | 最近完成轮次的实际反馈、恢复收益、三类惩罚、统一评价奖励；UCB 学习收益单列 |
| 成本估计 | 更新后的启动开销、单候选耗时、完整／不完整样本数、估计质量、近期与上一批吞吐 |
| 时间线 | 最近 50 条事件；向前翻页查看更早事件，或回到最新；展开查看脱敏的决策前后状态 |
| 下载 | 下载该运行的完整 JSONL 研究日志 |

“预计本批耗时”取自决策前评分中的 `predicted_seconds`，与该决策的批量对应；成本区系数取自最近状态，可能已吸收本轮反馈，不混作同一时刻的预测。

页面对运行中／暂停的运行约每 3 秒刷新；读取失败提示错误并约 5 秒后重试，已有数据保留并提示可能过期。查看较早事件时只更新摘要，避免轮询把历史页跳回最新。完成且已有停止原因后停止自动轮询，仍可手动刷新。

### 任务记录 → 详情 → 历史运行与研究详情

按任务分页列出运行编号、模式、状态和启动时间，按启动时间倒序排列。每页 20 项；同一真实运行在策略表与运行记录表中只出现一次。

点击“查看研究详情”进入执行工作台，并保存地址片段：

```text
http://localhost:5173/#run=运行编号
```

刷新页面或重新打开该地址后，根据 `run_id` 从后端重读研究详情。不会重新提交执行请求，也不依赖浏览器保存候选、任务明文或全量日志。查看其他历史运行时隐藏当前活动任务的执行按钮和过程卡片，防止误把操作作用于另一个任务。

恢复的是研究详情视图；原有启动表单和进程内工作台快照不从研究日志重建。活动运行的暂停／继续仍遵循原有执行控制入口。

## 3. HTTP 接口与字段约定

新增接口全部为 GET，统一使用 `/api` 前缀和现有错误结构。Pydantic 契约位于 `src/sage_pass/research_api.py`，TypeScript 契约位于 `frontend/src/research-types.ts`。

### 3.1 查询任务运行列表

```text
GET /api/tasks/{task_id}/runs?limit=20&offset=0
```

返回 `items / total / limit / offset`。每个 item 包含：

```text
run_id, task_id, mode, status, started_at, finished_at
```

`limit` 为 1～100；时间不存在时为 null。优先使用真实运行持久化记录，兼容只有策略行的 Mock 运行。查询不会调用会推进 Mock 状态的旧状态／结果接口。

### 3.2 查询研究摘要

```text
GET /api/runs/{run_id}/research
```

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 本接口版本，目前为 1 |
| `run_id / task_id / status` | 持久化运行身份与状态 |
| `available` | 是否存在研究事件；false 表示暂无数据，不能解释为零收益实验 |
| `configuration` | 最近一次 `run_started` 中保存的配置；不是当前系统配置 |
| `latest_decision` | 最近的开始／完成／中断决策事件，包含评分、动作与切换原因 |
| `latest_completed` | 最近具有完整反馈的决策；运行中的当前批次可能晚于此字段 |
| `latest_state` | 最近事件的更新后／最终状态，或批次开始前状态 |
| `stop_reason` | 最近一次启动之后记录的停止原因；无记录为 null |
| `totals` | 按下述口径聚合的研究结果 |
| `through_sequence` | 此次摘要读取到的日志边界 |

`configuration` 包含 policy 类型／版本／参数、reward 版本／权重／初始预算、学习收益定义、执行模式、seed、恢复轮次、前一 Arm、确定性选择约定、可选的脱敏验证画像。旧日志没有验证画像时为 null。

`latest_state` 包含 `arms`、`remaining_candidates`、`remaining_time`。时间超预算造成的负剩余值保留，不截成零。

`totals` 字段：

```text
completed_rounds, submitted_candidates, tested_candidates,
recovered_targets, duration, evaluation_reward
```

汇总只使用 `decision_completed`。同一 run 的同一 `round_index` 在恢复后出现多条完成记录时，采用 sequence 最大的一条，避免逻辑轮次重复计入。完整导出仍保留所有尝试。

这是已有完整反馈的逻辑轮次汇总，不是对断电前未记账物理工作的估算。未知反馈、仅有批次开始事件以及被覆盖的重试不计入；`duration` 是计入的批次耗时之和，不是含暂停和页面等待的墙钟耗时。主运行状态和研究日志来自两个持久化存储，正在运行时可能存在短暂写入先后差异；摘要内的日志读取使用同一 SQLite 事务边界。

### 3.3 查询研究事件

```text
GET /api/runs/{run_id}/research/events?latest=true&limit=50
GET /api/runs/{run_id}/research/events?before_sequence=123&limit=50
GET /api/runs/{run_id}/research/events?after_sequence=123&limit=50
```

- `latest=true`：取最近一页；`before_sequence`：取更早一页；`after_sequence`：增量向后读取。
- 三种方向不能混用。省略方向时等价于 `after_sequence=0`。
- `limit` 为 1～200；所有返回页内部均按 sequence 升序排列。
- 返回 `items / next_after_sequence / next_before_sequence / has_more`；`has_more` 指本次查询方向还有数据。
- sequence 是整个研究数据库中的游标，可能不连续；不能把它当轮次。

每条事件固定外层字段：

```text
sequence, schema_version, run_id, attempt_id, round_index, event_type, payload
```

`payload` 沿用 `ResearchRecorder` 的版本化脱敏结构，不复制执行器快照。决策事件中主要包含：

```text
available_arms, available_batches, prior_state, scores,
decision { arm_id, strategy_id, candidate_limit, time_limit, exploration },
selection_rule, scores_used_for_selection, switch_reason,
feedback, reward, reward_breakdown, learning_reward,
updated_state, stop_reason
```

开始事件尚无反馈与奖励；中断事件的反馈／奖励可以为 null；非 UCB 事件没有 UCB 学习收益。不同算法的评分字段不同，前端按实际存在的字段展示。固定顺序／轮询的 `scores_used_for_selection=false` 表示评分仅供诊断。

### 3.4 下载完整日志

```text
GET /api/runs/{run_id}/research/download
```

返回 UTF-8 JSONL：`Content-Type: application/x-ndjson`，附件文件名 `research.jsonl`。响应头 `X-Research-Through-Sequence` 记录请求开始时的最大事件 sequence。

下载按 run 过滤，包含该边界之前的全部事件和尝试，不受页面 50 条、执行器内存 200 条或检查点 50 条限制。下载过程中新增事件留待下一次下载。后端每次读取最多 200 条后释放 SQLite 连接，再向客户端发送，避免慢速下载一直持有读锁并阻碍执行器写日志。

### 空数据与错误

- 运行／任务不存在：404。
- 存在运行但没有研究日志：摘要 `available=false`；事件为空页；下载 404。
- 游标方向冲突或越界：422。
- 读取到不支持的研究日志版本：拒绝解释；当前支持日志版本 1。
- 查询不存在的日志库不会创建空数据库。

## 4. 数据口径与隐私

1. `recovered_targets` 表示研究反馈中的新增目标数；不能拿它与旧候选成功率字段混算。
2. UCB `learning_reward` 与统一评价 `reward` 分别显示，负评价奖励原样保留。
3. 未知吞吐、重复测量、评分显示“—”或“暂无评分”，不补零。
4. 成本 `cost_confidence` 显示为“估计质量（0–1）”，不是统计置信概率。
5. API、时间线和下载只读研究日志的脱敏投影；不返回候选明文、恢复明文、目标 Hash、执行器原始 snapshot、stdout 或 stderr。旧结果接口的恢复条目不会并入研究详情。
6. 服务重启后读取依赖原有主数据库及研究 SQLite 文件仍在原位置。默认研究库为配置的 upload_dir 同级 `research/real.sqlite3`；通过现有执行器注入自定义日志库时，读接口跟随该库。
7. 旧运行或 Mock 缺少研究日志时不追溯伪造数据。真实 Hashcat `tested` 的更细单位校正仍属于既有 A/B 接口对齐边界，本次没有改写其计数算法。

## 5. 文件位置

| 文件 | 用途 |
| --- | --- |
| `src/sage_pass/research_api.py` | 响应契约、运行列表、摘要、分页事件、流式下载 |
| `src/sage_pass/routes.py` | 注册新增只读路由 |
| `src/sage_pass/real_executor.py` | 提供跟随配置的只读日志入口 |
| `frontend/src/research-types.ts` | 前端研究字段契约 |
| `frontend/src/ResearchPanel.tsx` | 工作台研究展示与历史运行列表组件 |
| `frontend/src/api/client.ts` | 新增接口调用及下载 URL |
| `frontend/src/App.tsx` | 工作台、历史入口、运行地址恢复 |
| `frontend/src/styles.css` | 研究区域布局、表格和窄屏样式 |
| `tests/test_research_api.py` | 后端边界、重启、分页、脱敏、下载边界验收 |
| `frontend/tests/regressions.test.mjs` | 新增展示、分页、异步响应隔离、地址恢复、历史选择验收 |

复用了项目已有 FastAPI、SQLite 研究日志、React 和 esbuild 测试设施；没有新增运行依赖，没有移植外部仓库代码，也没有数据库表结构迁移。

## 6. 用户验收

在项目根目录、`sage` 环境运行：

```powershell
python -m pytest tests/test_research_api.py tests/test_real_research_logging.py tests/test_reward_logging.py tests/test_ucb_cost.py
python -m pytest tests/test_scheduler.py tests/test_a_week1_core.py tests/test_week3_recovery.py tests/test_decision_replay.py tests/test_calibration_salts.py
npm --prefix frontend test
npm --prefix frontend run build
```

浏览器人工验收清单：

- [ ] 启动一个已有环境支持的真实运行，工作台展示本次实际调度器、决策和完整反馈。
- [ ] 核对统一奖励与学习收益分别展示；未知吞吐／重复测量没有变成零。
- [ ] 超过 50 条研究事件时查看更早页，再回到最新；检查没有跳页、重复或跨运行数据。
- [ ] 从任务记录打开指定历史运行，刷新浏览器后仍显示同一运行。
- [ ] 结束运行后重启后端，用相同地址查看摘要和下载日志；无需重新执行任务。
- [ ] 下载 JSONL，条数和该 run 的落盘事件一致；不含候选或恢复明文。
- [ ] 查看 Mock／无日志旧运行，得到明确空状态。
- [ ] 在运行 A 与历史运行 B 之间快速切换，B 不显示 A 的晚到响应或暂停／取消按钮。
- [ ] 后端短暂离线时显示读取错误，恢复后能重读；研究查询错误不取消正在执行的任务。
- [ ] 在窄屏中检查评分表可横向滚动，事件详情与运行编号不撑破布局。

本次只做了静态代码检查，运行结果以用户执行上述验收为准。
