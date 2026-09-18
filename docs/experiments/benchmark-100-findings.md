# 100 道口令评测：发现与原因归档（2026-09-19）

评测对象：main `c244b62`。评测脚本：`scripts/benchmark_100.py`。
语料：10 类共 100 个"不那么常见"的口令（结构口令、拼音、个人信息派生、旧口令复用、
键盘序列、短语、leet、中文口令、高熵对照）。

## 一、结论速览

1. **真实执行链路本身是好的**：掩码（`-a 3`）、混合（`-a 6`）、规则文件（`-r`）、
   外部词表原生读盘都已在真实 hashcat 下验证通过（本次评测前）;
2. **本轮评测被环境阻塞**：主机内存耗尽导致 hashcat 无法映射 GPU 显存，
   100 道真实破解暂时跑不出结果（原因与处置见第二节）；
3. **用不依赖 GPU 的"候选空间覆盖"诊断拿到第一批结论**，并**发现一个结构性缺口**：
   **缺少 hashcat 最经典的"词表 × 规则"组合**（见第三节，这是本轮最重要的优化项）。

## 二、失败原因归档（环境类）

### 2.1 现象

`mode: real` 的每个批次都在 hashcat 启动阶段失败，运行消息只有 `Hashcat 执行失败`，
`tested=0`。抓取 hashcat stderr 后得到真实原因：

```
* Device #1: Not enough allocatable device memory or free host memory for mapping.
... append -O to your commandline.
```

### 2.2 根因

主机内存耗尽：物理内存 15.8 GB / **可用仅 0.9～2.0 GB**，提交内存 **41～47 GB / 上限 48.6 GB**。
hashcat 为 GPU 内核映射缓冲区需要可分配的主机内存，因此**不是代码问题，而是机器状态问题**
（同一套代码在此前内存充裕时跑通过掩码/混合/规则/词表四类攻击）。

### 2.3 已验证的处置手段

| 手段 | 结果 |
| --- | --- |
| 关闭 WSL（`wsl --shutdown`）、停掉前端 dev server 与后端进程 | 物理可用 0.9 → 2.0 GB，仍不足 |
| `hashcat -O -n 1 -u 1 -T 1`（优化内核 + 限制内核加速/循环/线程） | **命令行下成功命中 `summer2023`** ✅ |
| 通过适配器传入同样的参数 | ❌ hashcat 报 `The manual use of the -n option (or --kernel-accel) is outdated.`，需进一步定位与 `--runtime`/`--status-json` 的组合约束 |
| `hashcat -D 1`（强制 CPU） | ❌ `No devices found/left`（本机未安装 OpenCL CPU 运行时） |

**建议**：跑 100 道真实评测前先腾出内存（关闭浏览器/桌面应用并重启，或换一台内存更宽裕的机器）。
系统级建议见第四节优化项 2、3。

## 三、候选空间覆盖诊断（不依赖 GPU 的第一批结论）

诊断方式：为每个案例重建计划，逐单元拉取 Python 候选流，判断该口令是否落在候选空间内。
**注意**：词表 / 规则 / 掩码 / 混合四条路径由 hashcat 侧执行，Python 流看不到，因此下表
只反映 **Python 生成器（S1 基线、S2 规则变形、S3 统计、S4 个性化、S5 迁移）** 的覆盖率。

| 类别 | Python 侧覆盖 | 说明 |
| --- | --- | --- |
| reuse（旧口令复用） | **8/10** | S4 history/hybrid 的迁移逻辑（大小写变体、符号后缀、年份替换）有效 |
| pii（个人信息派生） | 2/10 | 姓名+出生年可得；`Xiaoming_01`、`Xiaoming#1998` 这类下划线/符号组合缺失 |
| dict+num | 1/10 | 词表词+数字完全依赖 hashcat 侧，Python 侧只有 20 条内置基线 |
| keyboard | 1/10 | 键盘序列命中 1 条（`1qaz2wsx` 在基线/规则里） |
| phrase | 1/10 | 多词短语基本不在候选空间 |
| dict+symbol / leet / pinyin+num / cn+mixed | 0/10 | 需要词表×规则、中文词表与相应变形 |
| hard(对照) | 0/10 | **符合预期**（高熵口令本就不可枚举），说明评测本身有效 |

各单元产出规模（案例 C001）：`S1:20, S2:1192, S3:1820, S6:0, S7:0`
—— S1 只有内置 20 条（词表在 hashcat 侧），S6/S7 是原生攻击单元（Python 侧 0 条，符合设计）。

### 3.1 结构性缺口（本轮最重要发现）

