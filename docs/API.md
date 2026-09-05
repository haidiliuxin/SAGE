# SAGE-Pass 统一接口（第 2 周）

版本：`0.2.0`
基础地址：`http://127.0.0.1:8000`

本文把团队提供的《统一接口.docx》落实为当前后端契约，并记录第 2 周新增的真实执行能力。字段定义的机器可读版本见同目录 `openapi.json`；运行服务后也可在 `/docs` 联调。

## 统一约定

- 时间预算单位为秒，候选预算为整数。
- 任务使用 `task_id`，上传文件使用 `file_id`，执行使用 `run_id`。
- 任务状态：`created`、`analyzed`、`planned`、`running`、`completed`、`failed`、`cancelled`。
- 目标类型：`hash`、`zip`、`pdf`、`office`、`unknown`。
- 验证成本：`low`、`medium`、`high`、`unknown`。
- 策略编号：`S1`、`S2`、`S3`、`S4`、`S5`。
- 执行模式：`mock`（第一周链路，保留）与 `real`（第 2 周真实执行）。
- 无法确定的字段使用 `null` 或 `unknown`，不得编造。

## 基础 HTTP API

### 健康检查

`GET /health`

```json
{"status": "ok"}
```

### 上传目标文件

`POST /api/files`，请求类型为 `multipart/form-data`，表单字段名为 `file`。

返回 `201`：

```json
{
  "file_id": "F10D44A4973C9",
  "filename": "sample.zip",
  "content_type": "application/zip",
  "size": 1024,
  "sha256": "64位十六进制摘要",
  "created_at": "2026-09-01T22:00:00+08:00"
}
```

文件大小由 `SAGE_MAX_UPLOAD_BYTES` 限制，默认 10 MiB。其他模块只能传递 `file_id`，不传文件路径或文件内容。

### 查询文件元数据

`GET /api/files/{file_id}`

响应结构与上传接口一致，不返回服务器存储路径。

### 创建任务

`POST /api/tasks`

```json
{
  "name": "bcrypt测试任务",
  "target": {
    "type": "hash",
    "content": "$2b$12$...",
    "file_id": null
  },
  "known_algorithm": "bcrypt",
  "time_budget": 300,
  "candidate_budget": 100000,
  "context": {
    "keywords": ["学校名称", "张三"],
    "years": [2024, 2025],
    "region": "北京",
    "organization": "示例大学",
    "description": "其他补充信息"
  }
}
```

返回 `201`：

```json
{
  "task_id": "T39FD9B11EB89",
  "status": "created",
  "created_at": "2026-09-01T22:00:00+08:00"
}
```

校验规则：

- `hash` 必须提供 `target.content`；
- `zip`、`pdf`、`office` 必须提供已上传的 `target.file_id`；
- `unknown` 必须至少提供 `content` 或 `file_id`；
- `time_budget` 和 `candidate_budget` 必须大于 0。

### 查询任务列表

`GET /api/tasks?limit=20&offset=0`

`limit` 为 1～100，按创建顺序倒序返回。

```json
{
  "items": [],
  "total": 0,
  "limit": 20,
  "offset": 0
}
```

### 查询任务详情

`GET /api/tasks/{task_id}`

返回创建时的完整任务内容、当前状态、`created_at` 和 `updated_at`。

### 更新任务状态

`PATCH /api/tasks/{task_id}/status`

```json
{"status": "cancelled"}
```

状态转换由后端约束：

```text
created  -> analyzed | failed | cancelled
analyzed -> planned  | failed | cancelled
planned  -> running  | failed | cancelled
running  -> completed | failed | cancelled
```

重复写入当前状态按幂等成功处理；终态不允许回退。对正在 `running` 的真实执行取消，会同步停止底层 Hashcat 进程。

## Analyzer 与 Planner

### 分析任务并生成 PRIR

`POST /api/tasks/{task_id}/analyze`

要求任务当前状态为 `created`。成功后写入 `PRIRModel`，任务状态更新为 `analyzed`。重复分析已处于 `analyzed` 且已有 PRIR 的任务时，返回已有 PRIR。

