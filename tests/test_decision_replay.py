"""B milestone 1-3 acceptance cases. Run by the project owner, not during authoring."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from sage_pass.decision import BaselinePolicy, DecisionArm, DecisionFeedback
from sage_pass.enums import TaskStatus
from sage_pass.experiments.replay import (
    ReplayCost, ReplayEnvironment, ReplayScenario, compare_baselines,
)
from sage_pass.scheduler import ArmSpec, RoundRobinScheduler


POLICIES = ("fixed", "round_robin", "heuristic_bandit")


def scenario(*, budget=8, seconds=20, batch_size=2, jitter=0):
    return ReplayScenario(
        arms=(
            DecisionArm("a", "S3", "pcfg", 1, 8, seconds),
            DecisionArm("b", "S3", "markov", 2, 8, seconds),
        ),
        candidate_streams={"a": ("x", "x", "y", "z", "end"), "b": ("x", "p", "q", "r")},
        target_ids=("u1", "u2", "unreachable"),
        matches={"x": ("u1", "u2"), "p": ("u1",)},
        total_candidate_budget=budget, total_time_budget=seconds,
        batch_size=batch_size, cost=ReplayCost(0.1, 0.2), seed=23, time_jitter=jitter,
    )


def feedback(decision, *, candidates=1, recovered_ids=(), hits=0):
    return DecisionFeedback(
        run_id="test", arm_id=decision.arm_id, strategy_id=decision.strategy_id,
        batch_index=1, candidate_count=candidates, tested=candidates,
        recovered=len(recovered_ids), duration=0.1, status=TaskStatus.COMPLETED,
        successful_candidates=hits, recovered_target_ids=recovered_ids,
        offered_count=candidates,
    )


@pytest.mark.parametrize("name", POLICIES)
def test_replay_is_deterministic_and_preserves_target_counts(name):
    first = ReplayEnvironment(scenario(jitter=0.3), name).run()
    second = ReplayEnvironment(scenario(jitter=0.3), name).run()
    assert first == second
    assert first["recovered_targets"] == 2
    # Seven unique tokens in the union, including four unique tokens in A.
    assert first["submitted_candidates"] == 7
    assert first["tested_candidates"] == 7
    assert first["stop_reason"] == "candidates_exhausted"
    assert sum(item["recovered_targets"] for item in first["arm_statistics"].values()) == 2
    assert sum(item["successful_candidates"] for item in first["arm_statistics"].values()) == 1


@pytest.mark.parametrize("name", POLICIES)
def test_json_snapshot_resume_matches_uninterrupted_execution(name):
    config = scenario(jitter=0.2, batch_size=1)
    expected = ReplayEnvironment(config, name).run()
    partial = ReplayEnvironment(config, name)
    partial.run(max_steps=1)
    saved = json.loads(json.dumps(partial.snapshot()))
    resumed = ReplayEnvironment(config, name)
    resumed.restore(saved)
    assert resumed.run() == expected


@pytest.mark.parametrize("name", POLICIES)
def test_global_candidate_budget_truncates_batch(name):
    environment = ReplayEnvironment(scenario(budget=3), name)
    report = environment.run()
    assert report["submitted_candidates"] == 3
    assert report["stop_reason"] == "candidate_budget"
    assert [event["outcome"]["candidate_count"] for event in report["trace"]] == [2, 1]


def test_timeout_accounts_submitted_candidates_separately_from_tested():
    environment = ReplayEnvironment(scenario(seconds=0.35, batch_size=3), "fixed")
    report = environment.run()
    assert report["submitted_candidates"] == 3
    assert report["tested_candidates"] == 1
    assert report["recovered_targets"] == 2
    assert report["simulated_seconds"] == pytest.approx(0.35)
    assert report["stop_reason"] == "time_budget"


def test_startup_only_timeout_makes_no_recovery():
    report = ReplayEnvironment(scenario(seconds=0.05), "fixed").run()
    assert report["tested_candidates"] == 0
    assert report["recovered_targets"] == 0
    assert report["submitted_candidates"] == 2
    assert report["simulated_seconds"] == pytest.approx(0.05)


def test_partial_batch_cursor_preserves_unselected_suffix():
    environment = ReplayEnvironment(scenario(budget=1, batch_size=3), "fixed")
    environment.step()
    saved = environment.snapshot()
    assert saved["cursors"]["a"] == 1
    assert saved["submitted_ids"] == ["x"]


def test_multiple_arms_share_business_strategy_and_reject_double_select():
    config = scenario()
    policy = BaselinePolicy("round_robin", config.arms, total_candidate_budget=8, total_time_budget=20)
    first = policy.select({"a": 1, "b": 1})
    assert first.strategy_id == "S3"
    with pytest.raises(ValueError, match="pending"):
        policy.select({"a": 1, "b": 1})
    policy.observe_outcome("a", feedback(first))
    saved = json.loads(json.dumps(policy.snapshot()))
    restored = BaselinePolicy("round_robin", config.arms, total_candidate_budget=8, total_time_budget=20)
    restored.restore(saved)
    second = restored.select({"a": 1, "b": 1})
    assert (first.arm_id, second.arm_id) == ("a", "b")
    assert second.strategy_id == "S3"


def test_target_ids_cannot_be_credited_twice():
    config = scenario()
    policy = BaselinePolicy("fixed", config.arms, total_candidate_budget=8, total_time_budget=20)
    decision = policy.select({"a": 1})
    policy.observe_outcome("a", feedback(decision, recovered_ids=("u1", "u2"), hits=1))
    decision = policy.select({"a": 1})
    with pytest.raises(ValueError, match="globally unique"):
        policy.observe_outcome("a", feedback(decision, recovered_ids=("u1",), hits=1))


def test_round_robin_public_snapshot_preserves_cursor_and_legacy_shape():
    arms = [ArmSpec("S1", 1, 4, 4), ArmSpec("S2", 2, 4, 4)]
    policy = RoundRobinScheduler(arms, total_candidate_budget=8, total_time_budget=8)
    assert policy.select({"S1": 1, "S2": 1}).strategy_id == "S1"
    saved = json.loads(json.dumps(policy.snapshot()))
    assert "recovered" in saved["S1"]
    restored = RoundRobinScheduler(arms, total_candidate_budget=8, total_time_budget=8)
    restored.restore(saved)
    assert restored.select({"S1": 1, "S2": 1}).strategy_id == "S2"
    restored.restore(policy.snapshot_statistics())
    assert restored.select({"S1": 1, "S2": 1}).strategy_id == "S1"


def test_snapshot_rejects_corruption_without_modifying_environment():
    environment = ReplayEnvironment(scenario(), "round_robin")
    environment.step()
    valid = environment.snapshot()
    damaged = deepcopy(valid)
    damaged["cursors"]["a"] += 1
    with pytest.raises(ValueError, match="contents"):
        environment.restore(damaged)
    assert environment.snapshot() == valid
    changed = ReplayEnvironment(scenario(budget=7), "round_robin")
    with pytest.raises(ValueError, match="incompatible"):
        changed.restore(valid)


def test_snapshot_rejects_another_policy():
    saved = ReplayEnvironment(scenario(), "fixed").snapshot()
    with pytest.raises(ValueError, match="policy differs"):
        ReplayEnvironment(scenario(), "round_robin").restore(saved)


def test_all_targets_recovered_stops_at_batch_boundary():
    raw = scenario().as_dict()
    raw["target_ids"] = ["u1", "u2"]
    report = ReplayEnvironment(ReplayScenario.from_dict(raw), "fixed").run()
    assert report["stop_reason"] == "all_targets_recovered"
    assert report["rounds"] == 1
    assert report["submitted_candidates"] == 2


def test_zero_local_budgets_and_empty_streams_stop_cleanly():
    raw = scenario().as_dict()
    for arm in raw["arms"]:
        arm["candidate_budget"] = 0
    report = ReplayEnvironment(ReplayScenario.from_dict(raw), "fixed").run()
    assert report["stop_reason"] == "strategy_budgets"
    assert report["rounds"] == 0
    raw["candidate_streams"] = {"a": [], "b": []}
    raw["matches"] = {}
    report = ReplayEnvironment(ReplayScenario.from_dict(raw), "fixed").run()
    assert report["stop_reason"] == "candidates_exhausted"


@pytest.mark.parametrize("name", POLICIES)
def test_local_candidate_and_time_limits_are_independent_of_global_limits(name):
    raw = scenario().as_dict()
    raw["arms"][0]["candidate_budget"] = 1
    raw["arms"][1]["time_budget"] = 0.05
    report = ReplayEnvironment(ReplayScenario.from_dict(raw), name).run()
    assert report["arm_statistics"]["a"]["allocated_candidates"] == 1
    assert report["arm_statistics"]["b"]["time_cost"] == pytest.approx(0.05)
    assert report["arm_statistics"]["b"]["tested"] == 0
    assert report["stop_reason"] == "strategy_budgets"


def test_report_and_trace_are_defensive_copies():
    environment = ReplayEnvironment(scenario(), "fixed")
    first = environment.step()
    first["outcome"]["recovered"] = 999
    report = environment.report()
    report["trace"].clear()
    assert environment.trace[0]["outcome"]["recovered"] == 2


def test_policy_rejects_invalid_feedback_before_updating_statistics():
    config = scenario()
    policy = BaselinePolicy("fixed", config.arms, total_candidate_budget=8, total_time_budget=20)
    decision = policy.select({"a": 1})
    before = policy.statistics()
    with pytest.raises(ValueError, match="submitted candidate limit"):
        policy.observe_outcome("a", feedback(decision, candidates=2))
    assert policy.statistics() == before
    policy.observe_outcome("a", feedback(decision))
    assert policy.statistics()["a"]["allocated_candidates"] == 1


def test_report_does_not_include_candidate_tokens_or_match_table():
    raw = scenario().as_dict()
    raw["candidate_streams"] = {"a": ["PRIVATE-CANDIDATE-ONE"], "b": ["PRIVATE-CANDIDATE-TWO"]}
    raw["matches"] = {"PRIVATE-CANDIDATE-ONE": ["u1"]}
    report = ReplayEnvironment(ReplayScenario.from_dict(raw), "fixed").run()
    serialized = json.dumps(report)
    assert "PRIVATE-CANDIDATE" not in serialized
    assert "matches" not in report


@pytest.mark.parametrize("key,value", [("batch_size", 0), ("total_candidate_budget", True), ("total_time_budget", float("nan")), ("time_jitter", 1)])
def test_invalid_scenario_parameters_fail_early(key, value):
    raw = scenario().as_dict()
    raw[key] = value
    with pytest.raises(ValueError):
        ReplayScenario.from_dict(raw)


def test_unimplemented_algorithms_fail_explicitly():
    with pytest.raises(ValueError, match="milestone"):
        ReplayEnvironment(scenario(), "ucb")


def test_bundled_fixture_compares_the_same_scenario():
    path = Path(__file__).resolve().parents[1] / "examples/replay/baseline-small.json"
    config = ReplayScenario.from_dict(json.loads(path.read_text(encoding="utf-8")))
    reports = compare_baselines(config)
    assert tuple(reports) == POLICIES
    assert len({report["scenario_fingerprint"] for report in reports.values()}) == 1
    assert all(report["submitted_candidates"] <= 12 for report in reports.values())
