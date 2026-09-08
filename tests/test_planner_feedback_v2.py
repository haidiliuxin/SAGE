from __future__ import annotations

import json

import pytest

from sage_pass.enums import PlannerType, StrategyId, TargetType, VerificationCost
from sage_pass.planner import LLM_PLAN_JSON_SCHEMA, LLMPlanner, RulePlanner
from sage_pass.policy import PolicyValidationError, PolicyValidator
from sage_pass.schemas import PRIR, StrategyItem, StrategyPlan
from sage_pass.transfer import KnowledgeSummary


def _prir(candidate_budget: int = 10_000) -> PRIR:
    return PRIR(
        task_id="T-FEEDBACK-PLAN",
        target_type=TargetType.HASH,
        algorithm="md5",
        salt=False,
        verification_cost=VerificationCost.LOW,
        context_available=False,
        candidate_space=None,
        time_budget=100,
        candidate_budget=candidate_budget,
        confidence=0.9,
    )


def _summary() -> KnowledgeSummary:
    return KnowledgeSummary(
        available=True,
        pattern_count=4,
        top_pattern_types=("suffix_pattern", "structure_signature"),
        highest_confidence=0.72,
        suggested_s5_max_budget=500,
        transfer_score=0.61,
    )


def test_rule_planner_adds_s5_only_when_knowledge_is_available():
    without = RulePlanner().plan(_prir())
    with_knowledge = RulePlanner().plan(_prir(), knowledge_summary=_summary())
    assert StrategyId.S5 not in [item.strategy_id for item in without.strategies]
    assert [item.strategy_id for item in with_knowledge.strategies][1] == StrategyId.S5
    s5 = next(item for item in with_knowledge.strategies if item.strategy_id == StrategyId.S5)
    assert s5.candidate_budget <= _summary().suggested_s5_max_budget
    assert sum(item.time_budget for item in with_knowledge.strategies) <= 100
    assert sum(item.candidate_budget for item in with_knowledge.strategies) <= 10_000


def test_policy_rejects_s5_without_knowledge_and_accepts_it_with_summary():
    plan = StrategyPlan(
        task_id=_prir().task_id,
        planner_type=PlannerType.LLM,
        total_time_budget=100,
        strategies=[StrategyItem(
            strategy_id=StrategyId.S5,
            strategy_name="Transfer",
            priority=1,
            time_budget=10,
            candidate_budget=100,
            reason="abstract transfer",
            parameters={},
        )],
    )
    with pytest.raises(PolicyValidationError) as captured:
        PolicyValidator().validate(_prir(), plan)
    assert "TRANSFER_KNOWLEDGE_REQUIRED" in {
        issue.code for issue in captured.value.issues
    }
    assert PolicyValidator().validate(_prir(), plan, _summary()) is plan


def test_llm_schema_supports_s5_and_receives_only_safe_summary():
    enum = LLM_PLAN_JSON_SCHEMA["properties"]["strategies"]["items"][
        "properties"
    ]["strategy_id"]["enum"]
    assert "S5" in enum

    class Gateway:
        profile = None

        def create_plan(self, task_profile):
            self.profile = task_profile
            return json.dumps({
                "strategies": [{
                    "strategy_id": "S5",
                    "priority": 1,
                    "time_budget": 10,
                    "candidate_budget": 100,
                    "reason": "use abstract history",
                    "parameters": {},
                }],
                "warnings": [],
            })

    gateway = Gateway()
    result = LLMPlanner(gateway, model="test").plan(
        _prir(), knowledge_summary=_summary()
    )
    assert result.planner_type == PlannerType.LLM
    assert result.strategies[0].strategy_id == StrategyId.S5
    payload = json.dumps(gateway.profile, ensure_ascii=False)
    assert "feedback_summary" in gateway.profile
    assert set(gateway.profile["feedback_summary"]) == {
        "available",
        "pattern_count",
        "top_pattern_types",
        "highest_confidence",
        "suggested_s5_max_budget",
    }
    assert "RecoveredSecret2025!" not in payload
    assert "target_content" not in payload
    assert "hash" not in gateway.profile or gateway.profile["target_type"] == "hash"


def test_illegal_llm_s5_without_knowledge_falls_back_to_rule_plan():
    class Gateway:
        def create_plan(self, _):
            return json.dumps({
                "strategies": [{
                    "strategy_id": "S5",
                    "priority": 1,
                    "time_budget": 10,
                    "candidate_budget": 100,
                    "reason": "invalid without knowledge",
                    "parameters": {},
                }],
                "warnings": [],
            })

    result = LLMPlanner(
        Gateway(), model="test", fallback=RulePlanner()
    ).plan(_prir())
    assert result.planner_type == PlannerType.RULE
    assert StrategyId.S5 not in [item.strategy_id for item in result.strategies]
    assert any("降级" in warning for warning in result.warnings)
