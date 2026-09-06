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
from .policy import PolicyValidationError, PolicyValidator
from .schemas import PRIR, StrategyItem, StrategyPlan


PROMPT_VERSION = "week2-llm-planner-v1"
STRATEGY_NAMES = {
    StrategyId.S1: "Baseline",
    StrategyId.S2: "Rule",
    StrategyId.S3: "PCFG-lite",
    StrategyId.S4: "Context",
}


class PlannerGateway(Protocol):
    """Small boundary around an LLM provider, making the planner testable."""

    def create_plan(self, task_profile: dict[str, Any]) -> str: ...


class _LLMStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: StrategyId
    priority: int = Field(ge=1, le=4)
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

    strategies: list[_LLMStrategy] = Field(min_length=1, max_length=4)
    warnings: list[str] = Field(default_factory=list, max_length=8)


LLM_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "strategies": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "strategy_id": {
                        "type": "string",
                        "enum": ["S1", "S2", "S3", "S4"],
                    },
                    "priority": {"type": "integer", "minimum": 1, "maximum": 4},
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
Choose and budget strategies from S1 Baseline, S2 Rule, S3 PCFG-lite, and S4
Context. Use S4 only when context_available is true. Low verification cost permits
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

    def plan(self, prir: PRIR) -> StrategyPlan:
        profile = _task_profile(prir)
        key = _cache_key(profile, self.model)
        try:
            cached = self._cache_get(key)
            if cached is not None:
                cached_plan = _build_strategy_plan(
                    prir, _LLMPlan.model_validate(cached)
                )
                return self.validator.validate(prir, cached_plan)
            raw = self.gateway.create_plan(profile)
            proposal = _LLMPlan.model_validate_json(raw)
            plan = _build_strategy_plan(prir, proposal)
            plan = self.validator.validate(prir, plan)
        except Exception as exc:
            fallback = self.fallback.plan(prir)
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
    def plan(self, prir: PRIR) -> StrategyPlan:
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
    },
    VerificationCost.MEDIUM: {
        StrategyId.S1: 0.30,
        StrategyId.S2: 0.30,
        StrategyId.S3: 0.20,
        StrategyId.S4: 0.20,
    },
    VerificationCost.HIGH: {
        StrategyId.S1: 0.45,
        StrategyId.S2: 0.17,
        StrategyId.S3: 0.08,
        StrategyId.S4: 0.30,
    },
    VerificationCost.UNKNOWN: {
        StrategyId.S1: 0.35,
        StrategyId.S2: 0.25,
        StrategyId.S3: 0.20,
        StrategyId.S4: 0.20,
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
    StrategyId.S3: "按有限 PCFG 结构概率扩展候选覆盖",
    StrategyId.S4: "利用已提供的关键词、拼音、缩写、年份、地区和组织信息",
}


class RulePlanner:
    """Deterministic planner used without an LLM and as the LLM fallback."""

    def __init__(
        self,
        *,
        validator: PolicyValidator | None = None,
        fallback: MockPlanner | None = None,
    ) -> None:
        self.validator = validator or PolicyValidator()
        self.fallback = fallback or MockPlanner()

    def plan(self, prir: PRIR) -> StrategyPlan:
        if prir.target_type == TargetType.UNKNOWN:
            return self._fallback(prir, "规则规划器不支持 unknown 目标")

        order = list(RULE_ORDERS[prir.verification_cost])
        if prir.context_available:
            if prir.verification_cost == VerificationCost.HIGH:
                order.insert(1, StrategyId.S4)
            else:
                order.append(StrategyId.S4)

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
                ),
            )
            for index, strategy_id in enumerate(selected, start=1)
        ]
        warnings: list[str] = []
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
            return self.validator.validate(prir, plan)
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
    rule_planner = RulePlanner()
    if settings.planner_type == PlannerType.RULE:
        return rule_planner
    if settings.planner_type != PlannerType.LLM:
        return MockPlanner()
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


def _rule_parameters(
    strategy_id: StrategyId,
    cost: VerificationCost,
    candidate_budget: int,
) -> dict[str, Any]:
    if strategy_id == StrategyId.S1:
        return {}
    if strategy_id == StrategyId.S2:
        return {
            "capitalize_first": True,
            "all_upper": True,
            "all_lower": True,
            "common_number_suffix": True,
            "year_suffix": True,
            "common_substitution": True,
            "symbol_suffix": True,
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
    return {
        "use_keywords": True,
        "use_pinyin": True,
        "use_abbreviations": True,
        "use_years": True,
        "use_region": True,
        "use_organization": True,
        "max_combinations": min(candidate_budget, 1_000_000),
    }


def _task_profile(prir: PRIR) -> dict[str, Any]:
    """The only data sent to the LLM. Deliberately excludes target/hash content."""
    return {
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
                parameters=item.parameters,
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
