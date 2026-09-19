# 100 条口令评测：用例清单与复现方式（交给队友复测）

> 这份评测回答两个问题：**系统能解出多少"不那么常见"的口令**、**命中是否只来自基线单元（S1）**。
> 语料与词表都由 `scripts/benchmark_100.py` 内置生成（确定性、可复现），不需要额外准备数据。

## 一、怎么跑（最短路径）

```powershell
git fetch origin
git checkout feat/adaptive-native-slicing        # 自适应切片分支；基线行为在 main 上也有
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

## 二、用例清单（100 条，10 类各 10 条）

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
（列：id / category / password / md5 / context / historical_passwords）。

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

### 明细

| ID | 类别 | 口令 | MD5 | 附带信息 |
| --- | --- | --- | --- | --- |
| C001 | dict+num | `summer2023` | `21ca4318f03e978629157d88ef647031` | — |
| C002 | dict+num | `dragon88` | `2afae277859d16ad40b6c2d4bea2d4c6` | — |
| C003 | dict+num | `coffee2024` | `8912dbe83422ad2f1dad6c1c819bf158` | — |
| C004 | dict+num | `monkey123` | `cc25c0f861a83f5efadc6e1ba9d1269e` | — |
| C005 | dict+num | `flower99` | `01a214d6d5e5dea4c1f4d16dd9617c06` | — |
| C006 | dict+num | `guitar007` | `10d6fb1bd26abccd68b14e8ad7b6420e` | — |
| C007 | dict+num | `banana2020` | `2ae854e8b8f1e76d797313f2254c9d06` | — |
| C008 | dict+num | `rocket42` | `d073e3f08891308b72f03d199c8e4864` | — |
| C009 | dict+num | `silver77` | `36b1910eb34e65b3e34ec350fa886a1c` | — |
| C010 | dict+num | `thunder21` | `7672f5feceb0cb50026ee3bac37a1a89` | — |
| C011 | dict+symbol | `P@ssw0rd2024` | `6bac796099b08e175ac2b823b83f88e5` | — |
| C012 | dict+symbol | `Dr@gon!88` | `b696d6e03bf4f42b7456652a083e857c` | — |
| C013 | dict+symbol | `C0ffee@7` | `ab7a00369b8f0c02bc9259c3776758b1` | — |
| C014 | dict+symbol | `Monkey#123` | `f9cbc29194c5b5487a4676c4902bd154` | — |
| C015 | dict+symbol | `H0use!2021` | `25956f62f45e435c4a189a51bcc3588a` | — |
| C016 | dict+symbol | `Sh@dow99` | `6ece4895f81a1e6a32e2c613b0c8084b` | — |
| C017 | dict+symbol | `Summ3r!23` | `953ef1c1e42d561c884f7c00cafd047a` | — |
| C018 | dict+symbol | `Winter#2024` | `f8cd646de70c05a94e7103e3f7bfeb7e` | — |
| C019 | dict+symbol | `Rocket@21` | `74441ae03dea85174ffd7a28823c35d7` | — |
| C020 | dict+symbol | `Fl0wer!7` | `5f343694c724a960d5d0ecf82abac6fe` | — |
| C021 | pinyin+num | `woaini1314` | `0a2cb03c4dc29cfc0d56afa46ae8fd2e` | — |
| C022 | pinyin+num | `wodemima888` | `bc8c27b7ada32bf648f5190ac4e7e4ec` | — |
| C023 | pinyin+num | `zhongguo2023` | `fc95fc10fd8325f8cb6c253d6a1466f8` | — |
| C024 | pinyin+num | `beijing2008` | `f186c29a9f2d751877f8361867875276` | — |
| C025 | pinyin+num | `shanghai2020` | `dc04ab0e357690f2cac5eea3bfe5622e` | — |
| C026 | pinyin+num | `xuexi1234` | `2b41dad4c6325afe0b303a42fd660a1a` | — |
| C027 | pinyin+num | `gongzuo2024` | `468f0d56e990ce9bd1c886f39f046854` | — |
| C028 | pinyin+num | `jiating666` | `e2993232f98cb940d34d70b075a3d49f` | — |
| C029 | pinyin+num | `pengyou520` | `94b0873ca07760e6d1cb2677ec3dff05` | — |
| C030 | pinyin+num | `laoshi888` | `148beb5ba04257fcdda33e2b2e0d0453` | — |
| C031 | pii | `zhangsan1998` | `cc8a5274741a7522ff8ccbf0cf9a2a16` | PII（见下方） |
| C032 | pii | `Xiaoming_01` | `e90cd9e1382ab1d44153ddd8fad7b65d` | PII（见下方） |
| C033 | pii | `zhangsan0305` | `0685404576fa022506f982d65c810574` | PII（见下方） |
| C034 | pii | `Xiaoming1998` | `fd695ea1f9873ca62c53072a7d37d7af` | PII（见下方） |
| C035 | pii | `zhangsan7788` | `087812264bc22b3e555449cc9d62bd2d` | PII（见下方） |
| C036 | pii | `Zhangsan2024` | `eb081535a6bfa74b3c72c9cbcf9741c6` | PII（见下方） |
| C037 | pii | `xiaoming2001` | `3df0d6cc5c472e691c02db88fb8b00d0` | PII（见下方） |
| C038 | pii | `zhangsan_1998` | `2af7f52762d32cdd13ddeff48314cdc7` | PII（见下方） |
| C039 | pii | `Xiaoming#1998` | `b71fccd502d1223fd5d28097c67b317a` | PII（见下方） |
| C040 | pii | `zhangsan.work` | `b7425b97217ad0c5eeb42af0abe7e0d7` | PII（见下方） |
| C041 | reuse | `Sunshine2023#` | `763cf490b123208f36445d031bb04c8a` | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C042 | reuse | `Admin@2025` | `82f9fa29d86dae71395f7fc9ef23fe5f` | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C043 | reuse | `Hunter2!2024` | `ce6d10b64d0f01e73497d48a2f6a2dac` | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C044 | reuse | `Qwerty2024` | `6af6a334e946ab5123cd8760db378cfb` | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C045 | reuse | `sunshine2023!` | `8b38825245096c9c881fde167f63a10c` | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C046 | reuse | `Admin@2020!` | `507c0185a5131ca82acd3b648177b0cf` | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C047 | reuse | `hunter2!2018` | `d04bc991f49dd67a70333ac620aa1751` | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C048 | reuse | `Qwerty2019!` | `41595c9cb8dd139cbb7192c9f873afea` | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C049 | reuse | `Sunshine2019` | `48e5e313696b490eaa1bcab875b99ce2` | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C050 | reuse | `Admin2025` | `6a3904d3808c250d33105a2269f69b5b` | 历史口令：Sunshine2019!、Admin@2020、Hunter2!2018、Qwerty2019 |
| C051 | keyboard | `1qaz2wsx` | `1c63129ae9db9c60c3e8aa94d3e00495` | — |
| C052 | keyboard | `qazwsx123` | `708a9c84b47404c5524405e5cbd910b8` | — |
| C053 | keyboard | `zxcvbnm!` | `73d8fe14526809e658e7f3c70fab0437` | — |
| C054 | keyboard | `1q2w3e4r` | `5416d7cd6ef195a0f7622a9c56b55e84` | — |
| C055 | keyboard | `asdfghjkl1` | `2d3009bd0b21adfc6f5c18f2ba671a4c` | — |
| C056 | keyboard | `qwerty789` | `2cb42f8734ea607eefed3b70af13bbd3` | — |
| C057 | keyboard | `poiuytrewq` | `3805248410673a8be6aa4807e61fb5ae` | — |
| C058 | keyboard | `1qaz@wsx` | `e5d2a815230449badccf00bc67436696` | — |
| C059 | keyboard | `zxcasdqwe` | `c0b9963cca3816a39eff2a947a21cf0b` | — |
| C060 | keyboard | `qweasdzxc2` | `b636dcab2595fb7206d84af992fbe249` | — |
| C061 | phrase | `correct-horse` | `2eb62d171ba8491db496081a8b24738a` | — |
| C062 | phrase | `iloveyou2` | `d796b1242dc89cedffe596d14517f2ac` | — |
| C063 | phrase | `letmein123` | `4ca7c5c27c2314eecc71f67501abb724` | — |
| C064 | phrase | `mypassword2024` | `9573ba239ace4e682041a0a44612e2ed` | — |
| C065 | phrase | `trustno1` | `5fcfd41e547a12215b173ff47fdd3739` | — |
| C066 | phrase | `opensesame` | `e6078b9b1aac915d11b9fd59791030bf` | — |
| C067 | phrase | `helloworld88` | `c574b02756bd7dfce8a8d73726989e28` | — |
| C068 | phrase | `goodmorning7` | `b5443fb5df05b36b89d58fcf03b74bf7` | — |
| C069 | phrase | `ilovechina2020` | `196d1d1edf0af519186ddadeff96ec2a` | — |
| C070 | phrase | `welcometothejungle` | `a205423ba8f1b2279f63fea4d38aba60` | — |
| C071 | leet | `Tr0ub4dor&3` | `4ece57a61323b52ccffdbef021956754` | — |
| C072 | leet | `P@ssw0rd!` | `8a24367a1f46c141048752f2d5bbd14b` | — |
| C073 | leet | `Adm1n1str@tor` | `db61483ec39c16bedb45050fdb974e30` | — |
| C074 | leet | `S3cur1ty2024` | `b9283555b9c0257eec68f1a658931f90` | — |
| C075 | leet | `H@ck3r2020` | `996a3bcd85579d07778662ce432b97e1` | — |
| C076 | leet | `M@st3rP13ce` | `9c5fa466f2b8426f4af1b698f0baddaf` | — |
| C077 | leet | `D@rkN1ght99` | `7a331d0e28855d653fcffd4dcf47e363` | — |
| C078 | leet | `Bl@ckW1d0w` | `ccc41a1a549b5c80fd7a2ca70bbd5577` | — |
| C079 | leet | `Cyb3rPunk2077` | `13b462346de52763712fd003dcfc8791` | — |
| C080 | leet | `N3v3rG0nna` | `c95d0305afb9e6a173e97bc887282573` | — |
| C081 | cn+mixed | `密码123` | `d18902dd4cc8df459a9e769dc749888d` | PII（见下方） |
| C082 | cn+mixed | `我的密码2024` | `5415f9898d737d8d26f7a8c5d9bd1b3f` | PII（见下方） |
| C083 | cn+mixed | `张三@1998` | `e6dd351aa58b5cd4500fc3482ad37ce9` | PII（见下方） |
| C084 | cn+mixed | `生日快乐2024` | `e73bc8ac2423b8628f271c60dd631ed3` | PII（见下方） |
| C085 | cn+mixed | `woaini520!` | `7b3e5f0a23431f40c96f01fd5f90126b` | PII（见下方） |
| C086 | cn+mixed | `xiaoming_2001` | `69849cb1406c8d1e08a8edfbdd073767` | PII（见下方） |
| C087 | cn+mixed | `zhang@123` | `8526ef6792e08462d3c5dda704ac39f7` | PII（见下方） |
| C088 | cn+mixed | `123456abc` | `df10ef8509dc176d733d59549e7dbfaf` | PII（见下方） |
| C089 | cn+mixed | `a1b2c3d4` | `22c14f311a60486b36f79f3bc962be66` | PII（见下方） |
| C090 | cn+mixed | `password!@#2024` | `fc1424b79219ee68962ec4f6ae65660e` | PII（见下方） |
| C091 | hard(对照) | `X7#kL9$mQ2` | `1adbd1f56e40fa58a84a8cb7e7602474` | — |
| C092 | hard(对照) | `vN4$tR8*wZ` | `a29b7c7c96ae50a27e3919fe8372f8d8` | — |
| C093 | hard(对照) | `9f2K!pQ7#x` | `71f6db92a45451ed85074ba1f6cb3e7a` | — |
| C094 | hard(对照) | `m3L$vB8nQ1!` | `72ccb886915082bd098addbeced95a49` | — |
| C095 | hard(对照) | `Zx9!Rk2#Wt` | `acb5b2feada59f4aaa306582f3f4c493` | — |
| C096 | hard(对照) | `7uJ#pL4$xN` | `e45f13a0e1b4567f080c40b9a0f7c009` | — |
| C097 | hard(对照) | `Qw8$Zr3!Vm` | `98fc84d4b133890ffbcda03a99858f61` | — |
| C098 | hard(对照) | `Lk5#Bn9$Xy` | `7a7d845986f2c1224b7f54cb6d355eec` | — |
| C099 | hard(对照) | `Tf2!Wq7#Rp` | `f71029b5f381d6ffaa01247294156791` | — |
| C100 | hard(对照) | `8hM$xK4!Vd` | `a85b9d7a691f2811fde3b5e442fc6358` | — |

