# Week 3 C：信息场景与输入模型

## 已完成

- 新建开发分支 `week3C`。
- 定义 `InformationScenario`：`I0` 无辅助信息、`I1` 个人信息、`I2` 历史旧口令、`I3` 个人信息 + 历史旧口令。
- 扩展 `TaskContext`，支持姓名、昵称、用户名、邮箱局部、电话后缀、生日/年份、地区、组织、兴趣词和其他授权关键词；旧 `keywords/years` 保持兼容。
- `historical_passwords` 是 `TaskCreate` 顶层独立字段，放进 `context` 会被拒绝，与普通关键词重复也会被拒绝。
- Analyzer 生成统一 `InformationProfile`；规划阶段结合当前作用域的 Pattern Knowledge 更新 `has_pattern_knowledge`。
- LLM Planner 只接收 `InformationProfile` 脱敏画像，不接收个人信息或历史旧口令原文。
- 任务详情不序列化 `historical_passwords`，只返回数量、类型和抽象结构分布。
- 前端已增加所有个人信息与历史旧口令输入，并在任务详情中仅展示脱敏摘要。

## `InformationProfile` 契约

字段包括：场景、是否有个人信息、信息类型、是否有历史口令、历史口令数量、是否有 Pattern Knowledge，以及不含原文的结构摘要。结构摘要当前包含个人字段/值数量、历史口令长度区间分布和字符类别组合分布。

## 后续猜测器接入约束

- 猜测器应从内部 `TaskDetail.historical_passwords` 单独取旧口令，不能把它们合并进 `TaskContext.keywords`。
- 旧口令候选来源使用 `kind="historical_password"`，对外展示必须调用 `CandidateSource.public_dict()`；该方法无条件清除 `original` 与 `normalized`。
- 跨任务迁移继续只使用 `PatternKnowledgeModel` 的抽象结构，不得从任务 JSON、运行快照或历史恢复结果读取明文作为新任务种子。
- 日志只记录 task/run id、数量和错误类型，不记录输入模型 repr、请求体、候选明文或旧口令。

## 验证

- 后端完整 pytest 通过。
- 前端 TypeScript/Vite 构建通过。
- 前端回归测试通过。
- 新增测试覆盖四种场景、类型识别、API/Planner 脱敏、字段边界和候选来源脱敏。

## Generator Registry 重构

- `generators/base.py` 定义无状态 `GeneratorProtocol`、每次运行独立的 `GeneratorState` 及 JSON 兼容快照；恢复时按确定性输入重建惰性迭代器并跳过已确认游标。
- 默认 Registry 注册已实现的 `baseline`、`rule`、`pcfg_lite`、`pcfg_full`、`markov`、`context`、`history`、`hybrid`、`pattern_knowledge`，并保留旧 `transfer` 注册名兼容。`passllm` 仍只保留 ID 文档，不注册空实现。
- `CandidateGenerator` 不再包含 S1～S5 算法分支，只负责 Strategy 映射、Registry 调用、全局去重、合法性过滤、策略/任务预算和分批。
- `CandidateBatch` 移至 `candidate_types.py`，原 `candidate_generator.CandidateBatch` 导入路径继续兼容，并增加 `generator_id`、`exhausted` 和状态快照。
- RunRecord 快照现为 schema v3：不再保存预先物化的全部候选，而是保存 CandidatePlanStream、Generator 游标、脱敏去重摘要和 Scheduler 检查点；旧 v1/v2 批次数组仍可恢复，未知版本明确失败。
- RealExecutor 在调度选中 Arm 时才拉取候选，默认以 100000 条大批次启动 Hashcat；持久化 session 使用 `--restore-file-path` 和定期检查点，崩溃恢复时优先执行 `--restore`。

## Full PCFG 接入

- `pcfg_full` 复用 MIT 许可 `lakiw/pcfg_cracker` 的 ruleset loader、parse-tree 实现和概率优先队列；第三方许可证与来源位于 `_vendor/pcfg_cracker/`。
- 不提交 RockYou 或上游默认 ruleset。通过 `SAGE_PCFG_RULESET_PATH` 加载用户合法提供/训练的兼容模型；模型 UUID 和训练器版本构成快照的 `model_version`。
- S3 默认保持 `pcfg_lite` 以兼容现有行为；设置 `SAGE_PCFG_VARIANT=pcfg_full` 才切换。
- 候选按概率降序惰性读取，`CandidateSource` 同时记录 `probability` 与自然对数 `log_probability`；统一管线继续负责全局去重和总/策略预算。
- 快照仅保存 JSON 游标、参数和模型版本，不保存候选、训练数据或模型明文；恢复时校验模型版本并确定性重放到已确认游标。
- 当前 `pcfg_full` 明确跳过 OMEN/Markov；OMEN 已作为独立 `markov` Generator 接入，PassLLM 尚未实现。

## Markov / OMEN 接入

- `markov` 复用 `pcfg_cracker` 中的 OMEN loader、`MarkovCracker`、`GuessStructure` 和 TMTO optimizer，不使用 `statsprocessor` 的固定按位置一阶链。
- 当前默认阶数为 3，即使用三个前序字符预测下一字符，对应 OMEN `config.txt` 中的 `ngram=4`；运行配置与训练模型不一致时明确失败。
- 模型目录必须包含 `config.txt`、`alphabet.txt`、`IP.level`、`EP.level`、`CP.level`、`LN.level`。六个文件的 SHA-256 构成 `model_version`。
- 候选按 OMEN level 由低到高流式输出，来源 `score` 保存 level；分批、预算、全局去重和最终 Hashcat 合法性过滤仍由统一 Pipeline 负责。
- 快照只保存候选游标、阶数、参数和模型指纹；恢复时校验指纹并确定性重放，不保存训练集、模型表或候选明文。

## Personalized Generator 与策略分类

- 策略分类改为 S1 Baseline、S2 Rule、S3 Statistical Model、S4 Personalized、S5 Transfer。
- S3 当前具体 Generator 为 `pcfg_lite`、`pcfg_full`、`markov`；S4 为 `context`、`history`、`hybrid`；S5 默认使用 `pattern_knowledge`。
- S4 默认自动路由：I1 使用 `context`，I2 使用 `history`，I3 使用 `hybrid`；可用 `SAGE_S4_GENERATOR` 显式覆盖为 `context/history/hybrid`。
- `history` 对当前任务旧口令执行大小写、年份替换、数字前后缀、符号、常见替换、词根提取和结构迁移。来源只带长度/字符类脱敏标签。
- `hybrid` 组合个人信息词根、旧口令结构、当前年份、组织/地区及抽象 Pattern Knowledge；Pattern Knowledge 只作用于当前任务个人信息词根。
- 历史旧口令只从任务本地的独立字段进入 S4，不会成为 S5 跨任务种子；全局去重、预算与最终编码/长度过滤仍由 CandidateGenerator 管线统一处理。
