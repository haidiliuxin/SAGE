# B 补齐项：成本标定器与盐条件模型

本次完成原分工中的成本标定标准输出、近期吞吐、分画像测量与导入，以及四种盐条件的显式建模。代码已接入回放与 UCB 成本预测；真实标定结果需由使用方运行产生，本次未执行真实工具或测试。

## 1. 标定组件与标准输出

`decision/costs.py` 提供 `CostEstimate`；`decision/calibration.py` 提供 `VerificationProfile`、`CostCalibrator`、标定报告校验与回放成本转换。

```text
CostEstimate(
    startup_cost, seconds_per_candidate, estimated_batch_time, confidence,
    complete_samples, censored_samples, recent_sample_count,
    recent_throughput, last_batch_throughput, identifiable, extrapolated,
    confidence_kind="heuristic_quality_not_probability"
)
```

单位分别为秒、秒/候选、秒、无量纲分数和候选/秒。候选吞吐表示一个候选对整个目标组的完成速度，不是 Hashcat 的底层哈希运算次数/秒。

模型保留全历史充分统计量和最近 20 批反馈。预测优先用窗口内完整样本拟合非负 `a + k × batch_size`，从而适应近期速度变化；窗口内没有完整样本时回退历史拟合或冷启动先验，并将 confidence 置零。近期吞吐按窗口内可确定 tested 且耗时为正的样本计算 `sum(tested)/sum(duration)`，不是简单平均各批速度。未知 tested 不冒充零；零耗时吞吐为 null。

`confidence` 是项目定义的质量提示，不是概率、置信区间或理论覆盖率。计算考虑完整样本数（10 批封顶）、窗口完整样本占比、拟合相对绝对误差、是否有不同批量以及是否外推。同批量或外推各折半；缺少正耗时完整样本时为零。UCB 的收益探索项仍独立计算，不把这项质量分数当成统计置信界。

全历史 `coefficients()/predict()` 保留原接口；新增 `estimate()` 使用上述近期拟合并输出完整结构。UCB 已调用 estimate，日志增加 `cost_confidence / recent_throughput / last_batch_throughput`。模型快照保存窗口与统计；旧成本模型快照缺窗口时可读取，先回退历史，再逐批填充窗口。

启动项是从多种批量的完整进程耗时中拟合的有效固定开销，包含现有适配器计时范围内的临时文件、进程初始化等，并非单独计量的 GPU 内核启动时间。只测同一批量时不能分别识别启动项与斜率，`identifiable=false`；这种情况下使用受观察均值约束的启动先验。

## 2. 算法参数、多目标与设备隔离

每组标定有独立 VerificationProfile：

- algorithm、hash_mode；
- parameters：iterations、memory_kib、parallelism、cost、n/r/p、version 等数值；
- target_count；
- salts.mode、salts.group_sizes；
- device、engine_version：不敏感的环境标识。

profile 的规范化哈希用于区分模型。目标数量、盐条件、设备、版本或参数不同，不能把样本混成同一个模型。报告导入回放时必须完整匹配。CLI fit 可从一个 JSONL 文件中自动分组，分别输出多个画像的模型。

这些字段是实验者对输入的声明：代码校验目标数量与分组总量，不解析任意 Hashcat 格式来验证盐值和参数。准备目标时必须保证声明与目标文件相符；device/engine_version 是标识，不负责选择设备或检测版本。测量使用 Hashcat 默认设备选择；需要固定设备时在受控的 Hashcat 命令封装中指定，并使画像标识保持一致。

## 3. 四种盐条件

| mode | 例：4 个目标的 group_sizes | 语义 |
|---|---|---|
| unsalted | `[4]` | 一个无盐计算组 |
| shared | `[4]` | 全部目标共用一个盐 |
| grouped | `[2,2]` 或 `[1,3]` | 多个盐组，部分目标共用 |
| independent | `[1,1,1,1]` | 每个目标独立盐 |

组大小必须为正整数、总和等于目标数；四种模式各有结构校验。仅保存分组数量，不记录实际盐或目标哈希。排序规范化让 `[1,3]` 与 `[3,1]` 的成本画像相同。

回放支持两种互斥的成本来源：

1. **whole_target_group**：`ReplayCost` 或导入标定结果已经表示整个目标组每候选耗时，不能再乘盐数。
2. **synthetic_salt_groups**：显式指定 `SaltCostModel`，按以下实验近似计算：

```text
每候选耗时 = 盐计算组数 × group_hash_seconds
           + 目标总数 × compare_seconds
整批耗时 = startup_cost + 批量 × 每候选耗时
```

无盐与共用盐在同样的人工每组参数下可以有相同成本，代码没有“只要加盐就固定变慢”的规则。`group_hash_seconds` 可针对不同算法参数另设，也可改用实测整组成本。线性盐组模型不声称精确复现 GPU 并行效率；整个回放保持初始目标组的固定验证成本，不模拟恢复某盐组后 Hashcat 减少该组计算的优化。

