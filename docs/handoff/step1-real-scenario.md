# 第 1 步：面向真实场景的执行能力（词表 / 大批次 / 命中即停）

目标：把 SAGE-Pass 从"有限候选的评测器"推进为"能拿真实字典跑的破解器"。

## 交付内容

### 1. 原生词表攻击（hashcat 直接读字典）

配置 `SAGE_WORDLIST_PATH` 指向服务器上的字典后，**S1 不再经过 Python 候选列表**，
而是由 hashcat 自己读整本字典：

```
hashcat --hash-type <M> --attack-mode 0 --runtime <T> ... <target.hash> <wordlist>
```

- 单进程跑完整本字典：不再有"每 1000 个候选起一个进程"的 3.5 秒启动开销，
  也不把字典复制成临时候选文件；
- 候选规模不再受 `MAX_EXECUTION_CANDIDATES`（10 万）限制；
- 未配置该变量时行为完全不变（S1 仍用内置基线与调用方传入的候选）。

相关实现：`HashcatJob` 新增 `attack_mode` / `wordlist_path` / `rules_path` / `mask` /
`candidate_estimate`，`HashcatHandle` 按 `is_native` 分支构造参数（位置参数顺序
`目标文件 词表/掩码`，这是 hashcat 的硬约束）；`RealExecutor._native_wordlist_path()`。

### 2. 决策批次大小可配置

`SAGE_DECISION_BATCH_SIZE`（默认 1000，上限 100000）传给候选管线 `iter_plan_batches`，
用于把每批的进程启动开销摊薄。Python 侧生成器（S2/S3/S4/S5）仍是分批执行。

### 3. 命中即停

`ExecutionRequest.stop_on_hit`（缺省取 `SAGE_STOP_ON_HIT`）。为 `true` 时采用真实破解语义：
任一策略恢复出目标后立即结束运行，研究停止原因为 `all_targets_recovered`，
策略消息为"命中即停：已恢复 N 项，停止后续候选"。

### 4. 前端词表入口

创建任务表单新增：**补充候选词表**（粘贴，每行一个，最多 10 万条）+ **导入 `.txt`/`.dic`/`.lst`**
（浏览器端读取后作为高优先级候选提交）+ **命中即停** 复选框。
解析函数 `parseCandidateList` 去空行、去重、丢弃超长（>1024）条目。

## 验证

### 自动化测试

- `tests/test_step1_real_scale.py`
  - 原生词表攻击：断言 hashcat 以 `--attack-mode 0` 启动、命令行里出现字典路径本身、
    **不出现临时候选文件**，且首个词表条目被恢复；
  - 命中即停：只启动一个 hashcat 批次，研究停止原因为 `all_targets_recovered`；
    关闭时同一配置不会以该原因提前结束。
- 前端 `npm test` 27 项（新增：词表解析、词表与命中即停确实进入 execute 请求体）。
- 全量后端 `pytest`：通过；`docs/openapi.json` 已随 `stop_on_hit` 字段重新生成并通过契约测试。

### 真实工具复测（本机 hashcat 7.1.2 + zip2john）

字典 `F:\SA\demo-samples\wordlist-demo.txt`（14 条），`SAGE_WORDLIST_PATH` 指向它，
`SAGE_STOP_ON_HIT=true`，对 WinZip AES 样本 `2024年度报告.zip` 跑真实执行：

```
S1 原生词表：tested=14  recovered=1  time=2.55s   ← 命中 xiaoming2001
S5/S2/S3/S4：tested=0                            ← 命中即停，未再消耗候选
总计：2.0 秒，恢复 1 项
```

对照：同一任务在旧的候选列表模式下需要多轮批次、并逐批启动 hashcat。

## 尚未完成（下一步）

- **候选惰性流式化**：当前 Python 侧生成器（S2~S5）仍在启动时把批次物化到内存，
  上限为 `MAX_EXECUTION_CANDIDATES`（10 万，约几 MB）。真实字典场景已由原生词表攻击解决，
  因此惰性流式化安排在接入 PCFG/Markov 训练模型（千万级候选）时一并落地，避免重复改造。
- 规则文件（`-r`）与掩码/混合攻击（`-a 3/6/7`）已在适配器层预留（`rules_path`/`mask`），
  但计划与调度层尚未暴露，属于下一步。
