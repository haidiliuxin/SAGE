# A 两周冲刺 · 第 2 周交接（目标适配与集中联调）

交接日期：2026-09-17
交付版本：0.5.0
范围：PDF/Office MVP、数据库迁移框架、E2E 验证报告（B/C 模块接入见下节）

## 交付结论

A 第 8～14 天的自有任务完成并通过全量回归：

- **PDF/Office MVP**：统一 `TargetExtractor` 链新增 `pdf2john` / `office2john` 适配，
  覆盖主流加密 PDF 与 2007/2010/2013+ Office（含传统 `$oldoffice$0/1/3/4`）；
  未加密、旧格式、工具缺失、损坏文件都返回明确错误，不做静默降级；
- **数据库迁移框架**：新增 `migrations.py`（`schema_migrations` 版本表 + 幂等迁移），
  既有 `strategy_runs.run_id` 修复并入版本 `0001`；`Database.create_all()` 现在
  自动应用增量迁移，新增列/索引类变更可安全追加；
- **Analyzer 同步升级**：ZIP/PDF/Office 走同一条“加密文件解析”路径，成功时写入
  真实算法（`zip-aes`/`pdf`/`office`）与 medium 验证成本，失败时给出可交接的降级原因；
- **版本与契约**：`0.5.0`，OpenAPI 与前端类型同步，环境变量新增
  `SAGE_PDF2JOHN_PATH` / `SAGE_OFFICE2JOHN_PATH` / `SAGE_EXTRACTION_TIMEOUT_SECONDS`。

## PDF / Office 模式映射（以本机 `hashcat -hh` 为准）

| 目标 | 特征 | Hashcat 模式 |
|---|---|---|
| PDF | `$pdf$1` | 10400 |
| PDF | `$pdf$2` | 10500 |
| PDF | `$pdf$3` | 10600 |
| PDF | `$pdf$4` | 10700 |
| PDF | `$pdf$5`（RC4-40） | 10510 |
| Office | `$oldoffice$0/$1` | 9700 |
| Office | `$oldoffice$3/$4` | 9800 |
| Office | `$offic$*2007*` | 9400 |
| Office | `$office$*2010*` | 9500 |
| Office | `$office$*2013*`（含 2016+） | 9600 |

提取器只解析 `*2john` 输出中的 Hash 行，不自行解析加密格式；命令可配置，便于在
无 John 的机器上用仿真程序做自动化测试。

## 验证结果（E2E）

- 后端：**450 项测试通过**（第 1 周 445 + 迁移框架 3 项 + PDF/Office 与 E2E 用例）；
- 覆盖点：
  - 提取器矩阵：Hash / ZIP(13600) / PDF(`$pdf$2`→10500) / Office(`$office$*2013*`→9600)；
  - 错误路径：`pdf2john` 缺失 → `503` 明确提示；无加密目标 → `422`；不支持变体 → `422`；
  - **端到端**：上传 PDF → `analyze` 识别 `pdf`（medium）→ `plan` → `execute(mode=real)`
    在**未传 `hashcat_mode`** 的情况下使用提取器给出的 **10500**，且 `$pdf$2...` 原样传给 hashcat；
  - 迁移框架：空库自动应用、重复运行幂等、旧库（UNIQUE run_id + 历史数据）在线升级后
    数据保留且同一 run 可写入多条策略行；
- 前端：12 项测试 + `npm run build` 通过；OpenAPI 与运行时一致；`git diff --check` 干净；
- 真实工具冒烟：本机 hashcat 7.1.2 真实执行 MD5 恢复成功（见 `docs/API.md` 验收口径）。

## B / C 模块接入状态

- **C（信息场景与生成器）**：`week3C` 分支内容（Feedback Engine v2 + S5）已完整包含在
  `main` 中（此前经 `feedback-engine-s5` 合并）；两周冲刺新增的 I0～I3、Generator Registry、
  PCFG/Markov/PassLLM 适配仍待 C 交付，A 侧接线点已就绪（`CandidateGenerator` 批次接口、
  `InformationProfile` 结构已在 `interfaces.py` 冻结）；
- **B（调度与实验）**：`part-B` 分支引入 `decision/`（UCB、cost-aware UCB、奖励、成本标定、
  盐条件、回放、研究日志）、`experiments/` CLI、`research_api.py` 与前端 Research 面板。
  A 已完成接入评审：合并时清理了误入库的 `.sqlite3` 数据文件，并把 B 的策略接入
  `build_scheduler`（`ucb` / `cost_aware_ucb` / `thompson` 不再返回“未交付”）。

## 下一阶段建议

1. 以真实加密 PDF / DOCX / XLSX / PPTX 各跑一次真实冒烟（需安装 John the Ripper jumbo）；
2. 接入 C 的 Generator Registry 与 I0～I3 信息画像；
3. 用 B 的 `ReplayEnvironment` 产出第 4 周实验数据并接入导出接口；
4. 生产化：SQLite WAL + busy_timeout、续跑原子认领、鉴权与限流（见
   `docs/backend-optimization-plan.md`）。
