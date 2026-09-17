# SAGE-Pass 统一接口（A / B / C 两周冲刺合并契约）

版本：`0.6.0`
基础地址：`http://127.0.0.1:8000`

本文把团队提供的《统一接口.docx》落实为当前后端契约，并记录 A 冲刺的算法识别链、Argon2、PDF/Office 目标提取、调度模式与跨重启结果查询，B 侧的决策模型与离线回放，以及 C 侧的信息场景（`I0`～`I3`）、`InformationProfile` 与生成器注册表。字段定义的机器可读版本见同目录 `openapi.json`；运行服务后也可在 `/docs` 联调。

## 统一约定

- 时间预算单位为秒，候选预算为整数。
- 任务使用 `task_id`，上传文件使用 `file_id`，执行使用 `run_id`。
- 任务状态：`created`、`analyzed`、`planned`、`running`、`paused`、`completed`、`failed`、`cancelled`。
- 目标类型：`hash`、`zip`、`pdf`、`office`、`unknown`（ZIP/PDF/Office 均已接入真实提取）。
- 验证成本：`low`、`medium`、`high`、`unknown`。
- 策略编号：`S1`、`S2`、`S3`、`S4`、`S5`。
- 执行模式：`mock`（第一周链路，保留）与 `real`（真实执行）。
- 规划模式：`mock`、`rule`、`llm`（`adaptive` 已弃用，见下）。
- 调度模式：`fixed`、`round_robin`、`heuristic_bandit`、`ucb`、`cost_aware_ucb` 已可用（`thompson` 为预留值，配置后明确报错，不静默回退）。
- 信息场景：`I0`（无辅助信息）、`I1`（仅个人信息）、`I2`（仅历史旧口令）、`I3`（个人信息 + 历史旧口令），由后端统一推导。
- 无法确定的字段使用 `null` 或 `unknown`，不得编造。

## A 冲刺第 1 周要点

### 算法识别链（真实执行模式判定顺序）

```text
ExecutionRequest.hashcat_mode
  → Task.known_algorithm
  → PRIR.algorithm（Analyzer 识别结果）
  → 422 报错（提示可显式提供 hashcat_mode）
```

- 算法名称统一归一：`SHA-1`/`sha_1` → `sha1`，`sha-256` → `sha256`，`WinZip-AES` → `zip-aes`，`Argon2id` → `argon2id`；
- 未填写 `known_algorithm` 时，Analyzer 识别出的 MD5/SHA/bcrypt/Argon2 可直接进入真实执行；
- 未知算法仍可用显式 `hashcat_mode` 覆盖。

### Argon2

- 变体识别：`$argon2id$` → `argon2id`、`$argon2i$` → `argon2i`、`$argon2d$` → `argon2d`，其余 `$argon2` → `argon2`；
- Hashcat 模式：`argon2` / `argon2i` / `argon2d` = `34000`，`argon2id` = `70000`（依据本机 `hashcat -hh`）；
- 参数化 Hash 行（含 `v=19$m=...,t=...,p=...`）完整透传，不做切分或截断；
- 验收：前端不填写 Hashcat 模式，仅提交 Argon2 Hash，即可完成 Analyzer → Planner → RealExecutor 全链路。

### 调度模式（修正 adaptive 语义）

- `POST /execute` 的真实执行仍读取规划结果，但“自适应”属于调度层：
  `SAGE_SCHEDULER_TYPE` ∈ `fixed` / `round_robin` / `heuristic_bandit` / `ucb` / `cost_aware_ucb` / `thompson`；
- 当前可用：`fixed`（按优先级跑完一个 Arm 再下一个）、`round_robin`（轮流取批）、`heuristic_bandit`（默认，评分选择）、`ucb` 与 `cost_aware_ucb`（B 侧成本感知 UCB）；
- `thompson` 尚未交付，配置后启动真实执行会返回明确 `422`，不会静默回退；
- `SAGE_PLANNER_TYPE=adaptive` 已弃用：会在配置解析阶段抛出明确迁移错误（提示改用 `SAGE_SCHEDULER_TYPE`），不再静默回退 Mock；
- `GET /api/system/config` 返回 `planner_type` 与 `scheduler_type`，前端据此把“自适应”显示为**调度模式**。

