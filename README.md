# SAGE-Pass

SAGE-Pass 是面向异构离线口令安全评测任务的智能策略编排系统。本仓库已完成后端基础与 Mock 链路、真实执行、S1～S5 候选生成、执行控制、Bandit 自适应调度，以及跨任务 Feedback Engine v2：系统从 completed 真实运行中提取抽象口令结构，事务性聚合 Pattern Knowledge，并在后续相关任务中生成 S5 迁移候选。

> Analyzer、Planner 与 Executor 均按团队统一接口实现。Mock 执行用于第一周链路演示；`mode: real` 会调用本机 Hashcat（ZIP 目标还需 zip2john）执行真实恢复，时间/候选预算用尽会自动停止。Planner 支持 OpenAI Responses API 和 OpenAI 兼容的 Chat Completions API（包括硅基流动）。

## 已完成

- FastAPI 应用工厂、健康检查、CORS 和统一错误响应；
- SQLAlchemy 数据库模型：`TaskModel`、`PRIRModel`、`StrategyRunModel`、`FileModel`；
- Pydantic 公共契约：Task、PRIR、StrategyPlan、执行状态与结果；
- I0～I3 信息场景与统一 `InformationProfile`：个人信息支持姓名/昵称、用户名、邮箱局部、电话后缀、生日/年份、地区、组织、兴趣词和其他授权关键词；历史旧口令由独立 `historical_passwords` 输入，Planner 与任务详情只接收脱敏摘要；
- 任务创建、列表、详情、受控状态更新 API；
- 文件上传与元数据查询 API，任务之间只传 `file_id`；
- Analyzer：支持 Hash 文本和 ZIP/PDF/Office 元数据输入，ZIP 经 zip2john 真实解析（无工具时降级提示），生成并持久化 PRIR；
- Mock Planner：根据 PRIR 生成 `mock` 策略计划；
- Mock Executor：启动模拟执行、查询执行状态、返回最终模拟结果；
- Hashcat Adapter：真实执行的启动、停止（取消）、时间预算自动停止与恢复结果解析（含 `$HEX[]`）；
- ZIP Adapter：zip2john 提取 WinZip AES（`$zip2$`，hashcat 13600）与传统 PKZIP / ZipCrypto（`$pkzip2$`，按结构选 17200/17210/17225/17230），两者都可进入真实执行；
- 原生攻击单元（第 2 步）：配置 `SAGE_RULES_PATH` / `SAGE_MASK_LADDER` / `SAGE_HYBRID_MASKS` 后，规划层自动纳入 **S6（掩码/暴力，`-a 3`）** 与 **S7（混合攻击，`-a 6`）**，S2 可改用 hashcat `-r` 规则引擎；掩码直接作为 hashcat 参数、空间由 hashcat 自己枚举，不由后端展开（`SAGE_PLANNER_TYPE=rule`）；
- 真实场景执行（第 1 步）：S1 可切换为 **hashcat 原生词表攻击**（`SAGE_WORDLIST_PATH` 或任务上传的词表文件，由 hashcat 单进程按需流式读取整本字典、不做候选物化），候选管线**惰性流式生成**（`CandidatePlanStream`：只生成被调度到的批次、跨生成器去重索引，重启后按流快照恢复；hashcat 批次使用持久会话目录以支持续跑），决策批次大小可配置（`SAGE_DECISION_BATCH_SIZE`），并支持**命中即停**（`stop_on_hit`，命中后立即结束并记 `all_targets_recovered`）；前端可粘贴/上传 `.txt` 词表作为候选或作为原生字典；
- PDF/Office Adapter（A 冲刺第 2 周）：pdf2john / office2john 提取加密目标，模式覆盖 10400/10500/10600/10700/10510 与 9700/9800/9400/9500/9600；
- 数据库迁移：`migrations.py` 版本化幂等迁移（`schema_migrations`），建表后自动应用；
- Real Executor：`mode: real` 按 Bandit 选择候选批次运行 Hashcat，写回 `StrategyRunModel` 并把任务推进到终态，运行中可暂停、继续或取消；
- S1 Baseline：后端生成有序基础候选，并支持请求方提供可选的高优先级补充候选；
- S2 Rule：根据计划参数执行首字母大写、全大写/小写、数字/年份/符号后缀及常见字符替换；
- S3 PCFG：默认 `pcfg_lite` 按有限结构模板展开；可切换到基于 MIT 许可 `lakiw/pcfg_cracker` 的 `pcfg_full`，加载兼容 ruleset 后按概率流式生成，并输出 `log_probability`；
- S3 Markov：可选 `markov` 生成器复用 `pcfg_cracker` 内置的 MIT 许可 OMEN，实现可配置阶数校验、按 OMEN level 排序、流式批次和游标恢复；
- S4 Personalized：按信息场景自动选择 `context`、`history` 或 `hybrid`，组合授权个人信息、当前用户旧口令结构与 Pattern Knowledge；
- Generator Registry：注册 `baseline`、`rule`、`pcfg_lite`、`pcfg_full`、`markov`、`context`、`history`、`hybrid`、`pattern_knowledge`（并保留 `transfer` 兼容名），统一使用可分批、可快照恢复的运行状态；候选管线只负责按优先级调度、跨生成器稳定去重、预算截断和 Hashcat 输入约束；
- 策略执行统计：同一策略的多个 Hashcat 批次共享时间预算，累计 `tested`、`recovered`、耗时和成功率；
- 执行控制（第 3 周）：分批执行的**暂停/继续/取消**与实时状态（`paused`）；Mock 暂停冻结进度，Real 在候选批次边界暂停、继续后恢复；
- 持久化与断点续跑（第 3 周甲后）：真实运行以 `RunRecordModel` 落库目标、计划、候选流游标、去重 digest 索引与逐批进度/Bandit 统计检查点，服务重启后自动从断点续跑（跳过已消费批次）；其余残留 `running/paused` 任务由启动收尾避免状态悬挂；
- Bandit Scheduler（第 3 周）：把目标组与策略作为 Arm，先按计划优先级各探索一个候选批次，再根据成功概率、近期收益、计划先验和时间成本评分选择下一批；
- Feedback Engine v2：completed 真实 run 自动抽取长度、字符类别、结构签名、数字位置、抽象前后缀、大小写、年份和常见替换，以事务和唯一 run 标记幂等聚合跨任务 Pattern Knowledge；
- S5 Transfer：达到最低观察数和任务数的同作用域模式可作用于当前任务授权种子，生成有界、可解释、稳定去重的迁移候选；历史恢复明文不会写入 Pattern Knowledge，也不会发送给 LLM；
- History Generator：仅处理当前任务授权旧口令，生成大小写、年份、数字、符号、字符替换、词根和历史结构迁移候选，来源展示不含旧口令原文；
- Hybrid Generator：组合个人信息词根、历史结构、当前年份、组织/地区与抽象 Pattern Knowledge；
- 迁移评分：S5 的 `transfer_score` 综合模式置信度、频次、任务覆盖和时效性后传入既有 `ArmSpec`，Bandit 评分公式和首轮探索语义保持不变；
- 只读知识 API：`GET /api/feedback/patterns` 支持作用域、模式类型、最低置信度和数量过滤，不返回恢复明文、Hash 或文件内容；
- Feedback Engine v2：提取长度、字符类别、结构签名、数字位置、抽象前后缀、大小写、年份和常见替换；通过 `feedback_runs` 保证重复 finalize 幂等，并在同一事务中更新 Pattern Knowledge；
- S5 Transfer：只把历史抽象模式应用于当前任务提供的关键词、S1 基线和合法规则种子，不读取或复用历史恢复明文；候选保持惰性、稳定排序、全局去重和来源元数据；
- 历史评分：S5 的 Pattern Knowledge 频次、任务覆盖、置信度和时效衰减形成 0～1 `transfer_score`，注入现有 `ArmSpec`；Bandit 公式和首轮探索规则保持不变；
- 只读知识 API：`GET /api/feedback/patterns` 支持按目标类型、算法、模式类型和最低置信度过滤，响应不包含恢复明文、Hash 或文件内容；
- 自适应预算与停止：Hashcat 每批返回的测试数、恢复数和耗时会更新调度评分；任务总预算、策略预算、时间预算或候选耗尽后停止继续分配；
- 调度策略与实验（B 侧）：`fixed` / `round_robin` / `heuristic_bandit` / `ucb` / `cost_aware_ucb` 可选，提供成本标定、离线回放、研究日志与研究 API（`/api/runs/{run_id}/research*`）及前端研究面板；
- LLM Planner：只向模型发送结构化 PRIR，使用严格 JSON Schema 输出，支持温度、超时、最大输出 token、进程内 TTL/LRU 缓存及异常降级；
- Policy Validator：在计划进入执行链路前校验策略白名单、目标适用性、双预算、优先级和参数范围；
- Rule Planner：无上下文按 S1→S2→S3，有上下文追加 S4；慢 Hash 将上下文高概率策略提前并限制候选池规模；
- pytest 覆盖任务、文件、状态机、候选生成、适配器、Mock/Real 执行、Bandit 评分、预算停止和策略统计链路。

