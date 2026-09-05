# SAGE-Pass

SAGE-Pass 是面向异构离线口令安全评测任务的智能策略编排系统。本仓库当前完成第 1 周后端基础与 Mock 链路、第 2 周甲的真实执行层：FastAPI 项目、SQLite 持久化、公共数据结构、任务 API、文件接入、PRIR 分析、Mock Planner、Mock/Real Executor、Hashcat 适配器与 ZIP（WinZip AES）真实接入。

> Analyzer、Planner 与 Executor 均按团队统一接口实现。Mock 执行用于第一周链路演示；`mode: real` 会调用本机 Hashcat（ZIP 目标还需 zip2john）执行真实恢复，时间/候选预算用尽会自动停止。LLM 规划、动态调度与持久化恢复由后续周次接入。

## 已完成

- FastAPI 应用工厂、健康检查、CORS 和统一错误响应；
- SQLAlchemy 数据库模型：`TaskModel`、`PRIRModel`、`StrategyRunModel`、`FileModel`；
- Pydantic 公共契约：Task、PRIR、StrategyPlan、执行状态与结果；
- 任务创建、列表、详情、受控状态更新 API；
- 文件上传与元数据查询 API，任务之间只传 `file_id`；
- Analyzer：支持 Hash 文本和 ZIP/PDF/Office 元数据输入，ZIP 经 zip2john 真实解析（无工具时降级提示），生成并持久化 PRIR；
- Mock Planner：根据 PRIR 生成 `mock` 策略计划；
- Mock Executor：启动模拟执行、查询执行状态、返回最终模拟结果；
- Hashcat Adapter：真实执行的启动、停止（取消）、时间预算自动停止与恢复结果解析（含 `$HEX[]`）；
- ZIP Adapter：zip2john 提取 WinZip AES（`$zip2$`，hashcat 13600），传统 PKZIP 明确报不支持；
- Real Executor：`mode: real` 逐策略运行 Hashcat，写回 `StrategyRunModel` 并把任务推进到终态，运行中可取消；
- pytest 覆盖任务、文件、状态机、乙链路、适配器与真实执行链路。
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

真实执行需在本机提供 Hashcat（ZIP 目标另需 zip2john），通过 `SAGE_HASHCAT_PATH` / `SAGE_ZIP2JOHN_PATH` 配置；未配置时 mock 链路不受影响，真实执行返回清晰的 `503` 提示。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

适配器与真实执行测试使用可控仿真可执行程序（见 `tests/sim_binaries.py`），不需要本机安装真实 hashcat/zip2john。

接口详情见 [docs/API.md](docs/API.md)，各周交接见 [docs/handoff/week1-A.md](docs/handoff/week1-A.md)、[docs/handoff/week1-B.md](docs/handoff/week1-B.md) 与 [docs/handoff/week2-A.md](docs/handoff/week2-A.md)。

## 目录

```text
src/sage_pass/
  main.py            FastAPI 应用与异常处理
  routes.py          HTTP 路由（含 real/mock 执行分派）
  analyzer.py        Analyzer 与 PRIR 持久化转换（ZIP 真实解析）
  planner.py         Mock Planner
  executor.py        Mock Executor
  real_executor.py   Real Executor：逐策略 Hashcat 执行与运行态
  hashcat_adapter.py Hashcat 适配器（启动/停止/超时/结果解析）
  zip_adapter.py     zip2john ZIP（WinZip AES）适配器
  schemas.py         公共输入输出契约
  contracts.py       Analyzer/Planner/Executor Protocol
  models.py          数据库模型
  service.py         任务状态机与文件存储
  repository.py      数据访问
tests/               自动化测试
docs/                接口与交接材料
```

## 合规边界

本项目用于经过授权的离线口令安全评测。请勿将其用于未获授权的目标或凭据。