Hash 任务返回示例：

```json
{
  "task_id": "T39FD9B11EB89",
  "target_type": "hash",
  "algorithm": "bcrypt",
  "salt": true,
  "verification_cost": "high",
  "context_available": true,
  "candidate_space": null,
  "time_budget": 300,
  "candidate_budget": 100000,
  "status": "analyzed",
  "confidence": 0.9,
  "warnings": []
}
```

分析规则（第 2 周）：

- Hash 文本任务优先使用 `known_algorithm`；未提供时按常见 Hash 形态做规则识别；
- **ZIP 文件任务**（第 2 周接入）：调用 zip2john 提取加密目标，识别成功时返回 `algorithm: "zip-aes"`、`salt: true`、`verification_cost: "medium"`；
- zip2john 未安装/超时/仅传统 PKZIP 时**不失败**，返回 `unknown` 算法并在 `warnings` 中说明降级原因，mock 链路仍可继续；
- `pdf`、`office` 仍为元数据级 PRIR，尚未接入真实解析。

### 生成策略计划

`POST /api/tasks/{task_id}/plan`

要求任务当前状态为 `analyzed` 或 `planned`，且已有 PRIR。成功后返回 `StrategyPlan`；若任务从 `analyzed` 进入规划，状态更新为 `planned`。

```json
{
  "task_id": "T39FD9B11EB89",
  "planner_type": "mock",
  "total_time_budget": 300,
  "strategies": [
    {
      "strategy_id": "S1",
      "strategy_name": "Baseline",
      "priority": 1,
      "time_budget": 60,
      "candidate_budget": 20000,
      "reason": "优先测试高频口令",
      "parameters": {}
    }
  ],
  "status": "planned",
  "warnings": []
}
```

当前 Mock Planner 固定生成 `S1`；如果 PRIR 表明有上下文且预算足够，则追加 `S4`。策略时间预算之和由 `StrategyPlan` 校验，不允许超过任务总时间预算。

设置 `SAGE_PLANNER_TYPE=llm` 并提供 `OPENAI_API_KEY` 后，接口改用 LLM Planner，返回的 `planner_type` 为 `llm`。模型只接收上述结构化 PRIR（不含目标 Hash、文件内容或上下文原文），并通过严格 JSON Schema 在 `S1`～`S4` 中选择策略和分配预算。`SAGE_LLM_API_STYLE=responses` 使用 OpenAI Responses API；`chat_completions` 使用 OpenAI 兼容的 Chat Completions API。硅基流动需同时设置 `OPENAI_BASE_URL=https://api.siliconflow.cn/v1` 和 `SAGE_LLM_API_STYLE=chat_completions`。服务端会再次检查策略唯一性、S4 上下文条件、优先级及时间/候选总预算；API、网络或输出异常时自动返回 Rule 计划，并把降级原因写入 `warnings`。生成限制与 TTL/LRU 缓存参数见 `.env.example`。

设置 `SAGE_PLANNER_TYPE=rule` 可完全跳过 LLM。Rule Planner 在无上下文时按 S1→S2→S3 规划，有上下文时增加 S4；对于 `verification_cost=high` 的慢 Hash，优先级调整为 S1→S4→S2→S3，并把候选池限制为任务上限的 25%。中等、未知和低验证成本默认分别使用候选上限的 60%、50% 和 100%。极小预算无法为所有策略各分配至少一个时间单位和候选时，按优先级保留前几个策略并返回 warning。

Policy Validator 当前规则：白名单为 `S1`～`S4`（`S5` 尚未开放）；目标必须是 `hash`、`zip`、`pdf` 或 `office`，且 `S4` 要求 `context_available=true`；每个已选策略的时间和候选预算必须大于零，两类预算总和均不得超过 PRIR；未知参数、错误参数类型或越界值均拒绝。S1 不接收参数，S2 接收七类规则布尔开关，S3 接收 PCFG 模板数/概率/结构长度，S4 接收上下文来源开关和受候选预算限制的组合数。

## 执行

### 启动执行

`POST /api/tasks/{task_id}/execute`

