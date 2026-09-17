# 传统 PKZIP（ZipCrypto）接入记录

## 起因

用户上传的加密 ZIP 在分析阶段报错：

```
ANALYZE_FAILED: 当前 ZIP 适配器仅支持 WinZip AES（$zip2$），不支持传统 PKZIP
```

该文件（`data/uploads/FAC28983346D5.zip`，内含一张 jpg）由 zip2john 解析为
`$pkzip2$1*1*2*0*4bb2*51d9*9f1629fc*0*42*8*4bb2*9f16*5402*…`，即传统 PKZIP
（ZipCrypto）加密，而适配器此前只接受 WinZip AES 的 `$zip2$`。

## 模式判定怎么来的

hashcat 把传统 PKZIP 拆成 5 个模式，先用 `hashcat --identify` 确认结构匹配，
再逐个模式实测"能否真正破解"（样例哈希取自 `hashcat --example-hashes`，口令 `hashcat`）：

| 运行模式 | 可破解的样例 | 被拒绝的样例 |
| --- | --- | --- |
| 17200 PKZIP (Compressed) | 17200 | 17210、17220、17225、17230 |
| 17210 PKZIP (Uncompressed) | 17210 | 17200、17220、17225、17230、真实文件 |
| 17220 PKZIP (Compressed Multi-File) | 17200、17220 | 17210、17225 |
| **17225 PKZIP (Mixed Multi-File)** | 17200、17210、17220、17225 | 无 |
| 17230 Mixed Multi-File Checksum-Only | 17220、17225、17230 | 17200、17210、真实文件 |

结论：**17225 通吃单文件压缩/未压缩与多文件压缩/混合**，只有"仅含 16 字节校验和"的
变体必须用 17230。`--identify` 只做结构匹配（对同一个哈希会同时报出 17200/17220/17225），
不能作为可用性依据，因此以实测兼容矩阵为准。

## 实现

`zip_adapter.py`：

- 新增 `PKZIP_PATTERN` 提取 `$pkzip2$…*$/pkzip2$`；
- `pkzip2_hashcat_mode()` 由哈希头判定模式：`data_type == 2`（单文件）取压缩方式字段
  （8 → 17200、0 → 17210，未知方式回退 17200 交给 hashcat 报错）；多文件时若所有长十六进制
  字段长度恰为 32 则判为"仅校验和" → 17230，否则 → 17225；
- `_resolve_pkzip2()` 为一个压缩包选定唯一模式（hashcat 单次运行只能用一个模式），
  多数派胜出，被排除的目标数量写入 `warnings`；
- `ExtractedZipTarget` 增加 `algorithm` 与 `warnings`；WinZip AES 仍为 `zip-aes`，
  传统 PKZIP 为 `zip-legacy`；
- 未加密压缩包给出"该 ZIP 未加密，无需口令评测"，不再与"未提取到目标"混淆。

`targets.py` 的 `ZipTargetExtractor` 透传提取器给出的算法名与警告；
`hashcat_adapter.py` 增加 `zip-legacy`/`zipcrypto`/`pkzip2` 等别名（回退 17225）与
17200/17210/17230 的显式名称。

## 验证

- 单元测试 `tests/test_zip_pkzip_modes.py`：五类结构 + 未知压缩方式 + 混合包警告；
- 适配器测试 `tests/test_adapters_week2.py`：单文件压缩/未压缩、多文件、仅校验和四种仿真输出；
- 端到端 `tests/test_week2_real_execution.py::test_real_execution_zip_legacy_pkzip_recovery`：
  分析得 `algorithm=zip-legacy` → 真实执行 → 断言传给 hashcat 的是 `--hash-type 17200` → 命中；
- 真实工具复测：把用户上传的 `FAC28983346D5.zip` 重新上传后走完整链路，
  `PRIR.algorithm=zip-legacy`、`verification_cost=medium`，真实运行 19 秒测试 1969 个候选
  （口令不在候选空间内故未命中，属预期）；
- 全量后端测试 625 项通过。

## 边界

- RAR、7z 等其他压缩格式仍未接入；
- 传统 PKZIP 的"仅校验和"变体（17230）无法还原明文，只能验证口令正确性；
- 同一压缩包内出现多种模式时只执行多数模式，其余目标本次不纳入（已在 `warnings` 说明）。
