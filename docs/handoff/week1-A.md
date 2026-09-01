# 第 1 周甲任务交接

交接日期：2026-09-01
交付版本：0.1.0
范围：项目骨架、FastAPI、数据库、公共数据结构、任务 API、联调基础

## 交付结论

甲负责的第 1 周任务已形成可独立启动和测试的后端基础。当前服务可上传目标文件、创建任务、分页查询任务、读取详情并按状态机更新任务状态；所有数据持久化到 SQLite。Analyzer、Planner、Mock Executor 和 Streamlit 页面没有在本提交中代做，已通过稳定契约为乙、丙保留接入面。

## 主要交付物

- `src/sage_pass/main.py`：应用工厂、生命周期、CORS、统一异常响应；
- `src/sage_pass/models.py`：Task、PRIR、StrategyRun、File 数据表；
- `src/sage_pass/schemas.py`：团队统一字段、枚举、预算校验；
- `src/sage_pass/contracts.py`：Analyzer、Planner、Executor 的最小 Protocol；
- `src/sage_pass/routes.py`：文件与任务 HTTP API；
- `src/sage_pass/service.py`：任务状态机、安全文件落盘、ID 与时间生成；
- `tests/`：核心接口回归测试；
- `docs/API.md` 与 `docs/openapi.json`：人工和机器可读接口文件；
- `docs/统一接口.docx`：团队提供的原始接口基线。

## 设计决定

1. 使用 SQLite 作为第 1 周默认数据库，数据库地址可通过 `SAGE_DATABASE_URL` 替换。
2. `task_id`、`file_id` 使用带类型前缀的随机公开 ID，避免依赖单机自增序号。
3. 文件名只保留用于展示；服务器存储名由 `file_id` 生成，API 不暴露真实路径。
4. 任务状态只能单向推进，非法跳转返回 `409 INVALID_TASK`，重复写同一状态保持幂等。
5. 公共 Pydantic 契约与数据库 ORM 分离，后续模块不需要直接依赖数据库对象。
6. 甲的提交不注册尚未实现的 Analyzer/Planner/Executor 路由，避免前端误把占位响应当成真实接口。

## 乙接入说明

1. 实现 `contracts.py` 中的 `Analyzer`、`Planner`、`Executor` Protocol。
2. Analyzer 成功后写入 `PRIRModel`，并通过状态 API 将任务从 `created` 更新到 `analyzed`。
3. Planner 必须复用 `StrategyPlan`，其校验器会拒绝策略时间预算总和超过任务预算。
4. Mock Executor 可写入一条或多条 `StrategyRunModel`；任务状态按 `planned -> running -> completed/failed` 推进。
5. 错误通过 `AppError` 返回团队约定的错误包络，不直接抛裸字符串或 FastAPI 默认响应。

## 丙联调说明

- Streamlit 默认可从 `http://127.0.0.1:8000` 调用；8501 端口已加入默认 CORS 白名单。
- 创建 Hash 任务直接传 `target.content`；文件任务先调 `/api/files`，再把 `file_id` 放入任务。
- Swagger UI 位于 `/docs`，可直接查看和试调当前接口。
- 任务列表接口支持 `limit`、`offset`；详情接口返回前端展示 PRIR 前所需的原始任务信息。

## 启动与验证

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m uvicorn sage_pass.main:app --app-dir src --reload
```

环境变量示例见 `.env.example`。运行目录下会自动创建 `data/sage_pass.db` 与 `data/uploads/`。

本次交付验证结果：

- `pytest`：8 项通过；
- `compileall`：源码与测试均通过字节码编译检查；
- `openapi.json`：由当前 FastAPI 应用自动生成，避免手写契约漂移。
- Uvicorn 实机冒烟：`/health`、`/openapi.json` 和 `POST /api/tasks` 分别返回 200、200、201。

## 当前限制与下一步

- 当前通过 `Base.metadata.create_all()` 建表；多人开始修改数据表前应引入 Alembic 迁移。
- 上传文件只完成安全接收与元数据登记，尚未解析 ZIP/PDF/Office。
- 未实现真实口令恢复、候选生成或外部工具调用；第 1 周 Mock Executor 由乙接入。
- 默认上传上限为 10 MiB，可通过环境变量调整；正式部署还需增加认证、授权与速率限制。
- 本系统仅用于获得授权的离线安全评测。
