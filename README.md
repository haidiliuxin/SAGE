# SAGE-Pass

SAGE-Pass 是面向异构离线口令安全评测任务的智能策略编排系统。本仓库当前完成第 1 周甲负责的后端基础：FastAPI 项目、SQLite 持久化、公共数据结构、任务 API 和文件接入骨架。

> 当前版本不执行真实口令恢复。Analyzer、Planner 和 Executor 仅定义契约，分别由后续模块按统一接口接入。

## 已完成

- FastAPI 应用工厂、健康检查、CORS 和统一错误响应；
- SQLAlchemy 数据库模型：`TaskModel`、`PRIRModel`、`StrategyRunModel`、`FileModel`；
- Pydantic 公共契约：Task、PRIR、StrategyPlan、执行状态与结果；
- 任务创建、列表、详情、受控状态更新 API；
- 文件上传与元数据查询 API，任务之间只传 `file_id`；
- pytest 覆盖任务、文件、状态机和错误格式。

## 本地启动

PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn sage_pass.main:app --app-dir src --reload
```

启动后可访问：

- Swagger UI：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>
- 健康检查：<http://127.0.0.1:8000/health>

默认数据库为 `data/sage_pass.db`，上传目录为 `data/uploads/`。配置项见 `.env.example`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

接口详情见 [docs/API.md](docs/API.md)，本周交接见 [docs/handoff/week1-A.md](docs/handoff/week1-A.md)。

## 目录

```text
src/sage_pass/
  main.py          FastAPI 应用与异常处理
  routes.py        HTTP 路由
  schemas.py       公共输入输出契约
  contracts.py     Analyzer/Planner/Executor Protocol
  models.py        数据库模型
  service.py       任务状态机与文件存储
  repository.py    数据访问
tests/             自动化测试
docs/              接口与交接材料
```

## 合规边界

本项目用于经过授权的离线口令安全评测。请勿将其用于未获授权的目标或凭据。
