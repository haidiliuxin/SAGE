# SAGE-Pass 统一接口（第 1 周）

版本：`0.1.0`
基础地址：`http://127.0.0.1:8000`

本文把团队提供的《统一接口.docx》落实为当前后端契约。字段定义的机器可读版本见同目录 `openapi.json`；运行服务后也可在 `/docs` 联调。

## 统一约定

- 时间预算单位为秒，候选预算为整数。
- 任务使用 `task_id`，上传文件使用 `file_id`，执行使用 `run_id`。
- 任务状态：`created`、`analyzed`、`planned`、`running`、`completed`、`failed`、`cancelled`。
- 目标类型：`hash`、`zip`、`pdf`、`office`、`unknown`。
- 验证成本：`low`、`medium`、`high`、`unknown`。
- 策略编号：`S1`、`S2`、`S3`、`S4`、`S5`。
- 无法确定的字段使用 `null` 或 `unknown`，不得编造。
- 第 1 周执行模式只使用 `mock`；真实执行由后续版本接入。

## 甲已实现的 HTTP API

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
{"status": "analyzed"}
```

状态转换由后端约束：

```text
created  -> analyzed | failed | cancelled
analyzed -> planned  | failed | cancelled
planned  -> running  | failed | cancelled
running  -> completed | failed | cancelled
```

重复写入当前状态按幂等成功处理；终态不允许回退。

## 乙已实现的 Mock 链路 API

Pydantic 模型位于 `src/sage_pass/schemas.py`，Python Protocol 位于 `src/sage_pass/contracts.py`。当前版本按第一周统一接口提供 Mock 实现，不执行真实口令恢复。

### 分析任务并生成 PRIR

`POST /api/tasks/{task_id}/analyze`

要求任务当前状态为 `created`。成功后写入 `PRIRModel`，任务状态更新为 `analyzed`。重复分析已处于 `analyzed` 且已有 PRIR 的任务时，返回已有 PRIR。

返回：

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

说明：

- Hash 文本任务会优先使用 `known_algorithm`；未提供时按常见 Hash 形态做规则识别；
- 文件任务支持 `zip`、`pdf`、`office` 的元数据级 PRIR，第一周不解析真实加密结构；
- 无法确认的字段使用 `unknown` 或 `null`。

### 生成策略计划

`POST /api/tasks/{task_id}/plan`

要求任务当前状态为 `analyzed` 或 `planned`，且已有 PRIR。成功后返回 `StrategyPlan`；若任务从 `analyzed` 进入规划，状态更新为 `planned`。

返回：

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
    },
    {
      "strategy_id": "S4",
      "strategy_name": "Context",
      "priority": 2,
      "time_budget": 120,
      "candidate_budget": 40000,
      "reason": "任务提供了上下文信息",
      "parameters": {
        "use_years": true,
        "use_keywords": true
      }
    }
  ],
  "status": "planned",
  "warnings": []
}
```

当前 Mock Planner 固定生成 `S1`；如果 PRIR 表明有上下文，则追加 `S4`。策略时间预算之和由 `StrategyPlan` 校验，不允许超过任务总时间预算。

### 启动模拟执行

`POST /api/tasks/{task_id}/execute`

请求：

```json
{"mode": "mock"}
```

要求任务当前状态为 `planned`，且已有 PRIR。第一周只接受 `mock` 模式。成功后写入一条或多条 `StrategyRunModel`，任务状态更新为 `running`。

返回：

```json
{
  "task_id": "T39FD9B11EB89",
  "run_id": "R10D44A4973C",
  "status": "running",
  "started_at": "2026-09-01T22:05:00+08:00"
}
```

### 查询执行状态

`GET /api/runs/{run_id}/status`

返回：

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
  "message": "正在执行上下文策略"
}
```

Mock 执行会随时间推进进度；完成后返回 `completed`，并把任务状态从 `running` 推进到 `completed`。

### 查询最终结果

`GET /api/runs/{run_id}/result`

返回：

```json
{
  "task_id": "T39FD9B11EB89",
  "run_id": "R10D44A4973C",
  "status": "completed",
  "total_time": 2.0,
  "total_tested": 60000,
  "total_recovered": 3,
  "strategy_results": [
    {
      "strategy_id": "S1",
      "time": 0.67,
      "tested": 20000,
      "recovered": 1,
      "success_rate": 0.00005
    },
    {
      "strategy_id": "S4",
      "time": 1.33,
      "tested": 40000,
      "recovered": 2,
      "success_rate": 0.00005
    }
  ],
  "finished_at": "2026-09-01T22:05:02+08:00"
}
```

调用结果接口会补全 Mock 执行结果；如果任务仍为 `running`，会推进到 `completed`。

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

当前使用的代码：`INVALID_TASK`、`TASK_NOT_FOUND`、`ANALYZE_FAILED`、`PLAN_FAILED`、`EXECUTION_FAILED`、`INTERNAL_ERROR`。已在公共契约中为后续模块保留 `BUDGET_EXCEEDED` 的格式约定。