### 公共接口（冻结）

`src/sage_pass/interfaces.py` 集中导出：`ArmSpec`（调度臂）、`CandidateBatch`（候选批次）、`BatchOutcome`（执行反馈）、`InformationProfile`（信息条件）、`DecisionEvent`（调度决策日志）与 `DecisionPolicy` 协议。
约定：`arm_id` 与 `strategy_id` 分离；Scheduler 只面向 Arm；Executor 只接收候选并返回 `BatchOutcome`；研究日志不记录恢复明文。

### 目标提取器（TargetExtractor）

- 统一接口：`supports(target_type)` + `extract(target_type, content, file_path) -> ExtractedTarget`；
- 已实现：`HashTargetExtractor`（行内 Hash）、`ZipTargetExtractor`（zip2john → `$zip2$` 走模式 13600；`$pkzip2$` 即传统 PKZIP/ZipCrypto，按结构选 17200 单文件压缩 / 17210 单文件未压缩 / 17225 多文件与混合 / 17230 仅校验和）；
- `PdfTargetExtractor`：pdf2john 提取，按 `$pdf$` 版本映射 10400 / 10500 / 10600 / 10700 / 10510，未知版本或非加密 PDF 返回明确 `422`；
- `OfficeTargetExtractor`：office2john 提取，按哈希签名映射 `$oldoffice$0/1` → 9700、`$oldoffice$3/4` → 9800、`$office$*2007*` → 9400、`*2010*` → 9500、`*2013*` → 9600，未识别签名返回明确 `422`；
- 三者共用 `SAGE_EXTRACTION_TIMEOUT_SECONDS` 超时；工具缺失时返回提示信息而非静默降级。

### 已完成真实运行的跨重启查询

`/runs/{run_id}/status` 与 `/runs/{run_id}/result` 的解析顺序：内存 registry → `RunRecordModel` 持久化记录（重建真实 tested/recovered/time 与 recovered_items）→ Mock StrategyRun。
因此服务重启后仍能查询已完成真实运行的原始结果，且不会被 MockExecutor 覆盖；损坏/不兼容记录返回明确错误。

## 研究与实验 API（B 侧）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/tasks/{task_id}/runs` | 列出该任务的历史运行（含研究日志可用性） |
| GET | `/api/runs/{run_id}/research` | 某次运行的研究摘要（步骤、策略、奖励、成本） |
| GET | `/api/runs/{run_id}/research/events` | 逐条决策事件（DecisionEvent 风格，无恢复明文） |
| GET | `/api/runs/{run_id}/research/download` | 下载研究日志（JSONL/SQLite 导出） |

实验与回放 CLI 见 `docs/decision/`，示例场景在 `examples/replay/`。

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
    "name": "张三",
    "nickname": "小明",
    "username": "zhangsan",
    "email_local_part": "zhangsan.work",
    "phone_suffix": "7788",
    "birthday": "01-23",
    "birth_year": 2001,
    "years": [2024, 2025],
    "region": "北京",
    "organization": "示例大学",
    "interest_words": ["摄影", "篮球"],
    "authorized_keywords": ["项目名", "宠物名"],
    "description": "其他补充信息"
  },
  "historical_passwords": ["仅在当前任务内部使用的旧口令"]
}
```

信息场景由后端统一推导：`I0` 无辅助信息、`I1` 仅个人信息、`I2` 仅历史旧口令、`I3` 同时包含个人信息和历史旧口令。旧版 `keywords`、`years` 字段继续兼容，分别按“其他授权关键词”和“生日或年份”处理。`historical_passwords` 是独立顶层字段，不得把同一明文混入 `keywords`、`interest_words` 或 `authorized_keywords`。

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
- 历史旧口令必须是单行文本，每项 1～1024 字符，最多 1000 项；
- 个人信息单值不超过 256 字符，`birth_year` 必须是四位年份。

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

返回当前任务内容、当前状态、`created_at`、`updated_at` 和脱敏 `information_profile`。响应不返回 `historical_passwords` 明文，只返回场景、是否存在个人信息、信息类型、是否存在历史口令、历史口令数量、是否有 Pattern Knowledge 以及长度区间/字符类别等脱敏结构摘要。

隐私边界：个人信息与历史旧口令明文不会发送给 LLM，不写入 Planner 决策日志，也不会作为跨任务种子传播；跨任务 Feedback 只保存抽象 Pattern Knowledge。任何候选来源展示都必须通过脱敏来源序列化，历史旧口令来源不包含 `original` 或 `normalized` 明文。

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
running  -> paused | completed | failed | cancelled
paused   -> running | completed | failed | cancelled
```

