from __future__ import annotations

from pathlib import Path

from sage_pass.config import Settings
from sage_pass.enums import (
    LLMApiStyle,
    PlannerType,
    StrategyId,
    TargetType,
    VerificationCost,
)
from sage_pass.planner import (
    LLMPlanner,
    MockPlanner,
    OpenAIChatCompletionsGateway,
    RulePlanner,
    build_planner,
)
from sage_pass.schemas import PRIR


def make_prir(
    *,
    cost: VerificationCost = VerificationCost.MEDIUM,
    context: bool = False,
    target_type: TargetType = TargetType.HASH,
    time_budget: int = 100,
    candidate_budget: int = 100_000,
) -> PRIR:
    return PRIR(
        task_id="T-RULE",
        target_type=target_type,
        algorithm="bcrypt" if cost == VerificationCost.HIGH else "sha256",
        salt=cost == VerificationCost.HIGH,
        verification_cost=cost,
        context_available=context,
        time_budget=time_budget,
        candidate_budget=candidate_budget,
        confidence=0.9,
    )


def ids(plan) -> list[StrategyId]:
    return [item.strategy_id for item in plan.strategies]


def test_without_context_prioritizes_s1_s2_s3():
    result = RulePlanner().plan(make_prir())

    assert result.planner_type == PlannerType.RULE
    assert ids(result) == [StrategyId.S1, StrategyId.S2, StrategyId.S3]
    assert sum(item.time_budget for item in result.strategies) == 100
    assert sum(item.candidate_budget for item in result.strategies) == 60_000


def test_context_adds_s4_for_non_slow_hash():
    result = RulePlanner().plan(
        make_prir(cost=VerificationCost.LOW, context=True)
    )

    assert ids(result) == [
        StrategyId.S1,
        StrategyId.S2,
        StrategyId.S3,
        StrategyId.S4,
    ]
    assert sum(item.candidate_budget for item in result.strategies) == 100_000
    context = result.strategies[-1]
    assert context.parameters["max_combinations"] == context.candidate_budget


def test_slow_hash_prioritizes_small_high_probability_candidate_sets():
    result = RulePlanner().plan(
        make_prir(cost=VerificationCost.HIGH, context=True)
    )

    assert ids(result) == [
        StrategyId.S1,
        StrategyId.S4,
        StrategyId.S2,
        StrategyId.S3,
    ]
    assert sum(item.candidate_budget for item in result.strategies) == 25_000
    assert result.strategies[0].candidate_budget > result.strategies[2].candidate_budget
    assert result.strategies[1].candidate_budget > result.strategies[3].candidate_budget
    pcfg = next(item for item in result.strategies if item.strategy_id == StrategyId.S3)
    assert pcfg.parameters == {
        "max_templates": 64,
        "min_probability": 0.01,
        "max_structure_length": 20,
    }
    assert any("慢 Hash" in warning for warning in result.warnings)


def test_tiny_budget_keeps_only_highest_priority_strategy():
    result = RulePlanner().plan(
        make_prir(time_budget=1, candidate_budget=1, context=True)
    )

    assert ids(result) == [StrategyId.S1]
    assert result.strategies[0].time_budget == 1
    assert result.strategies[0].candidate_budget == 1
    assert result.warnings == ["任务预算过小，未加入策略：S2, S3, S4"]


def test_unknown_target_degrades_to_mock():
    result = RulePlanner().plan(make_prir(target_type=TargetType.UNKNOWN))

    assert result.planner_type == PlannerType.MOCK
    assert result.warnings[-1].endswith("已降级为 Mock 计划")


def settings(
    planner_type: PlannerType,
    *,
    api_key: str | None = None,
    api_style: LLMApiStyle = LLMApiStyle.RESPONSES,
) -> Settings:
    return Settings(
        database_url="sqlite://",
        upload_dir=Path("uploads"),
        max_upload_bytes=1024,
        cors_origins=(),
        planner_type=planner_type,
        openai_api_key=api_key,
        llm_api_style=api_style,
    )


def test_planner_factory_selects_mock_and_rule_modes():
    assert isinstance(build_planner(settings(PlannerType.MOCK)), MockPlanner)
    assert isinstance(build_planner(settings(PlannerType.RULE)), RulePlanner)


def test_llm_mode_without_key_uses_rule_planner():
    assert isinstance(build_planner(settings(PlannerType.LLM)), RulePlanner)


def test_planner_factory_selects_chat_completions_gateway():
    configured = settings(
        PlannerType.LLM,
        api_key="not-used",
        api_style=LLMApiStyle.CHAT_COMPLETIONS,
    )

    planner = build_planner(configured)

    assert isinstance(planner, LLMPlanner)
    assert isinstance(planner.gateway, OpenAIChatCompletionsGateway)


def test_llm_failure_can_fallback_to_rule_planner():
    class FailedGateway:
        def create_plan(self, _):
            raise TimeoutError("provider details must not escape")

    result = LLMPlanner(
        FailedGateway(), model="test", fallback=RulePlanner()
    ).plan(make_prir(context=True))

    assert result.planner_type == PlannerType.RULE
    assert result.warnings[-1] == (
        "LLM Planner 不可用，已降级为 Rule 计划（TimeoutError）"
    )
