# 100 条口令评测：用例清单与复现方式（交给队友复测）

> 这份评测回答两个问题：**系统能解出多少"不那么常见"的口令**、**命中是否只来自基线单元（S1）**。
> 语料与词表都由 `scripts/benchmark_100.py` 内置生成（确定性、可复现），不需要额外准备数据。
>
> 下表已标注**本机实测结果**：✅ 命中（含命中策略）、❌ 未命中、⚠ 运行失败。
> 机读版本（带同样标注）：`docs/experiments/benchmark-cases-100.csv`。

## 一、本机结果总览

| 类别 | 完整 100 条（固定顺序 + 不切片，时间预算 60s） | 前 30 条（bandit + 自适应切片，时间预算 120s） | 前 30 条（固定顺序 + 不切片，时间预算 120s） |
| --- | --- | --- | --- |
| dict+num | 10/10 | 10/10 | 10/10 |
| dict+symbol | 5/10 | 5/10 | 5/10 |
| pinyin+num | 10/10 | 10/10 | 10/10 |
| pii | 10/10 | — | — |
| reuse | 9/10 | — | — |
| keyboard | 7/10 | — | — |
| phrase | 7/10 | — | — |
| leet | 1/10 | — | — |
| cn+mixed | 6/10 | — | — |
| hard(对照) | 0/10 | — | — |
| **合计** | **65/100** | **25/30** | **25/30** |

三个配置分别是：

1. **完整 100 条（固定顺序 + 不切片，时间预算 60s）** — `docs/experiments/benchmark-100-20260919-0316.csv`
2. **前 30 条（bandit + 自适应切片，时间预算 120s）** — `docs/experiments/benchmark-100-20260919-1042-adaptive.csv`
3. **前 30 条（固定顺序 + 不切片，时间预算 120s）** — `docs/experiments/benchmark-100-20260919-1049-fixed-noslice.csv`

## 二、怎么跑（最短路径）

```powershell
git fetch origin
git checkout feat/adaptive-native-slicing        # 自适应切片分支；旧行为在 main 上
python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
$env:PYTHONPATH='src'

# 1) 自适应（bandit 调度 + 原生攻击切片）—— 30 条快速口径
.\.venv\Scripts\python.exe scripts\benchmark_100.py --limit 30 `
    --scheduler heuristic_bandit --tag adaptive --out docs/experiments

# 2) 基线（固定顺序 + 不切片 = 改动前行为）
.\.venv\Scripts\python.exe scripts\benchmark_100.py --limit 30 `
    --scheduler fixed --no-adaptive-slicing --tag fixed-noslice --out docs/experiments

# 3) 对照汇总（命中 / 命中策略数 / 决策数 / 探索-利用 / 各臂拉取与命中）
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts\compare_schedulers.py

# 4) 完整 100 条（约 40~60 分钟，取决于 GPU）
.\.venv\Scripts\python.exe scripts\benchmark_100.py --out docs/experiments
```

**前置依赖**（脚本顶部常量，路径不同就改这三处）：

- hashcat 7.1.2：`HASHCAT = r"F:\SA\tools\hashcat-7.1.2\hashcat.exe"`（需 `rules/` 目录）
- 规则链（两份，hashcat 自带）：
- `best66.rule`
- `d3ad0ne.rule`
- John the Ripper run 目录（`zip2john.exe`，评测里用不到但脚本会设置）：`JOHN_RUN`

**运行环境要求**：GPU（本机 RTX 4060 Laptop 8GB）；主机内存 ≥ 4GB 空闲
（规则链 225 万条/词，内存不足会报 `Not enough allocatable memory (RAM) for this ruleset`）。

**脚本固定配置**（想复现同样的数字就别改）：时间预算 120s、候选预算 10 亿、
批大小 100000、命中即停、词表 81 条（`data/benchmark/base-words.txt`，脚本生成）、
掩码阶梯 `?d?d?d?d,?l?l?l?l,?l?l?l?l?d?d`、混合掩码 `?d?d?d?d,?d?d,!`、
planner=`rule`、`SAGE_SEED_WORDLISTS=data/wordlists/zh-base.txt`。

