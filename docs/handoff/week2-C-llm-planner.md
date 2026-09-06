# 第 2 周丙：LLM Planner 第一阶段交接

## 已实现

- 保持统一 `Planner.plan(PRIR) -> StrategyPlan` 接口。
- 支持 OpenAI Responses API 与 OpenAI 兼容 Chat Completions API（硅基流动），只发送结构化 PRIR，不发送目标 Hash、文件内容或上下文原文。
- 严格 JSON Schema 输出；服务端再次使用 Pydantic、策略唯一性、S4 上下文条件、优先级连续性及时间/候选总预算校验。
- 温度、请求超时、最大输出 token 均可通过环境变量限制。
- 以“提示版本 + 模型 + 规范化 Task Profile”为键的进程内 TTL/LRU 缓存；缓存结果会重新绑定当前 `task_id`。
- API、网络、JSON 或约束异常时降级至现有 Mock Planner，错误详情不进入对外响应。
- `/plan` 与 `/execute` 使用应用级同一个 Planner，因此执行阶段可命中规划缓存。
- `PolicyValidator` 在计划进入执行链路前检查 S1～S4 白名单、目标适用性、策略唯一性、连续优先级、正预算、时间/候选总预算和参数范围；LLM 计划异常时降级为 Rule 计划。
- `RulePlanner` 支持独立 `rule` 模式，并作为 LLM Planner 的首级降级路径；无上下文使用 S1/S2/S3，有上下文增加 S4，慢 Hash 优先高概率策略并将候选池限制为任务上限的 25%。

## 配置

```text
SAGE_PLANNER_TYPE=llm
OPENAI_API_KEY=...
# OPENAI_BASE_URL=...
SAGE_LLM_API_STYLE=responses
SAGE_LLM_MODEL=gpt-4.1-mini
SAGE_LLM_TEMPERATURE=0.1
SAGE_LLM_TIMEOUT_SECONDS=15
SAGE_LLM_MAX_OUTPUT_TOKENS=700
SAGE_LLM_CACHE_TTL_SECONDS=900
SAGE_LLM_CACHE_MAX_ENTRIES=256
```

硅基流动配置为 `OPENAI_BASE_URL=https://api.siliconflow.cn/v1`、`SAGE_LLM_API_STYLE=chat_completions`，模型可使用 `deepseek-ai/DeepSeek-V4-Flash`。Chat Completions 响应通过 `response_format.json_schema` 约束，并继续进入相同的 Pydantic 与 Policy Validator 校验链路。

`SAGE_PLANNER_TYPE` 支持 `mock`、`rule`、`llm`。选择 `llm` 但没有 API Key 时直接使用 Rule Planner；LLM 异常先降级至 Rule Planner，规则目标不受支持时再降级至 Mock。

## 当前边界

LLM 的 `parameters` 在当前生成协议中仍保留为空对象，避免提前依赖尚未稳定的候选生成器参数；Validator 已预先定义并测试参数白名单和范围，可在乙方接口稳定后直接开放给 LLM Schema。LLM 异常时先降级到注入的 Rule Planner，Rule 无法处理（如 `unknown` 目标）时再降级到 Mock Planner。