## 三、预期结果（本机实测，可用于对照）

### 30 条快速口径（C001–C030：dict+num / dict+symbol / pinyin+num）

| 指标 | 自适应（bandit + 切片） | 基线（固定顺序 + 不切片） |
| --- | --- | --- |
| 命中 | 25/30 | 25/30 |
| 命中来源策略 | S1 12、S7 5、S5 4、S3 3、S2 1（**S1 占 48%**） | S1 23、S2 1、S3 1（**S1 占 92%**） |
| 平均耗时 | 32.3s | 14.3s |
| 决策数（探索/利用） | 233（207/26） | 139（46/93） |
| 归档 | `docs/experiments/benchmark-100-20260919-1042-adaptive.*` | `docs/experiments/benchmark-100-20260919-1049-fixed-noslice.*` |

### 完整 100 条（main 上的 `79e0cbd`，规则链 best66 × d3ad0ne，时间预算 60s）

| 类别 | 命中 | 类别 | 命中 |
| --- | --- | --- | --- |
| dict+num | 10/10 | keyboard | 7/10 |
| pinyin+num | 10/10 | phrase | 7/10 |
| pii | 10/10 | cn+mixed | 6/10 |
| reuse | 9/10 | dict+symbol | 5/10 |
| | | leet | 1/10 |
| | | hard（对照） | 0/10 ✅ |