## 三、用例清单（100 条，10 类各 10 条）

| 类别 | 条数 | 设计意图 |
| --- | --- | --- |
| dict+num | 10 | 词典词 + 数字/年份（`summer2023`、`dragon88`） |
| dict+symbol | 10 | 词典词 + 符号 + 大小写/leet 混合（`Dr@gon!88`、`P@ssw0rd2024`） |
| pinyin+num | 10 | 拼音 + 数字（`woaini1314`、`beijing2008`） |
| pii | 10 | 个人信息派生（姓名/昵称/生日/手机尾号/邮箱） |
| reuse | 10 | 旧口令复用（提供 `historical_passwords`，含换年份/换符号） |
| keyboard | 10 | 键盘序列（`1qaz2wsx`、`zxcvbnm!`） |
| phrase | 10 | 短语/多词（`correct-horse`、`welcometothejungle`） |
| leet | 10 | leet 替换（`Tr0ub4dor&3`、`H@ck3r2020`） |
| cn+mixed | 10 | 中文与中英混合（`密码123`、`张三@1998`） |
| hard（对照） | 10 | 高熵随机口令，**预期全部不可达**，用于确认评测没有"作弊" |

机读版本：`docs/experiments/benchmark-cases-100.csv`
（列：id / category / password / md5 / context / historical_passwords / 各配置结果）。

评测里的 PII（`pii` 与部分 `cn+mixed` 用例共用）：

```json
{
  "authorized_keywords": [
    "示例大学",
    "网络安全"
  ],
  "birth_year": 1998,
  "birthday": "03-05",
  "email_local_part": "zhangsan.work",
  "interest_words": [
    "摄影",
    "篮球"
  ],
  "name": "张三",
  "nickname": "小明",
  "organization": "示例大学",
  "phone_suffix": "7788",
  "region": "北京",
  "username": "zhangsan",
  "years": [
    2019,
    2020,
    2021,
    2023,
    2024,
    2025
  ]
}
```

### 明细（含本机结果）