重复写入当前状态按幂等成功处理；终态不允许回退。

**暂停/继续（第 3 周）**：`running` 任务可 `PATCH {"status":"paused"}` 暂停，`paused` 任务可 `PATCH {"status":"running"}` 继续，或 `PATCH {"status":"cancelled"}` 取消。Mock 执行在暂停期间冻结进度；真实执行在**候选批次边界**暂停（当前批次结束后进入 `paused`，运行状态消息会先提示“等待当前批次结束”），继续后从下一批候选恢复。取消会同步停止底层 Hashcat 进程（含暂停中取消）。

**异常恢复（第 3 周）**：真实执行每次启动都会写入 `RunRecordModel`（目标、计划、候选流游标、去重 digest 索引、逐批进度与调度统计检查点）。服务重启后，处于 `running/paused` 的真实运行会自动从断点续跑（跳过已消费批次，至多整批重跑一次正在执行的那一批）；其余无运行记录的残留任务仍由启动收尾（全部策略行已完成则任务置为 `completed`，否则置为 `failed`），避免状态悬挂。暂停状态在重启后被自动继续。

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
  "warnings": [],
  "information_profile": {
    "scenario": "I3",
    "has_personal_information": true,
    "information_types": ["name", "username", "region"],
    "has_historical_passwords": true,
    "historical_password_count": 1,
    "has_pattern_knowledge": false,
    "structure_summary": {
      "personal_field_count": 3,
      "personal_value_count": 3,
      "historical_length_buckets": {"8-11": 1},
      "historical_character_classes": {"lower+upper+digit": 1}
    }
  }
}
```

分析规则（第 2 周）：

- Hash 文本任务优先使用 `known_algorithm`；未提供时按常见 Hash 形态做规则识别；
- **ZIP 文件任务**（第 2 周接入，第 3 周扩展传统 PKZIP）：调用 zip2john 提取加密目标，WinZip AES 返回 `algorithm: "zip-aes"`，传统 PKZIP（ZipCrypto）返回 `algorithm: "zip-legacy"`，两者都是 `salt: true`、`verification_cost: "medium"`；
- zip2john 未安装/超时/未加密时**不失败**，返回 `unknown` 算法并在 `warnings` 中说明降级原因，mock 链路仍可继续；同一压缩包内若混有多种传统 PKZIP 模式，按多数模式执行并在 `warnings` 中说明未纳入的目标数；
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

设置 `SAGE_PLANNER_TYPE=llm` 并提供 `OPENAI_API_KEY` 后，接口改用 LLM Planner，返回的 `planner_type` 为 `llm`。模型只接收上述结构化 PRIR、统一 `InformationProfile` 和脱敏反馈摘要（不含目标 Hash、文件内容、个人信息原文、历史旧口令或恢复明文），并通过严格 JSON Schema在 `S1`～`S5` 中选择策略和分配预算。反馈摘要只含 `available`、模式数量、主要模式类型、最高置信度和建议 S5 最大预算。`SAGE_LLM_API_STYLE=responses` 使用 OpenAI Responses API；`chat_completions` 使用 OpenAI 兼容的 Chat Completions API。服务端会再次检查策略唯一性、S4 上下文条件、S5 知识可用性、优先级及时间/候选总预算；API、网络或输出异常时自动返回 Rule 计划，并把降级原因写入 `warnings`。

设置 `SAGE_PLANNER_TYPE=rule` 可完全跳过 LLM。Rule Planner 在无上下文时按 S1→S2→S3 规划，有上下文时增加 S4；有可用迁移知识时在 S1 后加入 S5。慢 Hash 的候选池限制为任务上限的 25%，S5 候选预算还受知识摘要建议上限约束。极小预算按优先级保留前几个策略并返回 warning。

Policy Validator 当前规则：白名单为 `S1`～`S5`；目标必须是 `hash`、`zip`、`pdf` 或 `office`，S4 要求 `context_available=true`，S5 要求当前 `target_type + algorithm` 作用域存在满足支持度的 Pattern Knowledge。每个策略的时间和候选预算必须大于零，两类预算总和均不得超过 PRIR；未知参数、错误参数类型或越界值均拒绝。

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
  "hashcat_mode": 0,
  "timeout": 60
}
```