合计 **65/100**，命中来源 S1 51、S4 10、S2/S3/S5/S7 各 1~2；
归档：`docs/experiments/benchmark-100-20260919-0316.*`。

## 四、结果怎么读（复测时请按同一口径）

每个用例一行，关键列：

- `recovered` / `hit_strategy`：是否命中、由哪个策略命中（**复测重点：不应只有 S1**）；
- `tested`：本用例实际测试的候选总量；
- `stop_reason`：`all_targets_recovered`（命中即停）/ `candidates_exhausted`（候选耗尽）/
  `strategy_budgets`（各单元时间预算耗尽）；
- `native_units`：原生单元的**键空间**（S1 词表 × 规则链、S6 掩码、S7 词表 × 掩码）；
- `plan_arms`：Python 候选单元产出量；
- `reason`：未命中归因（Python 候选空间未覆盖 / 原生单元已实测仍未命中）；
- `run_status` / `run_message`：运行状态与 hashcat 失败原因（排查用）。

## 五、已知差异点（复测时容易踩）

1. **hashcat 的多个 `-r` 是规则链（乘积）**，不是并集：`best66 × d3ad0ne` 每个词
   225 万条规则，81 词 ≈ 1.8 亿键；规则表吃主机内存，三份以上大规则文件会失败。
2. **原生单元的每次启动有固定开销**（本机 5~9 秒），所以自适应切片的"探针 + 提交"
   需要足够的时间预算；30 秒预算下切片会挤掉覆盖，请用 120 秒（脚本默认）。
3. **无 GPU 时可先跑候选空间诊断**（不调用 hashcat，秒级）：
   `.\.venv\Scripts\python.exe scripts\benchmark_100.py --space-only`。
4. 复测结果请连 `--tag` 一起归档，便于和 `docs/experiments/` 里的历史结果对照。
