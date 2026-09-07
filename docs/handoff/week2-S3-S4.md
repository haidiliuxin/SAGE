# 第二周 S3 S4 候选生成交接

## 范围

本次在既有 S1 Baseline、S2 Rule 管线中加入 S3 PCFG-lite 和 S4 Context，未改变统一任务、规划、执行和结果接口。真实执行仍只向 Hashcat 传递单行字符串；候选来源作为内部不可变记录保留在生成批次和运行态中。

## S3 PCFG-lite

S3 使用 11 个固定模板：`W`、`WY`、`WD`、`C`、`CY`、`CD`、`WS`、`CS`、`WYS`、`WDS`、`DW`。`W/C/Y/D/S` 分别表示原词、首字母大写词、年份、常用数字和符号。模板按概率降序稳定展开，参数含义如下：

- `max_templates`：最多使用的模板数；
- `min_probability`：模板概率下限；
- `max_structure_length`：最终候选字符串的最大字符数。

生成器使用惰性笛卡尔积，不预先构造完整候选空间。Planner 不提供参数时使用有限模板默认值。

## S4 Context

S4 使用现有 `TaskContext` 中的 `keywords`、`years`、`region` 和 `organization`。词项先执行 Unicode NFKC、首尾清理、空白合并和 ASCII 大小写规范化，再通过 `pypinyin` 生成无声调全拼和首字母缩写。组织名额外派生移除“大学、学院、集团、公司”等常见后缀的形式。原词、拼音、缩写、地区、组织和年份按稳定顺序输出，并生成词项加年份的有限组合。

`description` 不直接进入候选，避免将不受限的长文本作为口令。`use_keywords`、`use_pinyin`、`use_abbreviations`、`use_years`、`use_region`、`use_organization` 控制来源，`max_combinations` 限制 S4 输出。

## 来源与去重

`CandidateRecord` 保存候选字符串、策略编号和一个或多个 `CandidateSource`。来源记录可包含原值、规范化值、PCFG 模板、概率与组合成分。`CandidateBatch.records` 与 `CandidateBatch.candidates` 数量、值和顺序一致。

策略按 `priority` 执行，全部策略共享最终字符串 `seen` 集合；高优先级策略已接纳的字符串不会由低优先级策略再次提交。每个策略受自身 `candidate_budget` 限制，全次执行受 100000 条硬上限限制，默认批次大小为 1000。

## 执行与前端

`RealExecutor` 将 `task.context` 传给候选生成器，并按实际去重后的批次数量计算预期进度和策略统计。前端执行请求支持 `mock` 和 `real`，默认仍为 `mock`；选择 `real` 时由后端检查本机 Hashcat 和目标算法支持情况。

## 验证

后端测试覆盖模板概率与过滤、候选长度、中文拼音与缩写、真实上下文来源、S4 组合上限、跨策略去重、来源对齐和仿真 Hashcat 的 S3 实际统计。前端回归测试覆盖上下文提交与完整工作台渲染，并通过 TypeScript 与 Vite 构建。
