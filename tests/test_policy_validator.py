from __future__ import annotations

import pytest

from sage_pass.enums import (
    PlannerType,
    StrategyId,
    TargetType,
    TaskStatus,
    VerificationCost,
)
from sage_pass.policy import PolicyValidationError, PolicyValidator
from sage_pass.schemas import PRIR, StrategyItem, StrategyPlan


def make_prir(
    *,
    target_type: TargetType = TargetType.HASH,
    context: bool = True,
    time_budget: int = 100,
    candidate_budget: int = 10_000,
) -> PRIR:
    return PRIR(
        task_id="T-POLICY",
        target_type=target_type,
        algorithm="bcrypt",
        salt=True,
        verification_cost=VerificationCost.HIGH,
        context_available=context,
        time_budget=time_budget,
        candidate_budget=candidate_budget,
        confidence=0.9,
    )


def strategy(
    strategy_id: StrategyId,
    *,
    priority: int = 1,
    time_budget: int = 20,
    candidate_budget: int = 1000,
    parameters: dict | None = None,
) -> StrategyItem:
    return StrategyItem(
        strategy_id=strategy_id,
        strategy_name=strategy_id.value,
        priority=priority,
        time_budget=time_budget,
        candidate_budget=candidate_budget,
        reason="policy test",
        parameters=parameters or {},
    )


def plan(
    items: list[StrategyItem], *, total_time_budget: int = 100
) -> StrategyPlan:
    return StrategyPlan(
        task_id="T-POLICY",
        planner_type=PlannerType.LLM,
        total_time_budget=total_time_budget,
        strategies=items,
        status=TaskStatus.PLANNED,
    )


def issue_codes(error: PolicyValidationError) -> set[str]:
    return {issue.code for issue in error.issues}


def test_accepts_whitelisted_applicable_plan_and_known_parameters():
    candidate = plan(
        [
            strategy(StrategyId.S1, priority=1),
            strategy(
                StrategyId.S2,
                priority=2,
                parameters={"capitalize_first": True, "symbol_suffix": False},
            ),
            strategy(
                StrategyId.S3,
                priority=3,
                parameters={"max_templates": 128, "min_probability": 0.01},
            ),
            strategy(
                StrategyId.S4,
                priority=4,
                parameters={"use_years": True, "max_combinations": 500},
            ),
        ]
    )

    assert PolicyValidator().validate(make_prir(), candidate) is candidate


def test_rejects_strategy_outside_week2_whitelist():
    with pytest.raises(PolicyValidationError) as captured:
        PolicyValidator().validate(make_prir(), plan([strategy(StrategyId.S5)]))

    assert "STRATEGY_NOT_ALLOWED" in issue_codes(captured.value)


def test_rejects_unsupported_target_and_context_strategy_without_context():
    validator = PolicyValidator()
    with pytest.raises(PolicyValidationError) as unsupported:
        validator.validate(
            make_prir(target_type=TargetType.UNKNOWN),
            plan([strategy(StrategyId.S1)]),
        )
    with pytest.raises(PolicyValidationError) as no_context:
        validator.validate(
            make_prir(context=False), plan([strategy(StrategyId.S4)])
        )

    assert "TARGET_NOT_SUPPORTED" in issue_codes(unsupported.value)
    assert "CONTEXT_REQUIRED" in issue_codes(no_context.value)


def test_rejects_total_time_and_candidate_budget_overruns():
    over_budget = plan(
        [
            strategy(
                StrategyId.S1,
                time_budget=101,
                candidate_budget=10_001,
            )
        ],
        total_time_budget=101,
    )

    with pytest.raises(PolicyValidationError) as captured:
        PolicyValidator().validate(make_prir(), over_budget)

    assert {
        "BUDGET_MISMATCH",
        "TIME_BUDGET_EXCEEDED",
        "CANDIDATE_BUDGET_EXCEEDED",
    } <= issue_codes(captured.value)


@pytest.mark.parametrize(
    ("item", "expected_code"),
    [
        (
            strategy(StrategyId.S1, parameters={"dictionary_path": "secret.txt"}),
            "PARAMETER_NOT_ALLOWED",
        ),
        (
            strategy(StrategyId.S2, parameters={"all_upper": "yes"}),
            "PARAMETER_OUT_OF_RANGE",
        ),
        (
            strategy(StrategyId.S3, parameters={"max_templates": 513}),
            "PARAMETER_OUT_OF_RANGE",
        ),
        (
            strategy(
                StrategyId.S4,
                candidate_budget=100,
                parameters={"max_combinations": 101},
            ),
            "PARAMETER_OUT_OF_RANGE",
        ),
    ],
)
def test_rejects_unknown_wrong_type_and_out_of_range_parameters(
    item: StrategyItem, expected_code: str
):
    with pytest.raises(PolicyValidationError) as captured:
        PolicyValidator().validate(make_prir(), plan([item]))

    assert expected_code in issue_codes(captured.value)


def test_rejects_duplicate_strategy_and_non_contiguous_priority():
    candidate = plan(
        [
            strategy(StrategyId.S1, priority=1),
            strategy(StrategyId.S1, priority=3),
        ]
    )

    with pytest.raises(PolicyValidationError) as captured:
        PolicyValidator().validate(make_prir(), candidate)

    assert {"DUPLICATE_STRATEGY", "INVALID_PRIORITY"} <= issue_codes(
        captured.value
    )
