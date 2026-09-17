"""UCB milestone acceptance tests; project owner runs these in the sage env."""

import json
import math
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace

import pytest

from sage_pass.decision.costs import OnlineCostModel, UCBConfig
from sage_pass.decision.research_log import SqliteResearchLog
from sage_pass.enums import SchedulerType, TaskStatus
from sage_pass.experiments.replay import ReplayEnvironment, ReplayScenario, compare_baselines
from sage_pass.hashcat_adapter import HashcatResult, RecoveredCredential
from sage_pass.real_executor import _scheduler_from_snapshot, _serialize_progress
from sage_pass.scheduler import ArmSpec, build_scheduler, decision_diagnostics

from test_decision_replay import scenario
from test_real_research_logging import configured_executor


def scheduler(*, aware=False, config=None, targets=20, budget=100):
    return build_scheduler(
        SchedulerType.COST_AWARE_UCB if aware else SchedulerType.UCB,
        [ArmSpec("a", 1, 100, 100), ArmSpec("b", 2, 100, 100)],
        total_candidate_budget=budget, total_time_budget=100,
        initial_targets=targets, ucb_config=config,
    )


def observe(policy, arm, *, recovered=0, duration=1, count=1, tested=None, complete=True):
    policy.observe(arm, candidate_count=count, tested=count if tested is None else tested,
                   recovered=recovered, duration=duration, complete=complete)


def test_ucb1_formula_cold_start_and_stable_ties():
    policy = scheduler()
    first = policy.select({"a": 1, "b": 1})
    assert first.strategy_id == "a" and first.exploration
    assert first.score.untried == 1
    observe(policy, "a", recovered=2)
    second = policy.select({"a": 1, "b": 1})
    assert second.strategy_id == "b" and second.exploration
    observe(policy, "b", recovered=2)
    third = policy.select({"a": 1, "b": 1})
    assert third.strategy_id == "a" and not third.exploration
    assert third.score.mean_reward == pytest.approx(0.1)
    assert third.score.exploration_bonus == pytest.approx(math.sqrt(2 * math.log(2)))
    assert third.score.score == pytest.approx(0.1 + math.sqrt(2 * math.log(2)))
    json.dumps(policy.snapshot(), allow_nan=False)


def test_cost_ratio_selects_faster_arm_after_equal_rewards():
    plain, aware = scheduler(), scheduler(aware=True)
    for policy in (plain, aware):
        observe(policy, "a", recovered=1, duration=10)
        observe(policy, "b", recovered=1, duration=1)
    assert plain.select({"a": 1, "b": 1}).strategy_id == "a"
    selected = aware.select({"a": 1, "b": 1})
    assert selected.strategy_id == "b"
    assert aware.score("a", 1).score * 10 == pytest.approx(selected.score.score)


def test_cost_fit_learns_intercept_and_slope_from_variable_batches():
    model = OnlineCostModel(UCBConfig())
    assert model.predict(100) == 1
    for count in (10, 20, 40):
        model.observe(count, 2 + 0.5 * count, complete=True)
    assert model.coefficients() == pytest.approx((2, 0.5))
    assert model.predict(6) == pytest.approx(5)
    restored = OnlineCostModel.from_snapshot(model.config, json.loads(json.dumps(model.snapshot())))
    assert restored.predict(6) == model.predict(6)


def test_equal_batch_sizes_use_documented_intercept_prior():
    model = OnlineCostModel(UCBConfig(startup_prior=2))
    model.observe(10, 7, complete=True)
    model.observe(10, 7, complete=True)
    assert model.coefficients() == pytest.approx((2, 0.5))


def test_negative_unconstrained_slope_is_projected_to_nonnegative_fit():
    model = OnlineCostModel(UCBConfig())
    model.observe(10, 10, complete=True)
    model.observe(20, 5, complete=True)
    assert model.coefficients() == pytest.approx((7.5, 0))


def test_partial_and_failed_batches_do_not_train_false_low_costs():
    policy = scheduler(aware=True)
    observe(policy, "a", count=10, duration=10)
    expected = policy.score("a", 10).predicted_seconds
    observe(policy, "a", count=10, tested=1, duration=0.1)
    observe(policy, "a", count=10, duration=0.1, complete=False)
    score = policy.score("a", 10)
    assert score.predicted_seconds == expected
    assert score.cost_samples == 1 and score.censored_samples == 2
    assert policy.statistics("a").allocated_candidates == 30
    assert policy.statistics("a").time_cost == pytest.approx(10.2)


def test_zero_duration_prediction_is_positive_and_finite():
    policy = scheduler(aware=True)
    observe(policy, "a", recovered=1, duration=0)
    score = policy.score("a", 1)
    assert score.predicted_seconds == policy.config.minimum_cost
    assert math.isfinite(score.score)


def test_exhaustion_and_effective_budget_sizes():
    policy = scheduler(aware=True, budget=3)
    observe(policy, "a", count=2)
    info = decision_diagnostics(policy, {"a": 0, "b": 10})
    assert info["available"] == {"a": 0, "b": 1}
    selected = policy.select({"a": 0, "b": 10})
    assert selected.candidate_limit == 1
    assert selected.score == info["scores"]["b"]
    observe(policy, "b")
    assert policy.select({"a": 10, "b": 10}) is None
    assert policy.stop_reason({"a": 10, "b": 10}) == "candidate_budget"


def test_actual_time_overrun_is_charged_and_restorable():
    policy = scheduler()
    observe(policy, "a", duration=101, tested=0, complete=False)
    assert policy.select({"a": 1, "b": 1}) is None
    restored = scheduler()
    restored.restore(policy.snapshot())
    assert restored.stop_reason({"a": 1, "b": 1}) == "time_budget"