关于盐分组、速度和 progress 的解释参考 [Hashcat FAQ](https://hashcat.net/wiki/doku.php?id=frequently_asked_questions)及[机器可读状态说明](https://hashcat.net/wiki/doku.php?id=machine_readable)。仅借鉴计量语义，没有复制源码。滑动拟合、质量分数和上述线性仿真式是项目实现。

## 4. 不调用 Hashcat 的完整示例

先用带 `source=synthetic` 的示例样本检查标定→导入→回放流程：

```powershell
python -m sage_pass.experiments.calibrate fit --input examples/calibration/synthetic-measurements.jsonl --output data/calibration/example-fit.json --batch-size 20
python -m sage_pass.experiments --scenario examples/replay/salts-unsalted.json --calibration data/calibration/example-fit.json --output data/replay/calibrated-example.json
```

这些样本不是设备测量值。报告保留 `measurement_sources`，区分 synthetic、measured 和 imported。

四种盐条件分别运行（全部五种策略）：

```powershell
foreach ($saltMode in @("unsalted", "shared", "grouped", "independent")) {
    python -m sage_pass.experiments --scenario "examples/replay/salts-$saltMode.json" --output "data/replay/salts-$saltMode.json"
}
```

四个样例的匹配关系、候选流和预算相同，只改变盐组结构。其 hash_mode 字段是合成场景占位；不能把加盐样例的画像当成真实 Hashcat 格式配置。

`--calibration` 选择与 scenario.verification_profile 匹配的模型，替换环境耗时参数并清除人工盐组成本公式；含 per-arm cost 覆盖的场景会拒绝导入，避免不清楚哪一套参数生效。无完整样本、拟合斜率不为正、模型与报告估计不一致时也拒绝导入。

导入标定只设置回放环境成本，不把环境隐藏参数自动传给策略。若预实验允许“已标定冷启动”，可以将独立标定的 startup_cost、seconds_per_candidate 写入 UCB 配置中的对应 prior，再通过 `--ucb-config` 使用；所有对照策略应遵循相同实验信息约定。

## 5. 在你的环境运行真实标定

提供了 4 个公开常见 MD5 测试哈希和 512 条合成、不命中的候选，供链路演示。先把 profile 文件的设备标识、工具版本修改为你的实际实验标识，再运行：

```powershell
python -m sage_pass.experiments.calibrate measure --profile examples/calibration/profile-md5.json --targets examples/calibration/md5-targets.txt --candidates examples/calibration/candidates.txt --batch-sizes 32 128 512 --repeats 3 --timeout 30 --output data/calibration/md5-measured.jsonl
python -m sage_pass.experiments.calibrate fit --input data/calibration/md5-measured.jsonl --output data/calibration/md5-fit.json --batch-size 128
```

`--hashcat` 可指定可执行文件路径。输出采用新文件独占创建，拒绝覆盖旧测量。每批完成后立即 flush；中途中断会保留之前的样本，可继续用 fit 分析。measure 会实际启动 Hashcat，本次由用户运行。启动或等待失败会报错并保留已有文件，不制造失败批次的虚假完成时间。

仅 `status=completed`、exit_code=1（候选耗尽）、未恢复目标且耗时小于时限的批次进入拟合，完整 tested 取实际提交量；提前恢复、超时或工具失败保留耗时，tested/throughput 为 null。如此避免直接把多盐 progress 当成完成候选数。真实执行的 UCB 在线拟合也采用这一保守判断，不将不确定反馈用于吞吐估计。

对快速 Hash，512 条往往只适合检查链路，启动开销可能占主导。正式测量应增加批量并重复多轮，固定设备负载；对 bcrypt/Argon2、不同目标数和盐组分别准备真实输入与画像。候选不能命中目标，否则目标组会变化，样本将排除。此处不测 GPU 利用率/显存，也不自动生成所有加密格式的标定目标。

## 6. 验收与兼容性

本次未运行测试、回放或真实标定，仅做静态检查。用户运行：

```powershell
python -m pytest tests/test_calibration_salts.py tests/test_ucb_cost.py tests/test_reward_logging.py tests/test_real_research_logging.py tests/test_decision_replay.py
python -m pytest tests/test_scheduler.py tests/test_a_week1_core.py tests/test_week3_recovery.py
```

新增用例覆盖标准估计、吞吐、窗口速度变化、缺测/零耗时、画像隔离、盐组校验、四条件回放、研究日志、恢复一致、标定拟合与导入，以及假的 Hashcat 反馈。测试替身不调用真实 Hashcat。

回放检查点升级为 **v3**，因评分、近期成本状态和画像字段变化，旧 v2 检查点需重新生成。真实 UCB 的完整模型恢复支持旧缺窗口结构，保留其历史拟合，后续重新积累窗口；旧研究事件原样保留。普通真实运行未提供盐画像时日志标为 null，不推断为无盐；完整画像由标定入口和显式配置的回放提供。