## B 决策模型与离线回放

第一至第三步交付说明见 [B 阶段交接](docs/handoff/b-milestone1-3.md)，包含
[接口对齐方案](docs/decision/interface-alignment.md)和
[数学模型](docs/decision/mathematical-model.md)。离线回放复用 fixed、round_robin、
heuristic_bandit，支持候选去重、双预算、目标级收益、完整轨迹和批次边界恢复。
第四步已接入真实执行与回放的统一奖励、SQLite 完整日志及 JSONL 导出，
详见 [第四步交接](docs/handoff/b-milestone4.md)和
[奖励与日志说明](docs/decision/reward-and-logging.md)。本次改动待使用方运行测试。

第五步新增 UCB、在线成本拟合与 Cost-aware UCB，已接入真实执行与回放；
CLI 的 `--policy all` 现在比较五种算法。公式与借鉴来源见
[UCB 与成本模型](docs/decision/ucb-and-cost.md)，验收命令见
[第五步交接](docs/handoff/b-milestone5.md)。第四步用户报告 79 项通过，第五步用户已确认测试和示例运行通过。

成本标定器与四种盐条件模型现已补齐，提供真实测量、标定拟合、回放导入及近期吞吐。
使用方式见 [成本与盐条件说明](docs/decision/calibration-and-salts.md)，
交接与验收状态见 [补齐项交接](docs/handoff/b-calibration-salts.md)。本次新增内容待用户测试。

