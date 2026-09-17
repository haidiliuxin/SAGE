# B 第五步交接

已实现 UCB1 公式基线、在线非负启动项/单候选耗时拟合、预测成本比值型 Cost-aware UCB，并接入真实执行、五算法回放、脱敏日志和完整快照恢复。

[方法与借鉴来源](../decision/ucb-and-cost.md)逐项说明原论文公式、SMPyBandits 的参考约定、本项目修改、配置与局限。没有复制第三方仓库源码或增加依赖。

## 验证状态

更新：用户已确认本步测试无问题，并成功运行五算法对照。后续补齐成本标定与盐条件的版本需重新验收，见 [补齐项交接](b-calibration-salts.md)。以下保留初次交付记录。

用户已报告第四步两组验收 **55 + 24 = 79 项通过**。第五步没有执行 Python、pytest 或真实 Hashcat；已有通过记录不代表本次新算法已验证。新增测试等待用户在 `sage` 环境执行：

```powershell
python -m pytest tests/test_ucb_cost.py tests/test_reward_logging.py tests/test_real_research_logging.py tests/test_decision_replay.py
python -m pytest tests/test_scheduler.py tests/test_a_week1_core.py tests/test_week3_recovery.py
```

新用例覆盖：手算 UCB 评分、冷启动和同分规则、等收益下成本选择、变批量拟合、同批量不可辨识处理、非负拟合、部分/失败样本排除、零耗时、预算裁剪、真实超时记账、多目标去重、完整快照恢复、坏快照不修改活动状态、回放续跑一致及日志字段。

原验收中“UCB 尚未实现”的两处期望改为“Thompson 尚未实现”；其余原回归断言保留。CLI 的 all 扩展为五算法，库函数默认仍保留三基线。

## 交给 A/C

- A 可通过 `SAGE_SCHEDULER_TYPE=ucb/cost_aware_ucb` 使用真实调度；页面接入和公共参数接口仍由 A 负责。
- 消费 UCB 日志时用 `learning_reward` 解释学习、用 `reward` 比较统一评价；两者不可混用。
- 同步恢复必须保留新增 `scheduler_snapshot`，不能只复制旧统计摘要。
- C 的生成器无需依赖 UCB，只需继续输出候选批次。原有真实生成去重归属、Hashcat progress 单位与回放之间的对齐要求继续适用。
- 本版不学习批量大小，不估计成本置信区间，不声称理论遗憾保证或性能提升；先做固定输入对照实验。