字段说明：

- `candidates`：可选的优先补充候选，最多 100000 条，每条为 1～1024 字符的单行文本；不提供时，后端根据策略计划自动生成 S1～S5 候选；
- `hashcat_mode`：可选。缺省时 Hash 任务由 `known_algorithm` 自动映射（bcrypt=3200、sha256=1400、zip-aes=13600、zip-legacy=17225 等），ZIP 任务按 zip2john 的 `$zip2$` / `$pkzip2$` 结构自动判定（13600 / 17200 / 17210 / 17225 / 17230）；无法确定时返回 `422`；
- `timeout`：可选，覆盖策略时间预算的每策略秒数上限。
- `stop_on_hit`：可选（默认取 `SAGE_STOP_ON_HIT`）。为 `true` 时采用真实破解语义：任一策略恢复出目标后立即结束本次运行，研究停止原因为 `all_targets_recovered`，不再消耗剩余候选。
- `candidates`：可选，最多 10 万条单行文本。作为高优先级候选进入 S1（前端支持粘贴或导入 `.txt` 词表）。更大的字典请放到服务器上并配置 `SAGE_WORDLIST_PATH`：此时 S1 改为 **hashcat 原生词表攻击**（`--attack-mode 0 <词表>`，单进程读完整本字典，不经过 Python 候选列表，也不写入临时候选文件）。

策略参数可启用 Hashcat 原生规则/掩码/混合攻击而不在后端展开全部候选：
`hashcat_rule_files` / `hashcat_inline_rules` 转为 `-r` 规则文件，
`hashcat_masks` 转为 `--attack-mode 3`，`hashcat_hybrid_mask` 加
`hashcat_hybrid_position=left|right` 转为 `--attack-mode 7|6`。

返回：

```json
{
  "task_id": "T39FD9B11EB89",
  "run_id": "R10D44A4973C",
  "status": "running",
  "started_at": "2026-09-01T22:05:00+08:00"
}
```

真实执行先按计划生成各策略候选：S1 生成基础候选，S2 执行规则变换，S3 按有限 PCFG 模板概率展开，S4 使用任务上下文生成候选；请求中的补充候选优先参与生成。候选按生成顺序跨策略稳定去重、按策略候选预算截断并形成批次，再由 Bandit Scheduler 动态选择下一批交给 Hashcat。每个策略的多个批次共用该策略的时间预算，统计结果按策略累计；候选写入临时词表，任务结束后清理。

#### S1～S5 候选生成约定

