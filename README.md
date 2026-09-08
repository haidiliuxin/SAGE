# SAGE-Pass

SAGE-Pass 是面向异构离线口令安全评测任务的智能策略编排系统。本仓库已完成后端基础与 Mock 链路、真实执行、S1～S5 候选生成、执行控制、Bandit 自适应调度，以及跨任务 Feedback Engine v2：系统从 completed 真实运行中提取抽象口令结构，事务性聚合 Pattern Knowledge，并在后续相关任务中生成 S5 迁移候选。

> Analyzer、Planner 与 Executor 均按团队统一接口实现。Mock 执行用于第一周链路演示；`mode: real` 会调用本机 Hashcat（ZIP 目标还需 zip2john）执行真实恢复，时间/候选预算用尽会自动停止。Planner 支持 OpenAI Responses API 和 OpenAI 兼容的 Chat Completions API（包括硅基流动）。

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
- Real Executor：`mode: real` 按 Bandit 选择候选批次运行 Hashcat，写回 `StrategyRunModel` 并把任务推进到终态，运行中可暂停、继续或取消；
- S1 Baseline：后端生成有序基础候选，并支持请求方提供可选的高优先级补充候选；
- S2 Rule：根据计划参数执行首字母大写、全大写/小写、数字/年份/符号后缀及常见字符替换；
- S3 PCFG-lite：按有限结构模板概率稳定展开词、年份、数字和符号组合；
- S4 Context：规范化关键词，派生拼音与缩写，并组合任务年份、地区和组织词；
- 候选管线：为候选保留策略和来源元数据，跨策略稳定去重、按策略候选预算截断、每批 1000 条输出，并保证 Hashcat 单行输入约束；
- 策略执行统计：同一策略的多个 Hashcat 批次共享时间预算，累计 `tested`、`recovered`、耗时和成功率；
- 执行控制（第 3 周）：分批执行的**暂停/继续/取消**与实时状态（`paused`）；Mock 暂停冻结进度，Real 在候选批次边界暂停、继续后恢复；
- 持久化与断点续跑（第 3 周甲后）：真实运行以 `RunRecordModel` 落库目标/计划/候选批次与逐批进度/Bandit 统计检查点，服务重启后自动从断点续跑（跳过已消费批次）；其余残留 `running/paused` 任务由启动收尾避免状态悬挂；
- Bandit Scheduler（第 3 周）：把目标组与策略作为 Arm，先按计划优先级各探索一个候选批次，再根据成功概率、近期收益、计划先验和时间成本评分选择下一批；
- Feedback Engine v2：completed 真实 run 自动抽取长度、字符类别、结构签名、数字位置、抽象前后缀、大小写、年份和常见替换，以事务和唯一 run 标记幂等聚合跨任务 Pattern Knowledge；
- S5 Transfer：达到最低观察数和任务数的同作用域模式可作用于当前任务授权种子，生成有界、可解释、稳定去重的迁移候选；历史恢复明文不会写入 Pattern Knowledge，也不会发送给 LLM；
- 迁移评分：S5 的 `transfer_score` 综合模式置信度、频次、任务覆盖和时效性后传入既有 `ArmSpec`，Bandit 评分公式和首轮探索语义保持不变；
- 只读知识 API：`GET /api/feedback/patterns` 支持作用域、模式类型、最低置信度和数量过滤，不返回恢复明文、Hash 或文件内容；
- Feedback Engine v2：提取长度、字符类别、结构签名、数字位置、抽象前后缀、大小写、年份和常见替换；通过 `feedback_runs` 保证重复 finalize 幂等，并在同一事务中更新 Pattern Knowledge；
- S5 Transfer：只把历史抽象模式应用于当前任务提供的关键词、S1 基线和合法规则种子，不读取或复用历史恢复明文；候选保持惰性、稳定排序、全局去重和来源元数据；
- 历史评分：S5 的 Pattern Knowledge 频次、任务覆盖、置信度和时效衰减形成 0～1 `transfer_score`，注入现有 `ArmSpec`；Bandit 公式和首轮探索规则保持不变；
- 只读知识 API：`GET /api/feedback/patterns` 支持按目标类型、算法、模式类型和最低置信度过滤，响应不包含恢复明文、Hash 或文件内容；
- 自适应预算与停止：Hashcat 每批返回的测试数、恢复数和耗时会更新调度评分；任务总预算、策略预算、时间预算或候选耗尽后停止继续分配；
- LLM Planner：只向模型发送结构化 PRIR，使用严格 JSON Schema 输出，支持温度、超时、最大输出 token、进程内 TTL/LRU 缓存及异常降级；
- Policy Validator：在计划进入执行链路前校验策略白名单、目标适用性、双预算、优先级和参数范围；
- Rule Planner：无上下文按 S1→S2→S3，有上下文追加 S4；慢 Hash 将上下文高概率策略提前并限制候选池规模；
- pytest 覆盖任务、文件、状态机、候选生成、适配器、Mock/Real 执行、Bandit 评分、预算停止和策略统计链路。