| ID | 类别 | 口令 | 完整 100 条（固定顺序 + 不切片，时间预算 60s） | 前 30 条（bandit + 自适应切片，时间预算 120s） | 前 30 条（固定顺序 + 不切片，时间预算 120s） | 附带信息 |
| --- | --- | --- | --- | --- | --- | --- |
| C001 | dict+num | `summer2023` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C002 | dict+num | `dragon88` | ✅ S1 | ✅ S7 | ✅ S1 | — |
| C003 | dict+num | `coffee2024` | ✅ S1 | ✅ S7 | ✅ S1 | — |
| C004 | dict+num | `monkey123` | ✅ S1 | ✅ S5 | ✅ S1 | — |
| C005 | dict+num | `flower99` | ✅ S1 | ✅ S7 | ✅ S1 | — |
| C006 | dict+num | `guitar007` | ✅ S1 | ✅ S3 | ✅ S1 | — |
| C007 | dict+num | `banana2020` | ✅ S1 | ✅ S7 | ✅ S1 | — |
| C008 | dict+num | `rocket42` | ✅ S1 | ✅ S7 | ✅ S1 | — |
| C009 | dict+num | `silver77` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C010 | dict+num | `thunder21` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C011 | dict+symbol | `P@ssw0rd2024` | ✅ S2 | ✅ S2 | ✅ S2 | — |
| C012 | dict+symbol | `Dr@gon!88` | ❌ 未命中 | ❌ 未命中 | ❌ 未命中 | — |
| C013 | dict+symbol | `C0ffee@7` | ❌ 未命中 | ❌ 未命中 | ❌ 未命中 | — |
| C014 | dict+symbol | `Monkey#123` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C015 | dict+symbol | `H0use!2021` | ❌ 未命中 | ❌ 未命中 | ❌ 未命中 | — |
| C016 | dict+symbol | `Sh@dow99` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C017 | dict+symbol | `Summ3r!23` | ❌ 未命中 | ❌ 未命中 | ❌ 未命中 | — |
| C018 | dict+symbol | `Winter#2024` | ✅ S3 | ✅ S3 | ✅ S3 | — |
| C019 | dict+symbol | `Rocket@21` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C020 | dict+symbol | `Fl0wer!7` | ❌ 未命中 | ❌ 未命中 | ❌ 未命中 | — |
| C021 | pinyin+num | `woaini1314` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C022 | pinyin+num | `wodemima888` | ✅ S1 | ✅ S5 | ✅ S1 | — |
| C023 | pinyin+num | `zhongguo2023` | ✅ S1 | ✅ S5 | ✅ S1 | — |
| C024 | pinyin+num | `beijing2008` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C025 | pinyin+num | `shanghai2020` | ✅ S1 | ✅ S5 | ✅ S1 | — |
| C026 | pinyin+num | `xuexi1234` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C027 | pinyin+num | `gongzuo2024` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C028 | pinyin+num | `jiating666` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C029 | pinyin+num | `pengyou520` | ✅ S1 | ✅ S3 | ✅ S1 | — |
| C030 | pinyin+num | `laoshi888` | ✅ S1 | ✅ S1 | ✅ S1 | — |
| C031 | pii | `zhangsan1998` | ✅ S1 | — | — | PII（见下方） |
| C032 | pii | `Xiaoming_01` | ✅ S1 | — | — | PII（见下方） |
| C033 | pii | `zhangsan0305` | ✅ S7 | — | — | PII（见下方） |
| C034 | pii | `Xiaoming1998` | ✅ S1 | — | — | PII（见下方） |
| C035 | pii | `zhangsan7788` | ✅ S1 | — | — | PII（见下方） |
| C036 | pii | `Zhangsan2024` | ✅ S1 | — | — | PII（见下方） |
| C037 | pii | `xiaoming2001` | ✅ S1 | — | — | PII（见下方） |
| C038 | pii | `zhangsan_1998` | ✅ S4 | — | — | PII（见下方） |
| C039 | pii | `Xiaoming#1998` | ✅ S4 | — | — | PII（见下方） |
| C040 | pii | `zhangsan.work` | ✅ S4 | — | — | PII（见下方） |
| C041 | reuse | `Sunshine2023#` | ❌ 未命中 | — | — | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C042 | reuse | `Admin@2025` | ✅ S4 | — | — | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C043 | reuse | `Hunter2!2024` | ✅ S4 | — | — | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C044 | reuse | `Qwerty2024` | ✅ S1 | — | — | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C045 | reuse | `sunshine2023!` | ✅ S4 | — | — | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C046 | reuse | `Admin@2020!` | ✅ S4 | — | — | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C047 | reuse | `hunter2!2018` | ✅ S4 | — | — | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C048 | reuse | `Qwerty2019!` | ✅ S4 | — | — | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C049 | reuse | `Sunshine2019` | ✅ S1 | — | — | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C050 | reuse | `Admin2025` | ✅ S1 | — | — | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C051 | keyboard | `1qaz2wsx` | ✅ S1 | — | — | — |
| C052 | keyboard | `qazwsx123` | ✅ S1 | — | — | — |
| C053 | keyboard | `zxcvbnm!` | ❌ 未命中 | — | — | — |
| C054 | keyboard | `1q2w3e4r` | ✅ S1 | — | — | — |
| C055 | keyboard | `asdfghjkl1` | ✅ S1 | — | — | — |
| C056 | keyboard | `qwerty789` | ✅ S1 | — | — | — |
| C057 | keyboard | `poiuytrewq` | ✅ S1 | — | — | — |
| C058 | keyboard | `1qaz@wsx` | ✅ S1 | — | — | — |
| C059 | keyboard | `zxcasdqwe` | ❌ 未命中 | — | — | — |
| C060 | keyboard | `qweasdzxc2` | ❌ 未命中 | — | — | — |
| C061 | phrase | `correct-horse` | ❌ 未命中 | — | — | — |
| C062 | phrase | `iloveyou2` | ✅ S1 | — | — | — |
| C063 | phrase | `letmein123` | ✅ S1 | — | — | — |
| C064 | phrase | `mypassword2024` | ✅ S1 | — | — | — |
| C065 | phrase | `trustno1` | ✅ S1 | — | — | — |
| C066 | phrase | `opensesame` | ✅ S1 | — | — | — |
| C067 | phrase | `helloworld88` | ✅ S1 | — | — | — |
| C068 | phrase | `goodmorning7` | ✅ S1 | — | — | — |
| C069 | phrase | `ilovechina2020` | ❌ 未命中 | — | — | — |
| C070 | phrase | `welcometothejungle` | ❌ 未命中 | — | — | — |
| C071 | leet | `Tr0ub4dor&3` | ❌ 未命中 | — | — | — |
| C072 | leet | `P@ssw0rd!` | ✅ S1 | — | — | — |
| C073 | leet | `Adm1n1str@tor` | ❌ 未命中 | — | — | — |
| C074 | leet | `S3cur1ty2024` | ❌ 未命中 | — | — | — |
| C075 | leet | `H@ck3r2020` | ❌ 未命中 | — | — | — |
| C076 | leet | `M@st3rP13ce` | ❌ 未命中 | — | — | — |
| C077 | leet | `D@rkN1ght99` | ❌ 未命中 | — | — | — |
| C078 | leet | `Bl@ckW1d0w` | ❌ 未命中 | — | — | — |
| C079 | leet | `Cyb3rPunk2077` | ❌ 未命中 | — | — | — |
| C080 | leet | `N3v3rG0nna` | ❌ 未命中 | — | — | — |
| C081 | cn+mixed | `密码123` | ✅ S1 | — | — | PII（见下方） |
| C082 | cn+mixed | `我的密码2024` | ✅ S1 | — | — | PII（见下方） |
| C083 | cn+mixed | `张三@1998` | ✅ S4 | — | — | PII（见下方） |
| C084 | cn+mixed | `生日快乐2024` | ✅ S1 | — | — | PII（见下方） |
| C085 | cn+mixed | `woaini520!` | ✅ S5 | — | — | PII（见下方） |
| C086 | cn+mixed | `xiaoming_2001` | ❌ 未命中 | — | — | PII（见下方） |
| C087 | cn+mixed | `zhang@123` | ✅ S1 | — | — | PII（见下方） |
| C088 | cn+mixed | `123456abc` | ❌ 未命中 | — | — | PII（见下方） |
| C089 | cn+mixed | `a1b2c3d4` | ❌ 未命中 | — | — | PII（见下方） |
| C090 | cn+mixed | `password!@#2024` | ❌ 未命中 | — | — | PII（见下方） |
| C091 | hard(对照) | `X7#kL9$mQ2` | ❌ 未命中 | — | — | — |
| C092 | hard(对照) | `vN4$tR8*wZ` | ❌ 未命中 | — | — | — |
| C093 | hard(对照) | `9f2K!pQ7#x` | ❌ 未命中 | — | — | — |
| C094 | hard(对照) | `m3L$vB8nQ1!` | ❌ 未命中 | — | — | — |
| C095 | hard(对照) | `Zx9!Rk2#Wt` | ❌ 未命中 | — | — | — |
| C096 | hard(对照) | `7uJ#pL4$xN` | ❌ 未命中 | — | — | — |
| C097 | hard(对照) | `Qw8$Zr3!Vm` | ❌ 未命中 | — | — | — |
| C098 | hard(对照) | `Lk5#Bn9$Xy` | ❌ 未命中 | — | — | — |
| C099 | hard(对照) | `Tf2!Wq7#Rp` | ❌ 未命中 | — | — | — |
| C100 | hard(对照) | `8hM$xK4!Vd` | ❌ 未命中 | — | — | — |

