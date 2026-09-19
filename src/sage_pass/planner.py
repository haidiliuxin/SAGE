from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings
from .enums import (
    LLMApiStyle,
    PlannerType,
    StrategyId,
    TargetType,
    TaskStatus,
    VerificationCost,
)
from .keyspace import (
    file_line_count,
    mask_keyspace,
    masks_keyspace,
    rule_chain_count,
    select_masks_within,
)
from .policy import PolicyValidationError, PolicyValidator
from .schemas import PRIR, StrategyItem, StrategyPlan
from .transfer import KnowledgeSummary


PROMPT_VERSION = "week3-generator-taxonomy-v1"
STRATEGY_NAMES = {
    StrategyId.S1: "Baseline",
    StrategyId.S2: "Rule",
    StrategyId.S3: "Statistical Model",
    StrategyId.S4: "Personalized",
    StrategyId.S5: "Transfer",
    StrategyId.S6: "Mask / Brute",
    StrategyId.S7: "Hybrid",
}


class PlannerGateway(Protocol):
    """Small boundary around an LLM provider, making the planner testable."""

    def create_plan(self, task_profile: dict[str, Any]) -> str: ...


class _LLMStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: StrategyId
    priority: int = Field(ge=1, le=5)
    time_budget: int = Field(ge=0)
    candidate_budget: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=200)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def parameters_are_reserved(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value:
            raise ValueError("当前规划协议的 parameters 必须为空")
        return value


class _LLMPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategies: list[_LLMStrategy] = Field(min_length=1, max_length=5)
    warnings: list[str] = Field(default_factory=list, max_length=8)


LLM_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "strategies": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "strategy_id": {
                        "type": "string",
                        "enum": ["S1", "S2", "S3", "S4", "S5"],
                    },
                    "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                    "time_budget": {"type": "integer", "minimum": 0},
                    "candidate_budget": {"type": "integer", "minimum": 0},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 200},
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "strategy_id",
                    "priority",
                    "time_budget",
                    "candidate_budget",
                    "reason",
                    "parameters",
                ],
            },
        },
        "warnings": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
        },
    },
    "required": ["strategies", "warnings"],
}


PLANNER_INSTRUCTIONS = """You are the strategy selector for an authorized offline
password security assessment. You receive only a structured Task Profile (PRIR).
Choose and budget strategies from S1 Baseline, S2 Rule, S3 Statistical Model,
S4 Personalized, and S5 Transfer. S3 may be backed by PCFG, Markov, or a future
PassLLM generator. S4 may be backed by Context, History, or Hybrid. Use S4 only
when context_available is true and use S5
only when feedback_summary.available is true. The feedback summary contains only
aggregate abstractions; never request or generate recovered plaintext. Low verification cost permits
broader candidate sets; high verification cost should favor smaller, higher
probability sets. Each strategy may appear at most once. Priorities must be unique
and contiguous starting at 1. The sum of time_budget and candidate_budget must not
exceed the profile budgets. Do not reproduce, infer, or request target secrets.
The parameters object is reserved for the later policy module and must be empty.
Return only the requested structured output."""


class OpenAIResponsesGateway:
    """OpenAI Responses API adapter using strict Structured Outputs."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float,
        timeout_seconds: float,
        max_output_tokens: int,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - deployment guard
                raise RuntimeError("启用 LLM Planner 需要安装 openai 依赖") from exc
            options: dict[str, Any] = {
                "api_key": api_key,
                "timeout": timeout_seconds,
                "max_retries": 0,
            }
            if base_url:
                options["base_url"] = base_url
            client = OpenAI(**options)
        self._client = client
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

    def create_plan(self, task_profile: dict[str, Any]) -> str:
        response = self._client.responses.create(
            model=self._model,
            instructions=PLANNER_INSTRUCTIONS,
            input=json.dumps(task_profile, ensure_ascii=False, sort_keys=True),
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "sage_strategy_plan",
                    "strict": True,
                    "schema": LLM_PLAN_JSON_SCHEMA,
                }
            },
            store=False,
        )
        output_text = getattr(response, "output_text", None)
        if not output_text:
            raise ValueError("LLM 未返回规划 JSON")
        return output_text


class OpenAIChatCompletionsGateway:
    """OpenAI-compatible Chat Completions adapter for SiliconFlow et al."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float,
        timeout_seconds: float,
        max_output_tokens: int,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - deployment guard
                raise RuntimeError("启用 LLM Planner 需要安装 openai 依赖") from exc
            options: dict[str, Any] = {
                "api_key": api_key,
                "timeout": timeout_seconds,
                "max_retries": 0,
            }
            if base_url:
                options["base_url"] = base_url
            client = OpenAI(**options)
        self._client = client
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

    def create_plan(self, task_profile: dict[str, Any]) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": PLANNER_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": json.dumps(
                        task_profile, ensure_ascii=False, sort_keys=True
                    ),
                },
            ],
            temperature=self._temperature,
            max_tokens=self._max_output_tokens,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "sage_strategy_plan",
                    "strict": True,
                    "schema": LLM_PLAN_JSON_SCHEMA,
                },
            },
            stream=False,
        )
        choices = getattr(response, "choices", None)
        if not choices:
            raise ValueError("LLM 未返回规划结果")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM 未返回规划 JSON")
        return content


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    payload: dict[str, Any]