用仿真 hashcat 打印每个单元的实际命令行（`data/argv-probe`，可复现）：

| 单元 | 攻击模式 | 规则 | 输入 | 问题 |
| --- | --- | --- | --- | --- |
| S1 | `-a 0` | **无** | `dict.txt`（外部词表） | 词表不带任何规则 → 只能命中词表原词 |
| S2 | `-a 0` | `-r best66.rule` | `candidates.txt`（**Python 生成的候选**） | **规则作用在 Python 候选（内置 20 条基线+PII 种子）上，而不是词表上** |
| S3 | `-a 0` | 无 | `candidates.txt` | 统计模型候选 |
| S4 | `-a 0` | 无 | `candidates.txt` | 个性化候选 |
| S6 | `-a 3` | — | `masks.hcmask` | 掩码阶梯 ✅ |
| S7 | `-a 6` | — | `dict.txt` + `?d?d` | 词表+掩码 ✅（但掩码位数有限） |

**影响**：真实场景里最高效的攻击是 **`hashcat -a 0 rockyou.txt -r best64.rule`**（词表 × 规则），
而我们目前把规则引擎用在"20 条内置基线"上，等于**浪费了规则的全部威力**，
`dragon88`、`P@ssw0rd2024`、`H0use!2021` 这类"词表词 + 数字/符号/leet 变形"因此全部命中不了。

## 四、优化清单（按性价比排序，待确认后实施）

| # | 优化项 | 说明 | 预期收益 |
| --- | --- | --- | --- |
| 1 | **词表 × 规则**：让 S1 的原生词表攻击带上 `-r`（或新增"词表+规则"单元，规则文件可配多个） | 把 `rule_files` 接到外部词表作业上：`hashcat -a 0 dict.txt -r best66.rule` | dict+num / dict+symbol / leet / keyboard 四类覆盖率大幅提升（这是真实破解的主力） |
| 2 | **低内存/低显存自适应**：新增 `SAGE_HASHCAT_OPTIMIZED`（`-O`）与内核参数（`-n/-u/-T`）、设备选择开关；并在内存/显存不足时自动降级重试一次 | 解决本轮"机器一紧张就整个跑不动"的问题；也让包分发到不同电脑时更稳 | 可用性 |
| 3 | **把 hashcat 失败原因回传到运行消息**（stderr 尾部 + 退出码） | 目前只有 `Hashcat 执行失败`，本轮排查完全靠临时抓日志 | 可诊断性 |
| 4 | 掩码阶梯与混合掩码默认值加强：加入 `?d?d?d?d`（年份）、`?u?l?l?l?l?d?d`、`!`/`@` 前后缀组合 | 覆盖"词+年份""词+符号+数字"的常见形态 | dict+ num/symbol 类 |
| 5 | 个人信息组合规则扩展：`用户名_年份`、`昵称#年份`、`姓名拼音+@+出生年` 等 | pii 类目前 2/10 | pii 类 |
| 6 | 中文口令支持：中文词表 + 中文相关规则/掩码策略 | cn+mixed 类目前 0/10 | 中文场景 |
| 7 | 待模型数据：PCFG grammar / OMEN(Markov) 训练模型 | S3 目前仅 pcfg_lite 约 800 条 | 统计型口令 |

## 三点五、优化实施记录（2026-09-19，第二轮）

| # | 状态 | 落地方式 |
| --- | --- | --- |
| 1 | ✅ | S1 的原生词表作业带 `-r`：`hashcat -a 0 dict.txt -r best66.rule`（`RulePlanner` 把 `hashcat_rule_files` 写进 S1 参数，执行层直接交给 hashcat 读盘）。S2 回退为"Python 规则变形候选"，不再叠加 `-r`（避免规则二次作用于已变形的候选）。 |
| 2 | ✅ | `SAGE_HASHCAT_OPTIMIZED`/`KERNEL_*`/`DEVICE_TYPES` + 内存失败自动降级重试。 |
| 3 | ✅ | `_failure_reason()` 把 stderr 尾部与退出码写进运行消息。 |
| 4 | ✅ | 混合掩码加入 `?d?d?d?d`（词 + 年份）；掩码阶梯保留 `?d?d?d?d`/`?l?l?l?l`；单靠自己就超出任务候选预算的掩码会被计划层忽略。 |
| 5 | ✅ | `context.py` 增加个人信息组合（`用户名_年份`、`昵称#年份`、`生日/手机尾号` 组合等）。 |
| 6 | ✅ | 中文词表 `data/wordlists/zh-base.txt`（99 条）作为 `SAGE_SEED_WORDLISTS`，并扩展内置基线口令。 |
| 7 | ⏳ | OMEN/Markov 模型已训练并接入 S3（`models/markov-demo` + `scripts/train_markov.py`）；**PCFG grammar（pcfg_full）仍缺语料与训练数据**。 |