## 四、本机未命中的 35 条（完整 100 条口径）

| ID | 类别 | 口令 | 本机未命中原因 |
| --- | --- | --- | --- |
| C012 | dict+symbol | `Dr@gon!88` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C013 | dict+symbol | `C0ffee@7` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C015 | dict+symbol | `H0use!2021` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C017 | dict+symbol | `Summ3r!23` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C020 | dict+symbol | `Fl0wer!7` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C041 | reuse | `Sunshine2023#` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C053 | keyboard | `zxcvbnm!` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C059 | keyboard | `zxcasdqwe` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C060 | keyboard | `qweasdzxc2` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C061 | phrase | `correct-horse` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C069 | phrase | `ilovechina2020` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C070 | phrase | `welcometothejungle` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C071 | leet | `Tr0ub4dor&3` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C073 | leet | `Adm1n1str@tor` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C074 | leet | `S3cur1ty2024` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C075 | leet | `H@ck3r2020` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C076 | leet | `M@st3rP13ce` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C077 | leet | `D@rkN1ght99` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C078 | leet | `Bl@ckW1d0w` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C079 | leet | `Cyb3rPunk2077` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C080 | leet | `N3v3rG0nna` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C086 | cn+mixed | `xiaoming_2001` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C088 | cn+mixed | `123456abc` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C089 | cn+mixed | `a1b2c3d4` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C090 | cn+mixed | `password!@#2024` | Python 候选空间未覆盖；原生单元（词表 × 规则链 / 掩码 / 混合）已实测仍未命中 |
| C091 | hard(对照) | `X7#kL9$mQ2` | 设计上的不可达样本（高熵对照） |
| C092 | hard(对照) | `vN4$tR8*wZ` | 设计上的不可达样本（高熵对照） |
| C093 | hard(对照) | `9f2K!pQ7#x` | 设计上的不可达样本（高熵对照） |
| C094 | hard(对照) | `m3L$vB8nQ1!` | 设计上的不可达样本（高熵对照） |
| C095 | hard(对照) | `Zx9!Rk2#Wt` | 设计上的不可达样本（高熵对照） |
| C096 | hard(对照) | `7uJ#pL4$xN` | 设计上的不可达样本（高熵对照） |
| C097 | hard(对照) | `Qw8$Zr3!Vm` | 设计上的不可达样本（高熵对照） |
| C098 | hard(对照) | `Lk5#Bn9$Xy` | 设计上的不可达样本（高熵对照） |
| C099 | hard(对照) | `Tf2!Wq7#Rp` | 设计上的不可达样本（高熵对照） |
| C100 | hard(对照) | `8hM$xK4!Vd` | 设计上的不可达样本（高熵对照） |

