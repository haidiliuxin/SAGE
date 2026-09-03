# SAGE-Pass

SAGE-Pass 是面向异构离线口令安全评测任务的智能策略编排系统。本仓库当前完成第 1 周后端基础与乙方 Mock 链路：FastAPI 项目、SQLite 持久化、公共数据结构、任务 API、文件接入、PRIR 分析、Mock Planner 和 Mock Executor。

> 当前版本不执行真实口令恢复。Analyzer、Planner 和 Executor 已按第一周统一接口接入 Mock 实现，真实 Hashcat/JtR、调度算法和 LLM 由后续版本替换。

## 已完成

- FastAPI 应用工厂、健康检查、CORS 和统一错误响应；
- SQLAlchemy 数据库模型：`TaskModel`、`PRIRModel`、`StrategyRunModel`、`FileModel`；
- Pydantic 公共契约：Task、PRIR、StrategyPlan、执行状态与结果；
- 任务创建、列表、详情、受控状态更新 API；
- 文件上传与元数据查询 API，任务之间只传 `file_id`；
- Analyzer：支持 Hash 文本和文件元数据输入，生成并持久化 PRIR；
- Mock Planner：根据 PRIR 生成第一周 `mock` 策略计划；
- Mock Executor：启动模拟执行、查询执行状态、返回最终模拟结果；
- pytest 覆盖任务、文件、状态机、乙方链路和错误格式。

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

接口详情见 [docs/API.md](docs/API.md)，本周交接见 [docs/handoff/week1-A.md](docs/handoff/week1-A.md) 和 [docs/handoff/week1-B.md](docs/handoff/week1-B.md)。

## 目录

```text
src/sage_pass/
  main.py          FastAPI 应用与异常处理
  routes.py        HTTP 路由
  analyzer.py      第一周 Mock Analyzer 与 PRIR 持久化转换
  planner.py       第一周 Mock Planner
  executor.py      第一周 Mock Executor
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