## 本地启动

PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn sage_pass.main:app --app-dir src --reload
```

项目要求 Python 3.11 或更高版本，推荐 Python 3.12。第二条命令必须显示
`Python 3.11.x` 或 `Python 3.12.x`；不要复用由 Python 3.9 创建的 `.venv`。
使用 `pip install -e ".[dev]"` 会先读取 `pyproject.toml` 并校验 Python 版本，
从而避免依赖看似安装成功、启动时才因新版依赖语法报错。

如果已有 `.venv` 使用了错误的 Python 版本，请先退出虚拟环境，将旧目录改名后重建：

```powershell
deactivate
Rename-Item -LiteralPath .venv -NewName .venv-py39-backup
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

启动后可访问：

- Swagger UI：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>
- 健康检查：<http://127.0.0.1:8000/health>

默认数据库为 `data/sage_pass.db`，上传目录为 `data/uploads/`。配置项见 `.env.example`。

真实执行需在本机提供 Hashcat（ZIP 目标另需 zip2john），通过 `SAGE_HASHCAT_PATH` / `SAGE_ZIP2JOHN_PATH` 配置；未配置时 mock 链路不受影响，真实执行返回清晰的 `503` 提示。

启用 LLM Planner：

```text
SAGE_PLANNER_TYPE=llm
OPENAI_API_KEY=...
SAGE_LLM_MODEL=gpt-4.1-mini
```

使用硅基流动：

```text
SAGE_PLANNER_TYPE=llm
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
SAGE_LLM_API_STYLE=chat_completions
SAGE_LLM_MODEL=deepseek-ai/DeepSeek-V4-Flash
```

`responses` 模式调用 OpenAI Responses API；`chat_completions` 模式调用兼容的 `/chat/completions`，并通过 `response_format.json_schema` 请求固定 JSON。修改 `.env` 后需完全重启后端进程。

其余生成限制和缓存配置见 `.env.example`。LLM 请求、输出校验或预算校验失败时会降级，并在 `warnings` 中说明。

如需完全不使用 LLM，可设置 `SAGE_PLANNER_TYPE=rule`。当选择 `llm` 但缺少 API Key，或者 LLM 请求/输出校验失败时，系统会使用 Rule Planner；规则规划也无法处理目标时才最终降级至 Mock。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

适配器与真实执行测试使用可控仿真可执行程序（见 `tests/sim_binaries.py`），不需要本机安装真实 hashcat/zip2john。

接口详情见 [docs/API.md](docs/API.md)，各周交接见 [docs/handoff/week1-A.md](docs/handoff/week1-A.md)、[docs/handoff/week1-B.md](docs/handoff/week1-B.md)、[docs/handoff/week2-A.md](docs/handoff/week2-A.md)、[docs/handoff/week2-B.md](docs/handoff/week2-B.md)、[docs/handoff/week2-C-llm-planner.md](docs/handoff/week2-C-llm-planner.md)、[docs/handoff/week3-A.md](docs/handoff/week3-A.md)、[docs/handoff/week3-B.md](docs/handoff/week3-B.md) 与 [docs/handoff/feedback-engine-v2.md](docs/handoff/feedback-engine-v2.md)。

## 目录

```text
src/sage_pass/
  main.py            FastAPI 应用与异常处理
  routes.py          HTTP 路由（含 real/mock 执行分派）
  analyzer.py        Analyzer 与 PRIR 持久化转换（ZIP 真实解析）
  planner.py         Mock/Rule/LLM Planner、结构化输出与缓存
  policy.py          LLM 策略计划白名单与安全约束校验
  candidate_types.py  候选值与来源元数据
  pcfg_lite.py        S3 有限概率模板及惰性展开
  context.py          S4 规范化、拼音、缩写与上下文组合
  candidate_generator.py  S1～S5 候选生成、去重、预算与批次输出
  patterns.py        确定性口令结构抽取（仅产生抽象特征）
  feedback.py        completed real run 的事务聚合与幂等处理
  transfer.py        S5 候选生成、知识摘要、时效评分
  scheduler.py       Bandit 批次调度、评分、探索与停止条件
  executor.py        Mock Executor
  real_executor.py   Real Executor：Bandit 选批、候选执行、策略统计与运行态
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
