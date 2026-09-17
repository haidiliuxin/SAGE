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

- 规则文件（`-r`）与掩码/混合攻击（`-a 3/6/7`）已在适配器层预留（`rules_path`/`mask`），
  但计划与调度层尚未暴露，属于第 2 步。
- Python 生成器路径的 hashcat stdin 常驻（一次进程持续喂候选）：暂不做，
  实测 ZIP AES 为 317 万/秒，把 `SAGE_DECISION_BATCH_SIZE` 调到 10 万后进程启动开销占比 <0.3%。

## 候选惰性流式化（本次补齐）

`_PlanStream`：候选管线仍在**整计划范围内一次生成**——跨策略全局去重、按优先级的
预算切片都发生在这遍里，这一点不能拆（实验证明：把每个调度单元单独生成会改变候选内容，
S3 会从 7 个模板候选退化成基础表，覆盖率下降）。

惰性化体现在**生成时机**上：

- 启动时只为每个单元预取**一批**（`peek`），其余候选留在管线里；
- 调度器选中某单元时才推进管线，直到该单元出现下一批，途中其它单元的批次按优先级排队；
- 批次被消费后立即释放，内存上界不超过"管线单遍产出"，且不会再为未被调度到的单元
  提前生成全部候选；
- 恢复运行时按每个单元已确认消费的批次数 `skip` 快进（生成顺序确定，可精确重建），
  并从快照恢复 `supplied_candidates` 与原生词表配置。

可观测验证：`tests/test_step1_real_scale.py::test_candidates_are_generated_lazily_not_materialized_up_front`
把批次大小设为 2、计划候选预算 20（全部生成需 10 批），命中即停后实际生成 ≤4 批、候选 ≤6 个。

## 上传词表（本次补齐）

任务增加 `wordlist_file_id`（模型列 + 迁移 `0002_task_wordlist_file`）：

- `POST /api/files` 上传 `.txt/.dic/.lst/.dict/.wordlist` 后在创建任务时引用；
- 执行时解析为上传目录中的落盘路径，**直接交给 hashcat**（`--attack-mode 0 <target> <wordlist>`），
  由 hashcat 按需流式读取整本字典——不受 10 万条候选上限、不进入 Python 候选列表、不复制临时文件；
- 上传词表优先于服务器端 `SAGE_WORDLIST_PATH`；非纯文本字典在创建任务时返回 422；
- 前端创建表单可直接选择词表文件（显示大小并提示原生读取）。

验证：`test_uploaded_wordlist_file_drives_native_attack`（hashcat 收到落盘字典路径并命中首条口令）、
`test_wordlist_file_must_be_a_text_dictionary`（.zip 词表被 422 拒绝）。