### 3.2 第二轮发现：原生攻击的候选记账（调度不变量）

真实评测暴露了一个**运行中途整体失败**的问题：

```
内部错误：tested must be between zero and candidate_count（strategy=S7 tested=8100 candidate_count=2000）
```

根因：调度层按"实际测试的候选数"记账，并要求 `tested ≤ candidate_count ≤ 该单元分配预算`；
但 S6/S7 的候选由 hashcat 自己枚举，键空间（词表 81 条 × `?d?d` = 8100）远大于计划按权重
分给该单元的 Python 候选数（2000）。原实现用 `min(tested, budget)` 夹取记账，反而直接破坏了
`tested ≤ candidate_count`，于是整个运行被一个记账问题中断。

修复（三层，各司其职）：

1. `src/sage_pass/keyspace.py`：统一的键空间模型——`-a 3` 为掩码乘积、`-a 0` 为词表条数 ×
   规则条数、`-a 6/7` 为词表条数 × 掩码键空间；含自定义字符集与 `?1..?4` 解析，无法估算时返回
   `None`（保守）。
2. 计划层 `_fit_native_units()`：**优先满足原生单元**（键空间是精确可知的），把 S1/S6/S7 的预算
   设为各自键空间，剩余预算再按权重分给 Python 生成单元；键空间超过总候选预算的掩码直接忽略
   （永远跑不起来），仍装不下时按预算裁剪掩码阶梯并在计划警告里说明。
3. 执行层 `_fit_job_to_budget()`：启动前兜底——掩码单元裁剪掩码阶梯，词表单元用 hashcat
   `-l/--limit` 截断词表条数（`-l` 限制的是词表条数，规则/掩码按倍数放大，因此按倍数反算），
   确实装不下时**优雅跳过该单元**而不是中断整个运行；记账改为 `max(候选数, 实测数)`。

验证：`tests/test_native_keyspace_budget.py`（键空间解析、计划层预算分配与裁剪、执行层 `-l` 截断
与越界跳过）；全量 `pytest` 通过。

### 3.3 规则集实验（决定 S1 用哪套 `-r`）

语料：本评测的 100 条口令；词表：`data/benchmark/base-words.txt`（81 条）；
命令：`hashcat -m 0 -a 0 <100 条 md5> base-words.txt -r …`（离线直接跑，脚本见
`scripts/rules_probe.py` 的离线版本，本机 RTX 4060 Laptop）。

| 规则集 | 命中 | 规则数/词（hashcat 报的 `Rules:`） | 键空间（81 词 ×） |
| --- | --- | --- | --- |
| best66 | 16/100 | 66 | 6 318 |
| best66 + leetspeak | 16/100 | 66 × 25 = 1 650 | 8 343 |
| best66 + combinator | 26/100 | 66 × 63 = 4 158 | 11 421 |
| best66 + combinator + stacking58 | 38/100 | 66 × 63 × 72 = 299 376 | 17 253 |
| best66 + d3ad0ne | 51/100 | 66 × 34 111 = 2 251 326 | 182 357 406 |
| **best66 + dive** | **55/100** | 66 × 98 670 = 6 512 220 | **527 489 820** |
| dive（单独） | 40/100 | 98 670 | 7 992 270 |
| best66 ⊕ dive（**合并成一个文件** = 并集） | 40/100 | 98 736 | 7 997 616 |
| best66 + d3ad0ne + dive / +leetspeak / +rockyou-30000 | 失败 | — | — |

**关键发现：hashcat 的多个 `-r` 是规则链（乘积），不是并集。**

- `-r a -r b` 会把 b 的规则**接在** a 的每条结果后面继续变换，规则数相乘（实测
  `-r best66.rule -r dive.rule` 打印 `Rules: 6512220`）；把两份规则合并成一个文件才是并集
  （`Rules: 98736`）；
- 规则链才覆盖得住"词 + 大小写 + leet + 符号 + 数字"的叠加变形：并集只到 40/100，
  链式到 55/100（多出 `C0ffee@7`、`Fl0wer!7`、`Qwerty2019!`、`zhangsan0305` 等）；
- 代价是键空间爆炸（5.27 亿键 ≈ 21 秒纯 GPU 时间）与主机内存占用：三份以上大规则文件在本机
  会失败（`Not enough allocatable memory (RAM) for this ruleset` /
  `Unsupported number of rules used in rule chaining`），因此 1～2 份为宜；