@pytest.mark.parametrize("aware", [False, True])
def test_scheduler_snapshot_restores_cost_and_next_choice(aware):
    policy = scheduler(aware=aware)
    observe(policy, "a", count=5, duration=2)
    observe(policy, "b", count=10, duration=3, recovered=2)
    saved = json.loads(json.dumps(policy.snapshot()))
    restored = scheduler(aware=aware)
    restored.restore(saved)
    assert restored.select({"a": 5, "b": 5}) == policy.select({"a": 5, "b": 5})
    assert restored.snapshot() == policy.snapshot()
    corrupted = deepcopy(saved)
    corrupted["cost_models"]["a"]["samples"] += 1
    before = restored.snapshot()
    with pytest.raises(ValueError):
        restored.restore(corrupted)
    assert restored.snapshot() == before
    with pytest.raises(ValueError):
        scheduler(aware=aware, config=UCBConfig(startup_prior=2)).restore(saved)


@pytest.mark.parametrize("name", ["ucb", "cost_aware_ucb"])
def test_replay_resume_determinism_and_research_fields(name, tmp_path):
    config = scenario(batch_size=1, jitter=0.2)
    store = SqliteResearchLog(tmp_path / "ucb.sqlite3")
    expected = ReplayEnvironment(config, name).run()
    partial = ReplayEnvironment(config, name, research_log=store)
    partial.run(max_steps=2)
    saved = json.loads(json.dumps(partial.snapshot()))
    resumed = ReplayEnvironment(config, name, research_log=store)
    resumed.restore(saved)
    assert resumed.run() == expected
    assert expected["recovered_targets"] == 2  # one candidate recovers two targets
    completed = [event for event in store.events() if event["event_type"] == "decision_completed"]
    assert len(completed) == expected["rounds"]
    for event in completed:
        payload = event["payload"]
        assert "reward_breakdown" in payload
        for score in payload["scores"].values():
            assert {"mean_reward", "exploration_bonus", "predicted_seconds", "untried"} <= score.keys()
    with pytest.raises(ValueError):
        ReplayEnvironment(config, name, ucb_config=UCBConfig(startup_prior=2)).restore(saved)


def test_comparison_keeps_legacy_library_default_and_adds_five_policy_option():
    assert len(compare_baselines(scenario())) == 3
    reports = compare_baselines(scenario(), include_ucb=True)
    assert tuple(reports) == ("fixed", "round_robin", "heuristic_bandit", "ucb", "cost_aware_ucb")
    assert len({item["scenario_fingerprint"] for item in reports.values()}) == 1


def test_synthetic_cost_fixture_does_not_leak_true_costs_to_policy():
    path = Path(__file__).resolve().parents[1] / "examples/replay/cost-aware-small.json"
    config = ReplayScenario.from_dict(json.loads(path.read_text(encoding="utf-8")))
    environment = ReplayEnvironment(config, "cost_aware_ucb")
    first = environment.step()
    initial = first["research"]["scores"]
    assert initial["slow"]["predicted_seconds"] == initial["fast"]["predicted_seconds"] == 0.01
    second = environment.step()
    assert second["decision"]["arm_id"] == "fast"
    third = environment.step()
    assert third["decision"]["arm_id"] == "fast"
    assert third["research"]["scores"]["slow"]["predicted_seconds"] == pytest.approx(2.5)


def test_rejected_target_feedback_does_not_change_learning_state():
    policy = scheduler(targets=2)
    observe(policy, "a", recovered=2)
    before = policy.snapshot()
    with pytest.raises(ValueError):
        observe(policy, "b", recovered=1)
    assert policy.snapshot() == before


@pytest.mark.parametrize("name", ["ucb", "cost_aware_ucb"])
def test_real_executor_ucb_target_accounting_and_full_restore(name, client, tmp_path, monkeypatch):
    executor, state, store = configured_executor(client, tmp_path, monkeypatch, count=3)
    state.targets = ("hash-secret", "second-secret")
    state.scheduler = build_scheduler(name, [ArmSpec("S1", 1, 3, 100)],
                                     total_candidate_budget=3, total_time_budget=100, initial_targets=2)
    def start(job):
        result = HashcatResult(TaskStatus.COMPLETED, 0.01, 1,
                              tuple(RecoveredCredential(target, job.candidates[0]) for target in state.targets),
                              0, "", "", "")
        return SimpleNamespace(wait=lambda: result)
    executor.hashcat = SimpleNamespace(start=start)
    executor._run_worker(state.run_id)
    assert state.status == TaskStatus.COMPLETED
    assert state.scheduler.statistics("S1").recovered == 2
    events = [e for e in store.events() if e["event_type"] == "decision_completed"]
    assert [e["payload"]["feedback"]["recovered"] for e in events] == [2, 0, 0]
    progress = _serialize_progress(state)
    snapshot = {"targets": list(state.targets), "scheduler_type": name,
                "plan": {"total_candidate_budget": 3, "total_time_budget": 100}}
    restored = _scheduler_from_snapshot(snapshot, progress, state.strategies)
    assert restored.snapshot() == state.scheduler.snapshot()
    del progress["scheduler_snapshot"]
    with pytest.raises(ValueError, match="full scheduler snapshot"):
        _scheduler_from_snapshot(snapshot, progress, state.strategies)


@pytest.mark.parametrize("params", [{"minimum_cost": 0}, {"exploration_coefficient": float("nan")},
                                   {"seconds_per_candidate_prior": -1}, {"startup_prior": True}])
def test_config_rejects_invalid_numbers(params):
    with pytest.raises(ValueError):
        UCBConfig(**params)