其中 `hard(对照)` 的 10 条是**设计上的不可达样本**，可用于检查评测是否"作弊"；
其余未命中是下一步优化项，见 `docs/experiments/benchmark-100-findings.md` 的
"下一轮优化清单"（leet 叠加规则、历史口令结构变换、词表补键盘序列、组合攻击、结构模板）。

## 五、结果怎么读（复测时请按同一口径）

每个用例一行，关键列：

- `recovered` / `hit_strategy`：是否命中、由哪个策略命中（**复测重点：不应只有 S1**）；
- `tested`：本用例实际测试的候选总量；
- `stop_reason`：`all_targets_recovered`（命中即停）/ `candidates_exhausted`（候选耗尽）/
  `strategy_budgets`（各单元时间预算耗尽）；
- `native_units`：原生单元的**键空间**（S1 词表 × 规则链、S6 掩码、S7 词表 × 掩码）；
- `plan_arms`：Python 候选单元产出量；
- `reason`：未命中归因（Python 候选空间未覆盖 / 原生单元已实测仍未命中）；
- `run_status` / `run_message`：运行状态与 hashcat 失败原因（排查用）。

## 六、已知差异点（复测时容易踩）

1. **hashcat 的多个 `-r` 是规则链（乘积）**，不是并集：`best66 × d3ad0ne` 每个词
   225 万条规则，81 词 ≈ 1.8 亿键；规则表吃主机内存，三份以上大规则文件会失败。
2. **原生单元的每次启动有固定开销**（本机 5~9 秒），所以自适应切片的"探针 + 提交"
   需要足够的时间预算；30 秒预算下切片会挤掉覆盖，请用 120 秒（脚本默认）。
3. **无 GPU 时可先跑候选空间诊断**（不调用 hashcat，秒级）：
   `.\.venv\Scripts\python.exe scripts\benchmark_100.py --space-only`。
4. 复测结果请连 `--tag` 一起归档，便于和 `docs/experiments/` 里的历史结果对照。
