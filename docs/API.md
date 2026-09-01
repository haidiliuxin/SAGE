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

## 已定义、待乙实现的模块契约

Pydantic 模型位于 `src/sage_pass/schemas.py`，Python Protocol 位于 `src/sage_pass/contracts.py`。

- `Analyzer.analyze(TaskDetail) -> PRIR`
- `Planner.plan(PRIR) -> StrategyPlan`
- `Executor.start(TaskDetail, StrategyPlan) -> ExecutionStarted`
- `Executor.status(run_id) -> RunStatus`
- `Executor.result(run_id) -> RunResult`

预留 HTTP 路径与团队接口保持一致：

- `POST /api/tasks/{task_id}/analyze`
- `POST /api/tasks/{task_id}/plan`
- `POST /api/tasks/{task_id}/execute`
- `GET /api/runs/{run_id}/status`
- `GET /api/runs/{run_id}/result`

这些路径当前不会注册，避免返回伪造业务数据。乙接入实现后再挂载路由。

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

当前使用的代码：`INVALID_TASK`、`TASK_NOT_FOUND`、`INTERNAL_ERROR`。已在公共契约中为后续模块保留 `ANALYZE_FAILED`、`PLAN_FAILED`、`EXECUTION_FAILED` 和 `BUDGET_EXCEEDED` 的格式约定。
