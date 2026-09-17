# Week 3 C 集成交接（A 侧评审与合并记录）

本次记录 `week3C` 分支（`04ffdd3`，作者 dailucy0824）并入 `main` 前的评审、修复与验证结果。
分支本身由 C 交付并通过自测；A 侧只做集成检查、契约同步与回归验证，不改动 C 的算法实现。

## 评审结论

- 合并基线：`week3C` 已包含 `main`（`git merge-base --is-ancestor origin/main origin/week3C` 成立），
  合并到集成分支时零冲突；
- 规模：相对 `main` 变更 58 个文件、+6686 / −202，含 `src/sage_pass/generators/`（12 个模块）、
  `information.py`、vendored `_vendor/pcfg_cracker`（含 `LICENSE` 与 `NOTICE.md`）、8 个新测试文件；
- 依赖：vendored 代码未引入 `numpy` / `scipy` 等第三方运行时依赖，`pyproject.toml` 仅新增
  `[tool.setuptools.package-data]` 声明以打包许可与 NOTICE 文件；
- 数据产物：分支内无 `.db` / `.sqlite3` / `.jsonl` 等二进制或数据文件被提交。

## 本次修复

| 问题 | 处理 |
| --- | --- |
| `docs/openapi.json` 未随 C 的 schema 变更同步（缺少 `InformationProfile`、`InformationScenario`、`InformationType`、`TaskContext` 个人信息字段、`TaskCreate.historical_passwords`） | 重新生成，新增 224 行；同步把应用版本提升到 `0.6.0`（`pyproject.toml`、`sage_pass.__version__`、`main.py`、`docs/API.md`） |
| `docs/API.md` 第 61 行仍写 “PDF/Office 提取器本阶段返回 422”，与已交付实现（`PdfTargetExtractor`、`OfficeTargetExtractor` 已在 `main.py` 装配）矛盾 | 更正为目标提取器实际行为（模式映射 10400/10500/10600/10700/10510 与 9700/9400/9500/9600、共用提取超时、工具缺失返回 503） |
| `docs/API.md` 缺少 C 侧字段与新增环境变量说明 | 新增 “C 侧：信息场景与生成器注册表” 小节（`InformationProfile` 契约、脱敏边界、策略→生成器映射、快照 schema v2）并补全环境变量表（PDF/Office 提取器、提取超时、调度/规划模式、PCFG/Markov 与 S3/S4 覆盖） |
| `.env.example` 调度模式注释仍写 “ucb 等由 B 侧交付” | 改为实际可用集合 `fixed / round_robin / heuristic_bandit / ucb / cost_aware_ucb`，并标注 `thompson` 为预留值 |
| 契约漂移缺少自动发现手段 | 新增 `scripts/regen_openapi.py`（校验/重写 `docs/openapi.json`）与 `tests/test_openapi_contract.py`（契约一致、`InformationProfile` 存在、包版本与 pyproject/OpenAPI 一致） |

## 验证证据

- 后端：集成分支完整 `pytest` 全绿（`609 passed`）；
- 前端：`npm test` 18 项通过，`npm run build` 通过；
- 契约：`python scripts/regen_openapi.py` 输出 `openapi sync: True`；
- 版本一致性：`pyproject.toml`、`sage_pass.__version__`、`docs/openapi.json` 与 `docs/API.md` 均为 `0.6.0`。

## 遗留（不阻塞本次合并）

- `SchedulerType.THOMPSON` 仍为预留值，配置后启动真实执行会明确报错（B 侧可选项）；
- `passllm` 生成器仅有 ID 文档，未注册实现；
- PCFG full / Markov 使用真实 ruleset 的端到端性能验证仍待具备样本数据的机器上复测。
