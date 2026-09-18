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

## 五、复现方式

```powershell
# 候选空间覆盖分析（不需要 GPU，秒级完成）
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\benchmark_100.py --space-only
# 真实破解评测（需要 GPU 与充足主机内存）
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\benchmark_100.py
```

输出：`docs/experiments/benchmark-100-<时间戳>.csv` 与 `.md`。