B 研究数据现已接入执行工作台与历史运行详情，提供只读摘要、分页事件和完整脱敏日志下载。
范围、字段约定、刷新／重启读取方式与验收命令见 [B 部分前端添加文档](docs/handoff/b-frontend-additions.md)。本次接入尚未运行测试或前端构建。

在已安装项目的环境中比较三个基线：

```powershell
python -m sage_pass.experiments --scenario examples/replay/baseline-small.json --output data/replay/baseline-comparison.json
```

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

真实执行使用按需候选流：启动 run 时只建立 Generator 游标，调度器选中 Arm 后
才生成候选。默认每个 Hashcat 会话最多拉取 100000 条，可通过
`SAGE_HASHCAT_STREAM_BATCH_SIZE` 调整。会话使用稳定的 `--session`、
`--restore-file-path` 和定期 restore 检查点；服务意外退出后可从 Hashcat
检查点和 Generator 游标继续。正常结束后会删除该 run 的临时候选与会话文件。
S2 可通过策略参数接入 Hashcat 原生攻击：`hashcat_rule_files` /
`hashcat_inline_rules` 会以 `-r` 规则引擎运行词表种子；`hashcat_masks`
会以 `-a 3` 掩码攻击运行；`hashcat_hybrid_mask` 配合
`hashcat_hybrid_position` 会以 `-a 6/7` 混合攻击运行，掩码作为 Hashcat
参数传递而不是在 Python 侧展开。

S3 默认继续使用 `pcfg_lite`。要启用完整 PCFG，将 `SAGE_PCFG_VARIANT` 设为
`pcfg_full`，并把 `SAGE_PCFG_RULESET_PATH` 指向 `pcfg_cracker` 训练器生成的
ruleset 目录（目录内应含 `config.ini`、`Grammar/`、`Alpha/` 等）。SAGE 不附带
RockYou 或其派生默认模型；ruleset 应由授权数据训练或由使用者合法提供。
`pcfg_full` 仅运行 PCFG 部分；OMEN/Markov 通过独立 `markov` 生成器启用。

使用三阶 Markov/OMEN：

```text
SAGE_S3_GENERATOR=markov
SAGE_MARKOV_RULESET_PATH=D:\path\to\pcfg_cracker\Rules\MyRuleset\Omen
SAGE_MARKOV_ORDER=3
```

这里的三阶表示“根据前 3 个字符预测下一字符”，对应 OMEN 模型配置
`ngram = 4`。阶数由训练决定，运行时配置必须与模型一致。候选按 OMEN
level 从低到高输出，`CandidateSource.score` 保存该 level；level 越低表示
模型认为候选越可能。模型内容不写入运行快照。

S4 默认使用 `auto` 路由：只有个人信息时使用 `context`，只有历史旧口令时
使用 `history`，两者同时存在时使用 `hybrid`。调试或消融时可通过
`SAGE_S4_GENERATOR=context|history|hybrid` 固定具体生成器。

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
  generators/pcfg_full.py  开源 PCFG ruleset 的流式、可恢复适配器
  generators/markov.py  三阶 OMEN Markov 模型加载、排序与流式适配器
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