- S1 使用后端内置的有序 Baseline 候选；请求中可选的 `candidates` 会排在内置候选之前；
- S2 以补充候选和 S1 基线为种子，只执行当前 `StrategyItem.parameters` 中值为 `true` 的规则；
- S2 支持 `capitalize_first`、`all_upper`、`all_lower`、`common_number_suffix`、`year_suffix`、`common_substitution` 和 `symbol_suffix`；
- S2 支持 Hashcat 原生 `-r`、mask（`-a 3`）与 hybrid（`-a 6/7`）参数；mask 单元作为紧凑候选流式调度，不在后端展开成完整明文空间；
- S3 默认使用 `pcfg_lite` 的固定有限模板 `W`、`WY`、`WD`、`C`、`CY`、`CD`、`WS`、`CS`、`WYS`、`WDS` 和 `DW`，按模板概率降序生成；`max_templates`、`min_probability` 和 `max_structure_length` 分别控制模板数、概率阈值和最终候选长度；
- 策略层级为 S1 Baseline、S2 Rule、S3 Statistical Model、S4 Personalized、S5 Transfer。S3 可配置 `pcfg_lite`、`pcfg_full`、`markov`；S4 默认按 I1/I2/I3 自动选择 `context`、`history`、`hybrid`；S5 使用 `pattern_knowledge`；
- 后端设置 `SAGE_PCFG_VARIANT=pcfg_full` 且配置 `SAGE_PCFG_RULESET_PATH` 后，S3 改用 MIT 许可 `pcfg_cracker` 兼容 ruleset；HTTP 请求/响应不变，候选内部来源增加原概率和自然对数 `log_probability`；
- 后端设置 `SAGE_S3_GENERATOR=markov`、`SAGE_MARKOV_RULESET_PATH` 和 `SAGE_MARKOV_ORDER=3` 后，S3 改用 OMEN 三阶 Markov；三阶上下文对应 OMEN `ngram=4`，内部来源的 `score` 为 OMEN level（越低越优先）；
- S4 对关键词、地区和组织词执行 Unicode NFKC 与稳定去重，通过 `pypinyin` 派生无声调全拼和首字母缩写，并与任务提供的年份组合；各 `use_*` 参数控制来源，`max_combinations` 控制输出上限；
- S5 只使用当前任务授权提供的关键词、S1 基线、合法规则种子和历史抽象结构，优先生成 `word + year`、`CapitalizedWord + digits`、`acronym + digits`、`word + digits + symbol` 与受控替换加后缀；
- S5 的 `CandidateSource` 记录抽象 pattern id/signature、pattern confidence 和当前任务种子，不记录历史恢复明文；
- 每条内部候选记录保留来源类型、原值、规范化值、模板概率和组合成分。该元数据不改变 HTTP 请求与 Hashcat 的字符串输入契约；
- 去重保持首次出现顺序，且区分大小写；每条候选必须为 1～1024 字符的非空单行文本；
- 每个策略最多产生其 `candidate_budget` 指定的数量，全次执行最多生成 100000 条；候选不足时以实际生成数量执行；
- 默认每批最多 1000 条，同一策略的全部批次共享该策略的时间预算。

#### Bandit 自适应调度约定

- 同一次真实执行中的“目标组 + 策略”构成一个 Arm；当前一个 run 只包含一个固定目标组，因此运行内使用策略编号标识 Arm；
- 探索阶段按 `StrategyItem.priority` 顺序，为每个仍有候选且预算可用的策略执行一个批次；
- 探索后，每个批次结束都会使用 Hashcat 返回的 `tested`、新增 `recovered` 和实际 `duration` 更新统计，再选择得分最高的策略执行下一批；
- 评分固定为 `Score = 0.45 * P_success + 0.30 * Gain + 0.15 * Transfer - 0.10 * Cost`；
- `P_success` 使用 Jeffreys 先验平滑候选成功率，并投影为下一批至少成功一次的概率；`Gain` 为近期每千候选恢复收益的指数移动平均；S5 的 `Transfer` 根据同作用域模式的置信度、观察频次、任务覆盖和时效性计算，S1～S4 保持 `1 / priority` 兼容先验；`Cost` 为该策略已用执行时间占策略时间预算的比例；
- Planner 给出的每策略 `candidate_budget` 和 `time_budget` 仍是硬上限；请求中的 `timeout` 仍覆盖每策略时间上限，但不会放宽任务的 `total_time_budget`；
- 达到任务候选预算、任务时间预算、任一策略自身预算或候选批次耗尽后，不再为对应范围分配新批次；暂停、继续和取消仍沿用既有批次边界语义；
- 调度仅改变候选批次的执行顺序和实际预算使用，不改变 `POST /execute`、Run Status 或 Run Result 的请求与响应结构。

