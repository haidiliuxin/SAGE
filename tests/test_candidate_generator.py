"""乙方 S1/S2 候选生成与 Hashcat 仿真联调测试。"""

from __future__ import annotations

import sys

import pytest

from sage_pass.candidate_generator import CandidateGenerator
from sage_pass.enums import ExecutionMode, PlannerType, StrategyId, TaskStatus
from sage_pass.hashcat_adapter import HashcatAdapter, HashcatJob
from sage_pass.schemas import ExecutionRequest, StrategyItem, StrategyPlan

from sim_binaries import write_sim_scripts


TARGET = "0123456789abcdef0123456789abcdef"


def strategy(
    strategy_id: StrategyId,
    *,
    priority: int,
    candidate_budget: int,
    parameters: dict[str, object] | None = None,
) -> StrategyItem:
    return StrategyItem(
        strategy_id=strategy_id,
        strategy_name="Baseline" if strategy_id == StrategyId.S1 else "Rule",
        priority=priority,
        time_budget=10,
        candidate_budget=candidate_budget,
        reason="test",
        parameters=parameters or {},
    )


def plan(*strategies: StrategyItem) -> StrategyPlan:
    return StrategyPlan(
        task_id="T-candidates",
        planner_type=PlannerType.RULE,
        total_time_budget=sum(item.time_budget for item in strategies),
        strategies=list(strategies),
    )


def test_s1_preserves_baseline_order_deduplicates_and_obeys_budget():
    generator = CandidateGenerator(
        baseline_candidates=("password", "admin", "password", "123456")
    )
    batches = list(
        generator.iter_plan_batches(
            plan(strategy(StrategyId.S1, priority=1, candidate_budget=3)),
            batch_size=2,
        )
    )

    assert [batch.strategy_id for batch in batches] == [StrategyId.S1] * 2
    assert [batch.candidates for batch in batches] == [
        ("password", "admin"),
        ("123456",),
    ]


def test_s2_applies_enabled_rules_in_stable_order():
    generator = CandidateGenerator(
        baseline_candidates=("pass",),
        number_suffixes=("1",),
        year_suffixes=("2026",),
        symbol_suffixes=("!",),
    )
    parameters = {
        "capitalize_first": True,
        "all_upper": True,
        "all_lower": False,
        "common_number_suffix": True,
        "year_suffix": True,
        "common_substitution": True,
        "symbol_suffix": True,
    }

    candidates = generator.build_execute_candidates(
        plan(
            strategy(
                StrategyId.S2,
                priority=1,
                candidate_budget=20,
                parameters=parameters,
            )
        )
    )

    assert candidates == [
        "Pass",
        "PASS",
        "p@55",
        "pass1",
        "Pass1",
        "PASS1",
        "p@551",
        "pass2026",
        "Pass2026",
        "PASS2026",
        "p@552026",
        "pass!",
        "Pass!",
        "PASS!",
        "p@55!",
    ]


def test_s2_all_lower_rule_and_zero_budget():
    generator = CandidateGenerator(baseline_candidates=("PassWord",))
    candidates = generator.build_execute_candidates(
        plan(
            strategy(
                StrategyId.S2,
                priority=1,
                candidate_budget=1,
                parameters={"all_lower": True},
            ),
            strategy(StrategyId.S1, priority=2, candidate_budget=0),
        )
    )

    assert candidates == ["password"]


def test_plan_deduplicates_across_strategies_and_limits_each_budget():
    generator = CandidateGenerator(
        baseline_candidates=("password", "admin"),
        number_suffixes=("1",),
    )
    candidate_plan = plan(
        strategy(StrategyId.S1, priority=1, candidate_budget=2),
        strategy(
            StrategyId.S2,
            priority=2,
            candidate_budget=4,
            parameters={
                "capitalize_first": True,
                "all_lower": True,
                "common_number_suffix": True,
            },
        ),
    )

    batches = list(generator.iter_plan_batches(candidate_plan, batch_size=2))

    assert [(batch.strategy_id, batch.candidates) for batch in batches] == [
        (StrategyId.S1, ("password", "admin")),
        (StrategyId.S2, ("Password", "password1")),
        (StrategyId.S2, ("Password1", "Admin")),
    ]
    flattened = [candidate for batch in batches for candidate in batch.candidates]
    assert len(flattened) == len(set(flattened))
    assert len(flattened[:2]) <= candidate_plan.strategies[0].candidate_budget
    assert len(flattened[2:]) <= candidate_plan.strategies[1].candidate_budget


def test_generated_candidates_match_current_execution_request_contract():
    generator = CandidateGenerator(baseline_candidates=("ok", "x" * 1024))
    candidates = generator.build_execute_candidates(
        plan(strategy(StrategyId.S1, priority=1, candidate_budget=2))
    )

    request = ExecutionRequest(mode=ExecutionMode.REAL, candidates=candidates)

    assert request.candidates == ["ok", "x" * 1024]
    assert all(
        candidate and "\n" not in candidate and "\r" not in candidate
        for candidate in request.candidates
    )


@pytest.mark.parametrize("invalid", ["", "bad\nline", "bad\rline", "x" * 1025])
def test_invalid_baseline_or_rule_seed_is_rejected(invalid):
    with pytest.raises(ValueError, match="单行文本"):
        CandidateGenerator(baseline_candidates=(invalid,))

    generator = CandidateGenerator(baseline_candidates=("valid",))
    with pytest.raises(ValueError, match="单行文本"):
        list(
            generator.iter_plan_batches(
                plan(
                    strategy(
                        StrategyId.S2,
                        priority=1,
                        candidate_budget=2,
                        parameters={"all_upper": True},
                    )
                ),
                rule_seeds=(invalid,),
            )
        )


def test_candidate_batches_execute_with_correct_per_strategy_statistics(
    tmp_path, monkeypatch
):
    hashcat_script, _ = write_sim_scripts(tmp_path)
    adapter = HashcatAdapter([sys.executable, str(hashcat_script)])
    generator = CandidateGenerator(baseline_candidates=("alpha", "beta"))
    candidate_plan = plan(
        strategy(StrategyId.S1, priority=1, candidate_budget=2),
        strategy(
            StrategyId.S2,
            priority=2,
            candidate_budget=4,
            parameters={"capitalize_first": True, "all_upper": True},
        ),
    )

    statistics = {
        StrategyId.S1: {"tested": 0, "recovered": 0},
        StrategyId.S2: {"tested": 0, "recovered": 0},
    }
    batches = list(generator.iter_plan_batches(candidate_plan, batch_size=2))
    for index, batch in enumerate(batches):
        result = adapter.start(
            HashcatJob(
                run_id=f"candidate-batch-{index}",
                target_hashes=(TARGET,),
                hash_mode=0,
                candidates=batch.candidates,
                timeout_seconds=10,
                candidate_budget=len(batch.candidates),
            )
        ).wait()
        assert result.status == TaskStatus.COMPLETED
        statistics[batch.strategy_id]["tested"] += result.tested
        statistics[batch.strategy_id]["recovered"] += len(result.recovered)

    assert statistics == {
        StrategyId.S1: {"tested": 2, "recovered": 1},
        StrategyId.S2: {"tested": 4, "recovered": 2},
    }
