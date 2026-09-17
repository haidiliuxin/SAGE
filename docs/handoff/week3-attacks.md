# 规则引擎 / 掩码 / 混合攻击接入记录（修改2 第二次提交整合）

来源：`origin/修改2` 的 `7158587`「接入 Hashcat 规则掩码混合攻击」（dailucy0824）。
该分支基线是旧的 `4b1c1e0`，因此本次是在已整合主线的基础上重新整合（`integration/rev2-attacks`）。

## 已实现的机制

| 层 | 内容 |
| --- | --- |
| 适配器 `hashcat_adapter.py` | `HashcatJob.attack_mode`（0 词表 / 3 掩码 / 6 词表+掩码 / 7 掩码+词表）、`rule_files`、`inline_rules`、`masks`、`custom_charsets`；`_validate_attack_contract` 做攻击契约校验；多掩码写 `.hcmask` 文件、内联规则写 `.rule` 文件；`_attack_position_args` 按模式排列位置参数（掩码直接作为 hashcat 参数） |
| 生成器 `generators/rule.py` | S2 支持 `hashcat_masks`，把掩码作为**紧凑候选流**（只产出掩码本身，不展开明文空间） |
| 执行器 `real_executor.py` | `_hashcat_job_for_strategy`：把计划参数 `hashcat_attack_mode` / `hashcat_masks` / `hashcat_hybrid_mask` / `hashcat_hybrid_position` / `hashcat_rule_files` / `hashcat_inline_rules` / `hashcat_custom_charsets` 映射为原生攻击作业 |
| 外部词表（A 侧第 1 步） | 保留：`HashcatJob.wordlist_path` + `is_native`，整本字典由 hashcat 直接读盘，可与掩码组合成原生混合攻击 |

## 整合中修掉的两个真实缺陷

1. **`--restore-timer` 在 hashcat 7.1.2 中不存在**：仿真 hashcat 不校验参数，所以单元测试全绿，
   但真实工具直接报 `unknown option --restore-timer`。已移除该参数（恢复文件默认自动写入），
   并把对应测试断言改为"不含该参数"。
2. **混合攻击（`-a 6/7`）在校验层拒绝外部词表**：契约要求 `candidates` 非空，
   而外部词表攻击的种子来自字典文件。已放宽为"存在 `candidates` 或 `is_native` 之一即可"。

此外 `HashcatJob` 给 `candidates`/`timeout_seconds`/`candidate_budget` 补了默认值，
使掩码/外部词表作业无需构造空候选列表。

## 真实 hashcat 验证（本机 7.1.2）

| 场景 | 命令要点 | 结果 |
| --- | --- | --- |
| 掩码攻击 | `-a 3 ?d?d?d?d` | 命中 `1234` |
| 混合攻击 | `-a 6 <词表> ?d?d` | 命中 `word12` |
| 规则文件 | `-r demo.rule`（`c toggle1 a5 $1 $2`） | 命中 `word1` |
| 外部词表 + 掩码 | `-a 6 <字典文件> ?d?d`（原生读盘） | 命中 `beta99`，测试 128 个候选 |

全量后端 `pytest` 通过；前端 27 项通过；`docs/openapi.json` 与运行时一致。

## 计划层激活（已完成）

原先缺口是"规划层不会产出这些参数"，现已补齐：

- `config.py`：`SAGE_RULES_PATH`（规则文件）、`SAGE_MASK_LADDER`（掩码阶梯）、
  `SAGE_HYBRID_MASKS`（混合掩码）；
- `planner.py`：`NativeAttackSettings` + `RulePlanner(native_attacks=...)`，
  配置后自动纳入 **S6（掩码/暴力）** 与 **S7（混合攻击）**，并给 S2 注入 `hashcat_rule_files`；
- `policy.py`：白名单、参数规则（掩码列表、规则文件列表）与目标适用性放开 S6/S7；
- `enums.py` + `generators/native.py`：S6/S7 策略编号与原生占位生成器（不产出候选，
  仅用于调度记账）；
- `real_executor.py`：参数驱动的原生单元按"一次性原生作业"调度；混合攻击使用
  `SAGE_WORDLIST_PATH`/任务上传的词表作为 hashcat 位置参数；未配置词表时让出该单元。

真实 hashcat 验证（掩码阶梯 `?d?d?d?d,?l?l?l?l`、混合掩码 `?d?d`、词表 14 条，目标 MD5("1234")）：

```
计划单元: S1, S5, S2, S3, S6, S7
S6 参数: {"hashcat_attack_mode": 3, "hashcat_masks": ["?d?d?d?d", "?l?l?l?l"]}
S6 执行: tested=2  recovered=1        ← -a 3 掩码命中 1234
S7 执行: tested=1400 recovered=0      ← -a 6 词表+掩码
合计: 3778 个候选、22 秒、8 轮调度、命中 1 项
```