真实执行的策略统计按实际执行批次累计到既有结果字段：`tested` 为 Hashcat 实际测试数量，`recovered` 为该策略去重后的恢复数量，`time` 为该策略各批次实际执行耗时之和，`success_rate` 为 `recovered / tested`（未测试候选时为 0）。总结果中的 `total_tested` 为各策略测试数量之和，`total_recovered` 按 `target + plaintext` 全局去重。

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

## Feedback Engine v2

真实 run 仅在状态为 `completed` 且终态已经持久化后自动进入反馈处理。相同 `run_id` 由 `feedback_runs` 唯一约束和同事务写入保证只处理一次；反馈失败只写入不含明文的错误类型，不会把成功任务改为 failed。cancelled、failed 和默认 Mock run 不进入知识库；测试环境可用 `SAGE_FEEDBACK_ENABLE_MOCK=true` 显式允许 Mock finalize 进入幂等处理（内置 Mock 不产生恢复明文，因此不会形成模式）。

Pattern Knowledge 的作用域为 `target_type + algorithm`，只保存长度、字符类别、压缩结构签名、数字位置、抽象前后缀、大小写模式、合理年份模式和常见替换等结构。最低观察数、最低任务数、单作用域最大模式数和时效半衰期由 `SAGE_FEEDBACK_*` 配置集中控制。

### 查询抽象模式

`GET /api/feedback/patterns`

可选过滤参数：`target_type`、`algorithm`、`pattern_type`、`minimum_confidence`（0～1）和 `limit`（1～500）。排序依次使用置信度、任务数、观察数、模式类型、模式签名和数据库 id，结果稳定可复现。

```json
[
  {
    "pattern_id": 12,
    "scope": "hash:md5",
    "pattern_type": "structure_signature",
    "pattern_signature": "U1L4D4S1",
    "feature_data": {"signature": "U1L4D4S1", "length": 10},
    "observation_count": 5,
    "task_count": 3,
    "confidence": 0.61,
    "last_seen_at": "2026-09-08T14:00:00+08:00"
  }
]
```

该接口不会返回恢复明文、目标 Hash、文件内容或历史任务上下文。最终运行结果接口仍按既有契约向当前授权客户端返回该 run 的 `recovered_items`；这与跨任务 Pattern Knowledge 的脱敏存储是两个独立边界。

## 环境变量（执行与生成器）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `SAGE_HASHCAT_PATH` | hashcat 可执行文件路径或命令名 | `hashcat` |
| `SAGE_HASHCAT_STREAM_BATCH_SIZE` | 每个长生命周期 Hashcat 会话按需拉取的最大候选数 | `100000` |
| `SAGE_ZIP2JOHN_PATH` | zip2john 可执行文件路径或命令名 | `zip2john` |
| `SAGE_PDF2JOHN_PATH` | pdf2john 可执行文件路径或命令名 | `pdf2john` |
| `SAGE_OFFICE2JOHN_PATH` | office2john 可执行文件路径或命令名 | `office2john` |
| `SAGE_EXTRACTION_TIMEOUT_SECONDS` | 目标提取（`*2john`）超时秒数 | `30` |
| `SAGE_SCHEDULER_TYPE` | 调度模式：`fixed` / `round_robin` / `heuristic_bandit` / `ucb` / `cost_aware_ucb` | `heuristic_bandit` |
| `SAGE_PLANNER_TYPE` | 规划模式：`mock` / `rule` / `llm` | `mock` |
| `SAGE_PCFG_VARIANT` | S3 默认生成器：`pcfg_lite` / `pcfg_full` | `pcfg_lite` |
| `SAGE_PCFG_RULESET_PATH` | `pcfg_full` 使用的 pcfg_cracker ruleset 目录 | 未设置 |
| `SAGE_MARKOV_ORDER` | Markov（OMEN 风格）模型阶数，取值 2～5 | `3` |
| `SAGE_MARKOV_RULESET_PATH` | Markov 独立模型/ruleset 路径 | 未设置 |
| `SAGE_S3_GENERATOR` | 覆盖 S3 生成器：`pcfg_lite` / `pcfg_full` / `markov` | 跟随 `SAGE_PCFG_VARIANT` |
| `SAGE_S4_GENERATOR` | 覆盖 S4 生成器：`auto` / `context` / `history` / `hybrid` | `auto` |
| `SAGE_FEEDBACK_MINIMUM_OBSERVATIONS` | S5 可用模式的最低观察数 | `2` |
| `SAGE_FEEDBACK_MINIMUM_TASKS` | S5 可用模式的最低独立任务数 | `2` |
| `SAGE_FEEDBACK_MAXIMUM_PATTERNS_PER_SCOPE` | 每个作用域最多保存的模式数 | `500` |
| `SAGE_FEEDBACK_RECENCY_HALF_LIFE_DAYS` | transfer score 时效半衰期（天） | `90` |
| `SAGE_FEEDBACK_ENABLE_MOCK` | 仅供测试显式允许 Mock finalize 进入反馈处理 | `false` |

