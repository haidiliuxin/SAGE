"""Milestone 4 acceptance cases; no real verifier or network required."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from sage_pass.decision import DecisionArm
from sage_pass.decision.research_log import ResearchRecorder, SqliteResearchLog
from sage_pass.decision.rewards import RewardContext, RewardWeights, calculate_reward
from sage_pass.enums import TaskStatus
from sage_pass.experiments.replay import ReplayEnvironment, ReplayScenario
from sage_pass.interfaces import BatchOutcome
from sage_pass.scheduler import ArmSpec, RoundRobinScheduler, decision_diagnostics


def outcome(**changes):
    base = BatchOutcome("run", "a", "a", 1, 1, 1, 2, 2.0, TaskStatus.COMPLETED)
    return replace(base, **changes)


def scenario(count=301):
    return ReplayScenario(
        arms=(DecisionArm("a", "S1", "fixture", 1, count, 100),),
        candidate_streams={"a": tuple(f"candidate-secret-{i}" for i in range(count))},
        target_ids=("target-secret", "unreachable-secret"),
        matches={"candidate-secret-0": ("target-secret",)},
        total_candidate_budget=count, total_time_budget=100, batch_size=1,
    )


def test_reward_counts_targets_and_separates_costs():
    reward = calculate_reward(outcome(), RewardContext(10, 100, 20),
                              duplicate_count=1, offered_count=2)
    assert reward.recovery_gain == pytest.approx(0.2)
    assert reward.time_penalty == pytest.approx(0.01)
    assert reward.candidate_penalty == pytest.approx(0.001)
    assert reward.duplicate_penalty == pytest.approx(0.05)
    assert reward.total == pytest.approx(0.139)
    assert reward.gain_per_thousand_candidates == 2000
    assert reward.gain_per_second == 1


def test_unmeasured_duplicates_and_zero_time_are_explicit():
    reward = calculate_reward(outcome(duration=0), RewardContext(10, 100, 20))
    assert reward.duplicate_fraction is None
    assert reward.duplicate_penalty is None
    assert reward.duplicate_measurement_available is False
    assert reward.gain_per_second is None
    assert reward.total == pytest.approx(0.199)


def test_negative_reward_and_time_overrun_are_not_clipped():
    reward = calculate_reward(outcome(recovered=0, duration=40), RewardContext(10, 100, 20))
    assert reward.time_fraction == 2
    assert reward.total == pytest.approx(-0.201)


@pytest.mark.parametrize("changes", [
    {"tested": 2}, {"recovered": 11}, {"tested": 0},
    {"duration": float("nan")}, {"duration": -1}, {"candidate_count": True},
])
def test_invalid_feedback_is_rejected(changes):
    with pytest.raises(ValueError):
        calculate_reward(outcome(**changes), RewardContext(10, 100, 20))


@pytest.mark.parametrize("weights", [{"time": -1}, {"recovery": 0}, {"duplicates": float("inf")}])
def test_invalid_weights_are_rejected(weights):
    with pytest.raises(ValueError):
        RewardWeights(**weights)


def test_long_replay_persists_every_round_and_exports_no_source_text(tmp_path):
    path = tmp_path / "events.sqlite3"
    report = ReplayEnvironment(scenario(), "fixed", research_log=SqliteResearchLog(path)).run()
    events = list(SqliteResearchLog(path).events())
    completed = [e for e in events if e["event_type"] == "decision_completed"]
    assert [e["round_index"] for e in completed] == list(range(1, 302))
    assert len(events) == 604  # run start/end plus 301 start/completion pairs
    assert report["total_reward"] == pytest.approx(sum(e["payload"]["reward"] for e in completed))
    export = tmp_path / "events.jsonl"
    assert SqliteResearchLog(path).export_jsonl(export) == len(events)
    exported = export.read_text(encoding="utf-8")
    assert [json.loads(line) for line in exported.splitlines()] == events
    assert "candidate-secret" not in exported
    assert "target-secret" not in exported
    assert "unreachable-secret" not in exported
    with pytest.raises(FileExistsError):
        SqliteResearchLog(path).export_jsonl(export)


def test_restore_does_not_duplicate_history_and_freezes_weights(tmp_path):
    store = SqliteResearchLog(tmp_path / "resume.sqlite3")
    first = ReplayEnvironment(scenario(5), "round_robin", research_log=store)
    first.run(max_steps=2)
    checkpoint = json.loads(json.dumps(first.snapshot()))
    first.mark_checkpoint()
    before = list(store.events())
    resumed = ReplayEnvironment(scenario(5), "round_robin", research_log=store)
    resumed.restore(checkpoint)
    assert list(store.events()) == before
    assert resumed.run() == ReplayEnvironment(scenario(5), "round_robin").run()
    events = list(store.events())
    completed = [e for e in events if e["event_type"] == "decision_completed"]
    assert [e["round_index"] for e in completed] == [1, 2, 3, 4, 5]
    assert len({e["attempt_id"] for e in completed}) == 2
    changed = ReplayEnvironment(scenario(5), "round_robin", reward_weights=RewardWeights(time=0.2))
    with pytest.raises(ValueError, match="reward configuration"):
        changed.restore(checkpoint)
    with pytest.raises(ValueError, match="incompatible"):
        resumed.restore({**checkpoint, "version": 1})


def test_diagnostics_are_read_only_and_use_effective_batch_size():
    scheduler = RoundRobinScheduler(
        [ArmSpec("a", 1, 10, 10), ArmSpec("b", 2, 10, 10)],
        total_candidate_budget=2, total_time_budget=10,
    )
    before = scheduler.snapshot()
    diagnostics = decision_diagnostics(scheduler, {"a": 8, "b": 8})
    assert scheduler.snapshot() == before
    assert diagnostics["available"] == {"a": 2, "b": 2}
    decision = scheduler.select({"a": 8, "b": 8})
    assert decision.strategy_id == "a"
    assert decision.score == diagnostics["scores"]["a"]


def recorder_with_decision(tmp_path):
    store = SqliteResearchLog(tmp_path / "events.sqlite3")
    recorder = ResearchRecorder(run_id="run", policy_type="fixed", context=RewardContext(10, 10, 10), store=store)
    scheduler = RoundRobinScheduler([ArmSpec("a", 1, 10, 10)], total_candidate_budget=10, total_time_budget=10)
    decision = scheduler.select({"a": 1})
    recorder.begin(decision=decision, available={"a": 1}, scores={"a": decision.score},
                   prior_state={"a": {"tested": 0, "message": "hidden-secret"}})
    return recorder, store


def test_interrupt_retains_started_decision_without_fabricating_feedback(tmp_path):
    recorder, store = recorder_with_decision(tmp_path)
    assert [e["event_type"] for e in store.events()] == ["run_started", "decision_started"]
    recorder.finish("execution_failed", state={"a": {"tested": 0}})
    events = list(store.events())
    assert events[-2]["event_type"] == "decision_interrupted"
    assert events[-2]["payload"]["reward"] is None
    assert events[-2]["payload"]["feedback"] is None
    assert "hidden-secret" not in json.dumps(events)


def test_feedback_projection_drops_messages_and_raw_state(tmp_path):
    recorder, store = recorder_with_decision(tmp_path)
    logged = recorder.complete(outcome(message="plaintext-secret"),
                               updated_state={"a": {"tested": 1, "credentials": ["plaintext-secret"]}})
    assert "plaintext-secret" not in json.dumps(list(store.events()))
    assert logged["selection_probability"] == 1
    assert logged["selection_rule"] == "priority_order"
    assert logged["scores_used_for_selection"] is False


def test_concurrent_retry_is_idempotent_and_conflicts_preserve_original(tmp_path):
    recorder, store = recorder_with_decision(tmp_path)
    original = list(store.events())[0]
    event = {k: v for k, v in original.items() if k != "sequence"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: SqliteResearchLog(store.path)._append(event), range(12)))
    assert len(list(store.events())) == 2
    with pytest.raises(ValueError, match="conflicting"):
        store._append({**event, "payload": {"changed": True}})
    assert list(store.events())[0] == original


def test_cli_writes_default_log_and_applies_reward_config(tmp_path, monkeypatch):
    from sage_pass.experiments.__main__ import main

    source = tmp_path / "scenario.json"
    source.write_text(json.dumps(scenario(5).as_dict()), encoding="utf-8")
    weights = tmp_path / "weights.json"
    weights.write_text('{"recovery": 2, "time": 0, "candidates": 0, "duplicates": 0}', encoding="utf-8")
    output = tmp_path / "report.json"
    export = tmp_path / "events.jsonl"
    monkeypatch.setattr("sys.argv", ["replay", "--scenario", str(source), "--output", str(output),
                                   "--policy", "fixed", "--max-steps", "2",
                                   "--reward-weights", str(weights), "--export-research", str(export)])
    main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["total_reward"] == 1  # 2 * 1 new target / 2 initial targets
    events = list(SqliteResearchLog(output.with_suffix(".research.sqlite3"), read_only=True).events())
    assert len([event for event in events if event["event_type"] == "decision_completed"]) == 2
    assert all(event["event_type"] != "run_finished" for event in events)
    assert len(export.read_text(encoding="utf-8").splitlines()) == len(events)