要求任务当前状态为 `planned`，且已有 PRIR。支持两种模式：

Mock（第一周链路，无需候选）：

```json
{"mode": "mock"}
```

真实执行（第 2 周，Hashcat）：

```json
{
  "mode": "real",
  "candidates": ["123456", "password", "passw0rd"],
  "hashcat_mode": 0,
  "timeout": 60
}
```

字段说明：

- `candidates`：真实执行候选集，最多 100000 条，每条为 1～1024 字符的单行文本，**不能为空列表**；
- `hashcat_mode`：可选。缺省时 Hash 任务由 `known_algorithm` 自动映射（bcrypt=3200、sha256=1400、zip-aes=13600 等），ZIP 任务默认 13600；无法确定时返回 `422`；
- `timeout`：可选，覆盖策略时间预算的每策略秒数上限。

返回：

```json
{
  "task_id": "T39FD9B11EB89",
  "run_id": "R10D44A4973C",
  "status": "running",
  "started_at": "2026-09-01T22:05:00+08:00"
}
```

真实执行按计划逐策略运行：每个策略按候选预算顺序分得候选子集，并带有各自的时间预算；任何情况下 Hashcat 进程都不会超过时间或候选预算上限（到时自动停止）。候选写入临时词表，任务结束后清理。

### 查询执行状态

`GET /api/runs/{run_id}/status`

```json
{
  "task_id": "T39FD9B11EB89",
  "run_id": "R10D44A4973C",
  "status": "running",
  "progress": 0.6,
  "current_strategy": "S4",
  "elapsed_time": 1.2,
  "tested": 36000,
  "recovered": 0,
  "message": "正在执行策略 Context（S4）"
}
```

Mock 与真实执行共用该接口。真实执行进行中可轮询进度；时间预算用尽或候选耗尽后自动进入终态。

### 查询最终结果

`GET /api/runs/{run_id}/result`

真实执行尚未结束时返回 `409 RUN_IN_PROGRESS`；结束后返回：

```json
{
  "task_id": "T39FD9B11EB89",
  "run_id": "R10D44A4973C",
  "status": "completed",
  "total_time": 12.0,
  "total_tested": 60000,
  "total_recovered": 2,
  "strategy_results": [
    {
      "strategy_id": "S1",
      "time": 4.0,
      "tested": 20000,
      "recovered": 1,
      "success_rate": 0.00005
    },
    {
      "strategy_id": "S4",
      "time": 8.0,
      "tested": 40000,
      "recovered": 1,
      "success_rate": 0.000025
    }
  ],
  "finished_at": "2026-09-01T22:05:02+08:00",
  "recovered_items": [
    {
      "target": "$2b$12$...",
      "plaintext": "password"
    }
  ],
  "message": "真实执行已完成"
}
```

`recovered_items` 为去重后的真实恢复结果；`target` 是 Hash 文本或 `$zip2$` 密文行，`plaintext` 是恢复的口令。Mock 结果的这两个字段保持兼容（空列表 / `null`）。

## 环境变量（真实执行）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `SAGE_HASHCAT_PATH` | hashcat 可执行文件路径或命令名 | `hashcat` |
| `SAGE_ZIP2JOHN_PATH` | zip2john 可执行文件路径或命令名 | `zip2john` |

未安装 hashcat 时，`mode: "real"` 的 Hash 任务在启动时返回 `503 EXECUTION_FAILED`（错误信息提示检查 `SAGE_HASHCAT_PATH`）；未安装 zip2john 时，ZIP 分析会降级并给出提示，ZIP 真实执行同样返回 `503`。

## 统一错误响应

```json
{
  "error": {
    "code": "INVALID_TASK",
    "message": "任务输入错误",
    "details": {
      "field": "target.content"
    }
  }
}
```

当前使用的代码：`INVALID_TASK`、`TASK_NOT_FOUND`、`ANALYZE_FAILED`、`PLAN_FAILED`、`EXECUTION_FAILED`、`RUN_IN_PROGRESS`、`INTERNAL_ERROR`。已在公共契约中为后续模块保留 `BUDGET_EXCEEDED` 的格式约定。