未安装 hashcat 时，`mode: "real"` 的 Hash 任务在启动时返回 `503 EXECUTION_FAILED`（错误信息提示检查 `SAGE_HASHCAT_PATH`）；未安装 zip2john 时，ZIP 分析会降级并给出提示，ZIP 真实执行同样返回 `503`。PDF/Office 真实执行依赖各自的 `*2john` 工具，缺失时返回 `503` 并在 `details` 中指明变量名。

## C 侧：信息场景与生成器注册表

### InformationProfile 契约

任务分析后，`TaskDetail.information_profile` 与 `PRIR.information_profile` 返回统一画像，字段包括：场景（`I0`～`I3`）、是否存在个人信息、信息类型集合、是否存在历史旧口令、历史旧口令数量、是否存在 Pattern Knowledge，以及不含原文的结构摘要（个人字段/值数量、历史口令长度区间分布、字符类别组合分布）。

- 画像只含计数与分布，不含个人信息原文，也不含历史旧口令原文、规范化形式或哈希；
- 任务详情不序列化 `historical_passwords`，仅返回数量与抽象结构；
- LLM Planner 只接收该脱敏画像，不接收个人信息或历史旧口令原文。

### 生成器注册表

`src/sage_pass/generators/` 提供统一 `GeneratorProtocol`（`prepare` / `next_batch` / `exhausted` / `snapshot` / `restore`），`CandidateGenerator` 只负责策略映射、Registry 调用、全局去重、合法性与预算过滤、分批：

| 策略 | 默认生成器 | 可覆盖 |
| --- | --- | --- |
| `S1` | `baseline` | — |
| `S2` | `rule` | — |
| `S3` | `pcfg_lite` | `SAGE_S3_GENERATOR` / `SAGE_PCFG_VARIANT` |
| `S4` | 按场景 `auto`：`I1` → `context`、`I2` → `history`、`I3` → `hybrid` | `SAGE_S4_GENERATOR` |
| `S5` | `pattern_knowledge`（旧名 `transfer` 保留兼容） | — |

- `CandidateBatch` 位于 `candidate_types.py`，带 `generator_id`、`exhausted` 与状态快照；旧的 `candidate_generator.CandidateBatch` 导入路径继续可用；
- RunRecord 快照为 schema v2，记录每个策略的 `generator_id`；无版本或 v1 快照按旧批次数组与 `consumed` 游标恢复，未知版本明确失败；
- 历史旧口令候选来源使用 `kind="historical_password"`，对外展示一律经 `CandidateSource.public_dict()` 无条件清除 `original` 与 `normalized`；
- `passllm` 仅保留 ID 文档，未注册空实现。

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