class LLMPlanner:
    """PRIR-only planner with strict parsing, bounded cache and safe fallback."""

    def __init__(
        self,
        gateway: PlannerGateway,
        *,
        model: str,
        fallback: MockPlanner | None = None,
        cache_ttl_seconds: int = 900,
        cache_max_entries: int = 256,
        validator: PolicyValidator | None = None,
        clock=time.monotonic,
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.fallback = fallback or MockPlanner()
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_max_entries = cache_max_entries
        self.validator = validator or PolicyValidator()
        self._clock = clock
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

    def plan(
        self,
        prir: PRIR,
        knowledge_summary: KnowledgeSummary | None = None,
    ) -> StrategyPlan:
        profile = _task_profile(prir, knowledge_summary)
        key = _cache_key(profile, self.model)
        try:
            cached = self._cache_get(key)
            if cached is not None:
                cached_plan = _build_strategy_plan(
                    prir, _LLMPlan.model_validate(cached)
                )
                return self.validator.validate(prir, cached_plan, knowledge_summary)
            raw = self.gateway.create_plan(profile)
            proposal = _LLMPlan.model_validate_json(raw)
            plan = _build_strategy_plan(prir, proposal)
            plan = self.validator.validate(prir, plan, knowledge_summary)
        except Exception as exc:
            fallback = self.fallback.plan(prir, knowledge_summary)
            fallback_name = {
                PlannerType.MOCK: "Mock",
                PlannerType.RULE: "Rule",
            }.get(fallback.planner_type, fallback.planner_type.value)
            fallback.warnings.append(
                f"LLM Planner 不可用，已降级为 {fallback_name} 计划"
                f"（{type(exc).__name__}）"
            )
            return fallback

        self._cache_put(key, proposal.model_dump(mode="json"))
        return plan

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        now = self._clock()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return deepcopy(entry.payload)

    def _cache_put(self, key: str, payload: dict[str, Any]) -> None:
        if self.cache_max_entries <= 0 or self.cache_ttl_seconds <= 0:
            return
        with self._lock:
            self._cache[key] = _CacheEntry(
                expires_at=self._clock() + self.cache_ttl_seconds,
                payload=deepcopy(payload),
            )
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_max_entries:
                self._cache.popitem(last=False)


class MockPlanner:
    def plan(
        self,
        prir: PRIR,
        knowledge_summary: KnowledgeSummary | None = None,
    ) -> StrategyPlan:
        del knowledge_summary
        baseline = _baseline_strategy(prir)
        strategies = [baseline]
        warnings: list[str] = []
        if prir.context_available:
            remaining_time = prir.time_budget - baseline.time_budget
            remaining_candidates = prir.candidate_budget - baseline.candidate_budget
            if remaining_time >= 1 and remaining_candidates >= 1:
                strategies.append(
                    _context_strategy(
                        prir,
                        max_time_budget=remaining_time,
                        max_candidate_budget=remaining_candidates,
                    )
                )
            else:
                warnings.append("上下文策略因任务预算不足未加入计划")
        return StrategyPlan(
            task_id=prir.task_id,
            planner_type=PlannerType.MOCK,
            total_time_budget=prir.time_budget,
            strategies=strategies,
            status=TaskStatus.PLANNED,
            warnings=warnings,
        )


RULE_ORDERS: dict[VerificationCost, tuple[StrategyId, ...]] = {
    VerificationCost.LOW: (StrategyId.S1, StrategyId.S2, StrategyId.S3),
    VerificationCost.MEDIUM: (StrategyId.S1, StrategyId.S2, StrategyId.S3),
    VerificationCost.HIGH: (StrategyId.S1, StrategyId.S2, StrategyId.S3),
    VerificationCost.UNKNOWN: (StrategyId.S1, StrategyId.S2, StrategyId.S3),
}

RULE_WEIGHTS: dict[VerificationCost, dict[StrategyId, float]] = {
    VerificationCost.LOW: {
        StrategyId.S1: 0.20,
        StrategyId.S2: 0.30,
        StrategyId.S3: 0.30,
        StrategyId.S4: 0.20,
        StrategyId.S5: 0.20,
        StrategyId.S6: 0.1,
        StrategyId.S7: 0.1,
    },
    VerificationCost.MEDIUM: {
        StrategyId.S1: 0.30,
        StrategyId.S2: 0.30,
        StrategyId.S3: 0.20,
        StrategyId.S4: 0.20,
        StrategyId.S5: 0.20,
        StrategyId.S6: 0.1,
        StrategyId.S7: 0.1,
    },
    VerificationCost.HIGH: {
        StrategyId.S1: 0.45,
        StrategyId.S2: 0.17,
        StrategyId.S3: 0.08,
        StrategyId.S4: 0.30,
        StrategyId.S5: 0.30,
        StrategyId.S6: 0.08,
        StrategyId.S7: 0.08,
    },
    VerificationCost.UNKNOWN: {
        StrategyId.S1: 0.35,
        StrategyId.S2: 0.25,
        StrategyId.S3: 0.20,
        StrategyId.S4: 0.20,
        StrategyId.S5: 0.20,
    },
}

CANDIDATE_USAGE_RATIO = {
    VerificationCost.LOW: 1.0,
    VerificationCost.MEDIUM: 0.6,
    VerificationCost.HIGH: 0.25,
    VerificationCost.UNKNOWN: 0.5,
}

STRATEGY_REASONS = {
    StrategyId.S1: "优先测试命中率最高的基线口令",
    StrategyId.S2: "对基线词进行常见大小写、数字、年份、替换和符号变换",
    StrategyId.S3: "使用已配置的统计模型按概率或分数扩展候选覆盖",
    StrategyId.S4: "利用当前用户授权的个人信息、旧口令及其抽象结构生成个性化候选",
    StrategyId.S5: "将跨任务抽象结构模式应用于当前任务授权种子",
    StrategyId.S6: "按字符集与长度递增枚举掩码，覆盖字典之外的组合",
    StrategyId.S7: "词表与掩码混合，覆盖词根加短后缀的组合",
}


@dataclass(frozen=True, slots=True)
class NativeAttackSettings:
    """原生攻击（hashcat 自己枚举候选）的配置。

    - rules_path：规则文件，S2 以 `-r` 运行词表种子；
    - mask_ladder：掩码阶梯，每个掩码一次 `-a 3` 原生批次；
    - hybrid_masks：词表 + 掩码的混合攻击掩码（`-a 6`）。
    """

    rules_path: str | None = None
    rule_paths: tuple[str, ...] = ()
    mask_ladder: tuple[str, ...] = ()
    hybrid_masks: tuple[str, ...] = ()
    # 外部词表：原生词表攻击（S1 词表 × 规则、S7 词表 × 掩码）按它估算键空间。
    wordlist_path: str | None = None

    @property
    def active_rule_paths(self) -> tuple[str, ...]:
        """生效的规则文件：优先多路配置，回退单个 rules_path。"""
        if self.rule_paths:
            return self.rule_paths
        return (self.rules_path,) if self.rules_path else ()

    @property
    def wordlist_lines(self) -> int:
        if not self.wordlist_path:
            return 0
        return file_line_count(self.wordlist_path)

    @property
    def rule_count(self) -> int:
        """规则链放大倍数（多个规则文件相乘，与 hashcat `-r a -r b` 一致）。"""
        return rule_chain_count(self.active_rule_paths)

    @property
    def s1_keyspace(self) -> int | None:
        """S1 原生词表（× 规则）一次批次测试的候选数。"""
        lines = self.wordlist_lines
        if lines <= 0:
            return None
        return lines * max(1, self.rule_count)

    @property
    def s6_keyspace(self) -> int | None:
        """S6 掩码阶梯的总键空间。"""
        return masks_keyspace(self.mask_ladder) if self.mask_ladder else None

    @property
    def s7_keyspace(self) -> int | None:
        """S7 词表 × 掩码的总键空间。"""
        if not self.hybrid_masks:
            return None
        lines = self.wordlist_lines
        if lines <= 0:
            return None
        factor = masks_keyspace(self.hybrid_masks)
        if factor is None:
            return None
        return lines * factor

    @property
    def enabled(self) -> bool:
        return bool(self.active_rule_paths or self.mask_ladder or self.hybrid_masks)


class RulePlanner:
    """Deterministic planner used without an LLM and as the LLM fallback."""

    def __init__(
        self,
        *,
        validator: PolicyValidator | None = None,
        fallback: MockPlanner | None = None,
        native_attacks: NativeAttackSettings | None = None,
    ) -> None:
        self.validator = validator or PolicyValidator()
        self.fallback = fallback or MockPlanner()
        self.native_attacks = native_attacks or NativeAttackSettings()

    def plan(
        self,
        prir: PRIR,
        knowledge_summary: KnowledgeSummary | None = None,
    ) -> StrategyPlan:
        if prir.target_type == TargetType.UNKNOWN:
            return self._fallback(prir, "规则规划器不支持 unknown 目标")

        order = list(RULE_ORDERS[prir.verification_cost])
        if prir.context_available:
            if prir.verification_cost == VerificationCost.HIGH:
                order.insert(1, StrategyId.S4)
            else:
                order.append(StrategyId.S4)
        if knowledge_summary and knowledge_summary.available:
            order.insert(1, StrategyId.S5)
        # 原生攻击：掩码阶梯与混合掩码按需追加为独立调度单元（候选由 hashcat 枚举）。
        if self.native_attacks.mask_ladder:
            order.append(StrategyId.S6)
        if self.native_attacks.hybrid_masks:
            order.append(StrategyId.S7)

        candidate_limit = max(
            1,
            int(
                prir.candidate_budget
                * CANDIDATE_USAGE_RATIO[prir.verification_cost]
            ),
        )
        count = min(len(order), prir.time_budget, candidate_limit)
        selected = order[:count]
        weights = RULE_WEIGHTS[prir.verification_cost]
        time_allocations = _allocate_budget(prir.time_budget, selected, weights)
        candidate_allocations = _allocate_budget(
            candidate_limit, selected, weights
        )
        if StrategyId.S5 in candidate_allocations and knowledge_summary is not None:
            candidate_allocations[StrategyId.S5] = min(
                candidate_allocations[StrategyId.S5],
                max(1, knowledge_summary.suggested_s5_max_budget),
            )
        # 原生攻击（S1 词表×规则 / S6 掩码 / S7 词表×掩码）的候选由 hashcat 自己枚举，
        # 必须按键空间给它们分配预算，否则实测候选数会超出分配预算，破坏调度不变量。
        native_masks, native_rules, native_warnings = _fit_native_units(
            selected, candidate_allocations, candidate_limit, self.native_attacks,
            weights,
        )
        strategies = [
            StrategyItem(
                strategy_id=strategy_id,
                strategy_name=STRATEGY_NAMES[strategy_id],
                priority=index,
                time_budget=time_allocations[strategy_id],
                candidate_budget=candidate_allocations[strategy_id],
                reason=STRATEGY_REASONS[strategy_id],
                parameters=_rule_parameters(
                    strategy_id,
                    prir.verification_cost,
                    candidate_allocations[strategy_id],
                    native=self.native_attacks,
                    native_masks=native_masks,
                    native_rules=native_rules,
                ),
            )
            for index, strategy_id in enumerate(selected, start=1)
        ]
        warnings: list[str] = list(native_warnings)
        if count < len(order):
            skipped = ", ".join(item.value for item in order[count:])
            warnings.append(f"任务预算过小，未加入策略：{skipped}")
        if prir.verification_cost == VerificationCost.HIGH:
            warnings.append(
                "慢 Hash 采用高概率小候选集："
                f"计划使用 {candidate_limit}/{prir.candidate_budget} 个候选"
            )
        plan = StrategyPlan(
            task_id=prir.task_id,
            planner_type=PlannerType.RULE,
            total_time_budget=prir.time_budget,
            strategies=strategies,
            status=TaskStatus.PLANNED,
            warnings=warnings,
        )
        try:
            return self.validator.validate(prir, plan, knowledge_summary)
        except PolicyValidationError as exc:
            return self._fallback(
                prir, f"规则计划未通过策略校验（{type(exc).__name__}）"
            )

    def _fallback(self, prir: PRIR, warning: str) -> StrategyPlan:
        plan = self.fallback.plan(prir)
        plan.warnings.append(f"{warning}，已降级为 Mock 计划")
        return plan


def build_planner(settings: Settings) -> MockPlanner | RulePlanner | LLMPlanner:
    if settings.planner_type == PlannerType.MOCK:
        return MockPlanner()
    rule_planner = RulePlanner(
        native_attacks=NativeAttackSettings(
            rules_path=(
                str(settings.rules_path) if settings.rules_path is not None else None
            ),
            rule_paths=tuple(str(path) for path in settings.wordlist_rule_paths),
            mask_ladder=tuple(settings.mask_ladder),
            hybrid_masks=tuple(settings.hybrid_masks),
            wordlist_path=(
                str(settings.wordlist_path)
                if settings.wordlist_path is not None
                else None
            ),
        )
    )
    if settings.planner_type == PlannerType.RULE:
        return rule_planner
    if settings.planner_type != PlannerType.LLM:
        raise ValueError(
            f"不支持的 Planner 类型：{settings.planner_type!r}；"
            "自适应能力属于调度层（SAGE_SCHEDULER_TYPE）"
        )
    if not settings.openai_api_key:
        return rule_planner
    gateway_class = (
        OpenAIChatCompletionsGateway
        if settings.llm_api_style == LLMApiStyle.CHAT_COMPLETIONS
        else OpenAIResponsesGateway
    )
    gateway = gateway_class(
        api_key=settings.openai_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout_seconds=settings.llm_timeout_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
        base_url=settings.openai_base_url,
    )
    return LLMPlanner(
        gateway,
        model=settings.llm_model,
        fallback=rule_planner,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
        cache_max_entries=settings.llm_cache_max_entries,
    )


def _allocate_budget(
    total: int,
    strategies: list[StrategyId],
    weights: dict[StrategyId, float],
) -> dict[StrategyId, int]:
    """Allocate all of a positive integer budget with at least one per strategy."""
    if not strategies:
        return {}
    allocations = {strategy_id: 1 for strategy_id in strategies}
    remaining = total - len(strategies)
    if remaining <= 0:
        return allocations
    weight_sum = sum(weights[strategy_id] for strategy_id in strategies)
    raw = {
        strategy_id: remaining * weights[strategy_id] / weight_sum
        for strategy_id in strategies
    }
    for strategy_id in strategies:
        allocations[strategy_id] += int(raw[strategy_id])
    leftover = total - sum(allocations.values())
    ranked = sorted(
        strategies,
        key=lambda strategy_id: (
            -(raw[strategy_id] - int(raw[strategy_id])),
            strategies.index(strategy_id),
        ),
    )
    for strategy_id in ranked[:leftover]:
        allocations[strategy_id] += 1
    return allocations


def _fit_native_units(
    selected: list[StrategyId],
    allocations: dict[StrategyId, int],
    candidate_limit: int,
    native: NativeAttackSettings,
    weights: dict[StrategyId, float],
) -> tuple[dict[StrategyId, tuple[str, ...]], dict[StrategyId, tuple[str, ...]], list[str]]:
    """给原生攻击单元按键空间分配候选预算，并把掩码阶梯裁剪到预算范围内。

    hashcat 自己枚举原生候选，所以一个单元的"实测候选数"是键空间（掩码乘积、
    词表条数 × 规则条数…），而不是计划里的 Python 候选数。若不给它们调整预算，
    实测值会超出分配预算，破坏调度记账不变量
    （tested ≤ candidate_count ≤ 该单元的分配预算）。

    键空间是精确可知的，因此**优先满足原生单元**：先按各自键空间预留预算，剩余的
    预算再按权重分给 Python 生成单元。整个原生键空间都装不下时，退化为逐个裁剪
    （掩码阶梯取能装下的最大掩码、词表由执行层用 hashcat -l 截断）。
    """
    overrides: dict[StrategyId, tuple[str, ...]] = {}
    warnings: list[str] = []

    def _usable(masks: tuple[str, ...], base: int) -> tuple[str, ...]:
        """丢掉"单靠自己就装不下整个候选预算"的掩码：它们永远跑不起来。"""
        return tuple(
            mask
            for mask in masks
            if (mask_keyspace(mask) or 0) * max(1, base) <= candidate_limit
        )

    hybrid_base = max(1, native.wordlist_lines)
    s6_masks = _usable(native.mask_ladder, 1)
    s7_masks = _usable(native.hybrid_masks, hybrid_base)
    rule_overrides: dict[StrategyId, tuple[str, ...]] = {}

    # S1：词表 × 规则链。规则链是**乘积**（66 × 34111 ≈ 225 万条/词），键空间极易超出
    # 候选预算；装不下时按预算缩短规则链（保留"乘积能装下"的前缀），而不是放弃主力攻击。
    s1_keyspace = native.s1_keyspace
    s1_rule_paths = tuple(native.active_rule_paths)
    if s1_rule_paths and native.wordlist_lines > 0:
        chosen: list[str] = []
        product = 1
        for path in s1_rule_paths:
            count = max(1, file_line_count(path))
            if native.wordlist_lines * product * count <= candidate_limit:
                chosen.append(path)
                product *= count
        if tuple(chosen) != s1_rule_paths:
            rule_overrides[StrategyId.S1] = tuple(chosen)
            s1_keyspace = (
                native.wordlist_lines * product if chosen else native.wordlist_lines
            )
            warnings.append(
                "S1 规则链超出候选预算，已缩短为："
                f"{' × '.join(chosen) if chosen else '（无规则，纯词表）'}；"
                f"完整规则链键空间 {native.s1_keyspace:,}，"
                f"如需完整覆盖请把任务候选预算提高到 ≥ {native.s1_keyspace:,}"
            )

    def _mask_total(masks: tuple[str, ...]) -> int | None:
        return masks_keyspace(masks) if masks else None

    s6_keyspace = _mask_total(s6_masks)
    s7_factor = _mask_total(s7_masks)
    s7_keyspace = (
        native.wordlist_lines * s7_factor
        if s7_factor is not None and native.wordlist_lines > 0
        else None
    )
    if s6_masks != native.mask_ladder:
        ignored = [mask for mask in native.mask_ladder if mask not in s6_masks]
        warnings.append(
            "S6 掩码阶梯中超出总候选预算的掩码已被忽略："
            f"{','.join(ignored) or '（全部）'}"
        )
    if s7_masks != native.hybrid_masks:
        ignored = [mask for mask in native.hybrid_masks if mask not in s7_masks]
        warnings.append(
            "S7 混合掩码中超出总候选预算的掩码已被忽略："
            f"{','.join(ignored) or '（全部）'}"
        )

    units = (
        (StrategyId.S1, s1_keyspace, (), 1),
        (StrategyId.S6, s6_keyspace, s6_masks, 1),
        (StrategyId.S7, s7_keyspace, s7_masks, hybrid_base),
    )
    present = [
        (strategy_id, keyspace, masks, base)
        for strategy_id, keyspace, masks, base in units
        if strategy_id in selected and keyspace is not None
    ]
    if not present:
        return overrides, rule_overrides, warnings
    for strategy_id, _, masks, _ in present:
        if masks:
            overrides[strategy_id] = masks

    native_total = sum(keyspace for _, keyspace, _, _ in present)
    if native_total > candidate_limit:
        # 原生键空间总和超出候选预算：优先砍"最贵的掩码"，保住便宜但高价值的那些
        # （例如"词 + 4 位年份"只占几十万键，而"?l?l?l?l?d?d"要几千万键）。
        # 这样做的好处：候选预算只有百万级时，"词 + 年份"这类形态依然可达，而不是
        # 被最贵的掩码把预算吃光（实测：预算 100 万时 ?d?d?d?d 曾被裁掉，
        # 导致 summer2023 这类"词 + 年份"口令整体不可达）。
        droppable: list[tuple[int, int, StrategyId, str]] = []
        # 裁剪顺序：先砍纯掩码单元（S6 的"?l?l?l?l"这类随机字母几乎无实战价值），
        # 再砍"词表 × 掩码"（S7：词根 + 后缀/年份，价值高得多）；同一单元内先砍最贵的。
        mask_drop_rank = {StrategyId.S6: 0, StrategyId.S7: 1}
        for strategy_id, _, masks, base in present:
            for mask in masks:
                size = mask_keyspace(mask) or 0
                if size > 0:
                    droppable.append(
                        (
                            mask_drop_rank.get(strategy_id, 2),
                            -(size * max(1, base)),
                            strategy_id,
                            mask,
                        )
                    )
        droppable.sort(key=lambda item: (item[0], item[1]))
        kept_masks = {sid: list(masks) for sid, _, masks, _ in present}
        totals = {sid: keyspace for sid, keyspace, _, _ in present}
        dropped: list[tuple[StrategyId, str]] = []
        for _, neg_cost, strategy_id, mask in droppable:
            if sum(totals.values()) <= candidate_limit:
                break
            if mask in kept_masks[strategy_id]:
                kept_masks[strategy_id].remove(mask)
                totals[strategy_id] = max(0, totals[strategy_id] + neg_cost)
                dropped.append((strategy_id, mask))
        if dropped:
            trimmed_present = []
            for strategy_id, keyspace, masks, base in present:
                if masks:
                    overrides[strategy_id] = tuple(kept_masks[strategy_id])
                trimmed_present.append(
                    (strategy_id, totals[strategy_id], masks, base)
                )
            present = [unit for unit in trimmed_present if unit[1] > 0]
            native_total = sum(keyspace for _, keyspace, _, _ in present)
            for strategy_id, mask in dropped:
                warnings.append(
                    f"{strategy_id.value} 掩码 {mask} 因候选预算不足被裁掉"
                    f"（该掩码键空间 {mask_keyspace(mask) or 0}）"
                )

    if native_total <= candidate_limit:
        native_ids = {strategy_id for strategy_id, _, _, _ in present}
        for strategy_id, keyspace, _, _ in present:
            allocations[strategy_id] = keyspace
        others = [item for item in selected if item not in native_ids]
        if others:
            rest = max(len(others), candidate_limit - native_total)
            remapped = _allocate_budget(rest, others, weights)
            for strategy_id, value in remapped.items():
                allocations[strategy_id] = value
        return overrides, rule_overrides, warnings

    for strategy_id, keyspace, masks, base in present:
        available = candidate_limit - sum(
            budget for other, budget in allocations.items() if other != strategy_id
        )
        if available < 1:
            available = 1
        if keyspace <= available:
            allocations[strategy_id] = max(allocations[strategy_id], keyspace)
            continue
        if not masks:
            # S1：键空间 = 词表条数 × 规则链倍数，由执行层按预算切词表。
            allocations[strategy_id] = available
            warnings.append(
                f"{strategy_id.value} 原生键空间 {keyspace:,} 超出可用候选预算 "
                f"{available:,}，将按预算截断词表；"
                f"如需完整跑完 S1 请把任务候选预算提高到 ≥ {keyspace:,}"
            )
            continue
        kept, used = select_masks_within(masks, base=base, budget=available)
        if kept:
            overrides[strategy_id] = tuple(kept)
            allocations[strategy_id] = max(allocations[strategy_id], used)
            if len(kept) != len(masks):
                warnings.append(
                    f"{strategy_id.value} 掩码阶梯按候选预算裁剪为 "
                    f"{','.join(kept)}（键空间 {used:,}）；"
                    f"完整阶梯需要候选预算 ≥ {keyspace:,}"
                )
        else:
            # 连最小的掩码都装不下：只保留最小的一个作占位，执行层会跳过该单元。
            smallest = min(
                masks,
                key=lambda mask: mask_keyspace(mask) or 0,
            )
            overrides[strategy_id] = (smallest,)
            warnings.append(
                f"{strategy_id.value} 最小掩码键空间仍超出可用候选预算 {available:,}，"
                "本次执行将跳过该单元"
            )
    return overrides, rule_overrides, warnings


def _rule_parameters(
    strategy_id: StrategyId,
    cost: VerificationCost,
    candidate_budget: int,
    native: "NativeAttackSettings | None" = None,
    native_masks: "dict[StrategyId, tuple[str, ...]] | None" = None,
    native_rules: "dict[StrategyId, tuple[str, ...]] | None" = None,
) -> dict[str, Any]:
    settings = native or NativeAttackSettings()
    override = (native_masks or {}).get(strategy_id)
    rule_override = (native_rules or {}).get(strategy_id)
    if strategy_id == StrategyId.S1:
        # 词表 × 规则：hashcat 最经典的主力攻击（-a 0 dict.txt -r best66.rule）。
        # 规则作用在外部词表上，而不是 Python 生成的那几十条基线上；
        # 多个规则文件是**规则链**（乘积），预算不够时由计划层缩短（rule_override）。
        rule_paths = (
            rule_override if rule_override is not None else settings.active_rule_paths
        )
        if rule_paths:
            return {"hashcat_rule_files": list(rule_paths)}
        return {}
    if strategy_id == StrategyId.S2:
        parameters: dict[str, Any] = {
            "capitalize_first": True,
            "all_upper": True,
            "all_lower": True,
            "common_number_suffix": True,
            "year_suffix": True,
            "common_substitution": True,
            "symbol_suffix": True,
        }
        return parameters
    if strategy_id == StrategyId.S6:
        # 掩码阶梯：掩码作为 hashcat 参数（-a 3），空间由 hashcat 自己枚举。
        return {
            "hashcat_attack_mode": 3,
            "hashcat_masks": list(override if override else settings.mask_ladder),
        }
    if strategy_id == StrategyId.S7:
        # 混合攻击：词表 + 掩码（-a 6），掩码直接作为 hashcat 参数。
        return {
            "hashcat_attack_mode": 6,
            "hashcat_hybrid_mask": list(override if override else settings.hybrid_masks),
        }
    if strategy_id == StrategyId.S3:
        if cost == VerificationCost.HIGH:
            return {
                "max_templates": min(64, candidate_budget),
                "min_probability": 0.01,
                "max_structure_length": 20,
            }
        return {
            "max_templates": min(256, candidate_budget),
            "min_probability": 0.001 if cost == VerificationCost.MEDIUM else 0.0001,
            "max_structure_length": 32,
        }
    if strategy_id == StrategyId.S4:
        return {
            "use_keywords": True,
            "use_pinyin": True,
            "use_abbreviations": True,
            "use_years": True,
            "use_region": True,
            "use_organization": True,
            "max_combinations": min(candidate_budget, 1_000_000),
        }
    return {}


def _task_profile(
    prir: PRIR, knowledge_summary: KnowledgeSummary | None = None
) -> dict[str, Any]:
    """The only data sent to the LLM. Deliberately excludes target/hash content."""
    profile = {
        "target_type": prir.target_type.value,
        "algorithm": prir.algorithm,
        "salt": prir.salt,
        "verification_cost": prir.verification_cost.value,
        "context_available": prir.context_available,
        "candidate_space": prir.candidate_space,
        "time_budget": prir.time_budget,
        "candidate_budget": prir.candidate_budget,
        "confidence": prir.confidence,
        "warnings": list(prir.warnings),
    }
    if prir.information_profile is not None:
        profile["information_profile"] = prir.information_profile.model_dump(
            mode="json"
        )
    if knowledge_summary is not None:
        profile["feedback_summary"] = knowledge_summary.public_dict()
    return profile


def _cache_key(profile: dict[str, Any], model: str) -> str:
    material = json.dumps(
        {"version": PROMPT_VERSION, "model": model, "profile": profile},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _build_strategy_plan(prir: PRIR, proposal: _LLMPlan) -> StrategyPlan:
    strategies: list[StrategyItem] = []
    for item in proposal.strategies:
        parameters = dict(item.parameters)
        if item.strategy_id == StrategyId.S2:
            # LLM 协议只负责选择和分配预算，parameters 必须为空；S2 的
            # 可执行规则由本地可信策略层补齐，避免给空规则单元分配预算。
            parameters = _rule_parameters(
                item.strategy_id,
                prir.verification_cost,
                item.candidate_budget,
            )
        strategies.append(
            StrategyItem(
                strategy_id=item.strategy_id,
                strategy_name=STRATEGY_NAMES.get(
                    item.strategy_id, item.strategy_id.value
                ),
                priority=item.priority,
                time_budget=item.time_budget,
                candidate_budget=item.candidate_budget,
                reason=item.reason,
                parameters=parameters,
            )
        )
    return StrategyPlan(
        task_id=prir.task_id,
        planner_type=PlannerType.LLM,
        total_time_budget=prir.time_budget,
        strategies=sorted(strategies, key=lambda item: item.priority),
        status=TaskStatus.PLANNED,
        warnings=proposal.warnings,
    )


def _baseline_strategy(prir: PRIR) -> StrategyItem:
    return StrategyItem(
        strategy_id=StrategyId.S1,
        strategy_name="Baseline",
        priority=1,
        time_budget=max(1, prir.time_budget // 5),
        candidate_budget=max(1, prir.candidate_budget // 5),
        reason="优先测试高频口令",
        parameters={},
    )


def _context_strategy(
    prir: PRIR, *, max_time_budget: int, max_candidate_budget: int
) -> StrategyItem:
    return StrategyItem(
        strategy_id=StrategyId.S4,
        strategy_name="Context",
        priority=2,
        time_budget=min(max_time_budget, max(1, (prir.time_budget * 2) // 5)),
        candidate_budget=min(
            max_candidate_budget, max(1, (prir.candidate_budget * 2) // 5)
        ),
        reason="任务提供了上下文信息",
        parameters={
            "use_keywords": True,
            "use_pinyin": True,
            "use_abbreviations": True,
            "use_years": True,
            "use_region": True,
            "use_organization": True,
            "max_combinations": min(
                max_candidate_budget,
                max(1, (prir.candidate_budget * 2) // 5),
            ),
        },
    )