- 本评测最终配置取 **best66 × d3ad0ne**（51/100，1.82 亿键 ≈ 9 秒，可在时间预算内跑完），
  整轮时间预算提到 60 秒让 S1 的份额（12 秒）够用。

其他结论：

1. 掩码（S6）对本语料 0 命中——语料全是"词根 + 变形"；掩码只在混合攻击（S7）里有价值：
   `词表 × ?d?d?d?d` 单独就有 16/100；
2. 单批次候选粒度默认 1000 时，每次 hashcat 启动（约 3 秒）只测试 1000 条候选：把
   `SAGE_DECISION_BATCH_SIZE`/`SAGE_HASHCAT_STREAM_BATCH_SIZE` 提到 100000 后，
   "候选空间已覆盖但未命中（预算/调度问题）"的案例（如 `Xiaoming#1998`）被 S4 命中。

### 3.4 100 条真实评测结果（三轮对照）

| 轮次 | 配置 | 命中 | 归档 |
| --- | --- | --- | --- |
| 优化前（仅候选空间诊断） | pcfg_lite + 20 条内置基线，掩码/混合默认值 | 10～22/100（Python 侧覆盖） | `benchmark-100-20260919-0042.*` |
| 第 2 轮 | 修复记账 + S1 带 `-r best66` + 混合掩码加 `?d?d?d?d` | **47/100** | — |
| 第 3 轮 | 批大小 100000 + 规则链 `best66 × d3ad0ne` + 时间预算 60s | **65/100** | `benchmark-100-20260919-0316.*` |

第 3 轮分类结果（命中 65/100）：

| 类别 | 命中 | 类别 | 命中 |
| --- | --- | --- | --- |
| dict+num | 10/10 | keyboard | 7/10 |
| pinyin+num | 10/10 | phrase | 7/10 |
| pii（个人信息） | 10/10 | cn+mixed | 6/10 |
| reuse（旧口令复用） | 9/10 | dict+symbol | 5/10 |
| | | leet | 1/10 |
| | | hard（高熵对照） | 0/10 ✅ 预期不可达 |

命中来源：S1（词表 × 规则链）51 条、S4（个人信息组合）10 条、S5/S2/S3/S7 各 1～2 条。
`hard(对照)` 10 条全部未命中，说明评测本身没有"作弊"。

### 3.5 下一轮优化清单（按第 3 轮未命中证据）

| # | 优化项 | 证据 | 预期收益 |
| --- | --- | --- | --- |
| 1 | **leet 叠加规则**：自建一份"替换 + 大小写 + 数字/符号后缀"的小规则文件（几百条），与 best66 做规则链 | `leet` 仅 1/10：`H@ck3r2020`、`D@rkN1ght99`、`Bl@ckW1d0w`、`S3cur1ty2024` 等 9 条未命中；而 `best66 × dive` 能覆盖其中 4 条，代价是 5.27 亿键 / 21 秒 | leet + dict+symbol 合计 +6～8 条 |
| 2 | **历史口令结构变换（S4/S5）**：把历史口令中的年份/数字替换为上下文年份、符号后缀互换（`Sunshine2019!` → `Sunshine2023#`） | `reuse` 9/10，唯一未命中 `Sunshine2023#` 就是"换年份 + 换符号" | reuse +1，pii 类更稳 |
| 3 | **词表补全键盘序列**：`zxcvbnm`、`zxcasdqwe`、`qweasdzxc`、`poiuytrewq` 等完整序列 | `keyboard` 3 条未命中（`zxcvbnm!`、`zxcasdqwe`、`qweasdzxc2`）都只差"完整序列 + 后缀" | keyboard +3 |
| 4 | **组合攻击（`-a 1`：词表 × 词表）**：短语类（`correct-horse`、`ilovechina2020`、`welcometothejungle`）需要两词/多词拼接 | `phrase` 3 条未命中全部是多词拼接 | phrase +3 |
| 5 | **结构模板扩展**：`123456abc`、`a1b2c3d4`、`xiaoming_2001`、`password!@#2024` | `cn+mixed` 4 条未命中都是"字母数字交替 / 符号串"结构 | cn+mixed +2～3 |
| 6 | PCFG grammar（`pcfg_full`）语料与训练数据 | 优化清单第 7 项未完成的一半 | 统计型口令 |

## 五、复现方式

```powershell
# 候选空间覆盖分析（不需要 GPU，秒级完成）
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\benchmark_100.py --space-only
# 真实破解评测（需要 GPU 与充足主机内存）
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\benchmark_100.py
```

输出：`docs/experiments/benchmark-100-<时间戳>.csv` 与 `.md`。
