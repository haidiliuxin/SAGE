"""Exercise real executor boundaries using an in-process fake Hashcat."""

import json
from types import SimpleNamespace

from sage_pass.decision.research_log import SqliteResearchLog
from sage_pass.enums import TaskStatus
from sage_pass.errors import AppError
from sage_pass.hashcat_adapter import HashcatResult, RecoveredCredential
from sage_pass.real_executor import _RunState, _StrategyState, _serialize_progress
from sage_pass.scheduler import ArmSpec, FixedOrderScheduler


def configured_executor(client, tmp_path, monkeypatch, count=301):
    executor = client.app.state.real_executor
    store = SqliteResearchLog(tmp_path / "research.sqlite3")
    executor._research_log = store
    monkeypatch.setattr(executor, "_persist", lambda state: None)
    monkeypatch.setattr(executor.feedback_service, "process_completed_run", lambda **kwargs: None)
    item = _StrategyState(
        "S1", "fixture", 1, 100, count, {},
        candidate_batches=tuple((f"candidate-secret-{i}",) for i in range(count)),
    )
    state = _RunState("research-test", "task-test", ("hash-secret",), 0,
                      strategies=[item], expected_candidates=count)
    state.scheduler = FixedOrderScheduler(
        [ArmSpec("S1", 1, count, 100)], total_candidate_budget=count, total_time_budget=100,
    )
    executor._registry[state.run_id] = state
    return executor, state, store


def test_real_log_outlives_summary_and_deduplicates_recovery(client, tmp_path, monkeypatch):
    executor, state, store = configured_executor(client, tmp_path, monkeypatch)

    def start(job):
        events = list(store.events())
        assert events[-1]["event_type"] == "decision_started"
        # Different reported plaintext for the same target must not earn a new reward.
        result = HashcatResult(
            TaskStatus.COMPLETED, 0.01, 1,
            (RecoveredCredential("hash-secret", job.candidates[0]),),
            0, "message-secret", "stdout-secret", "stderr-secret",
        )
        return SimpleNamespace(wait=lambda: result)

    executor.hashcat = SimpleNamespace(start=start)
    executor._run_worker(state.run_id)
    assert state.status == TaskStatus.COMPLETED
    assert len(state.decision_events) == 200
    assert len(_serialize_progress(state)["decision_events"]) == 50
    events = list(store.events())
    completed = [e for e in events if e["event_type"] == "decision_completed"]
    assert len(completed) == 301
    assert [e["round_index"] for e in completed] == list(range(1, 302))
    assert sum(e["payload"]["feedback"]["recovered"] for e in completed) == 1
    assert completed[0]["payload"]["reward_breakdown"]["duplicate_measurement_available"] is False
    assert events[-1]["payload"]["stop_reason"] == "candidate_budget"
    serialized = json.dumps(events)
    for secret in ("hash-secret", "candidate-secret", "message-secret", "stdout-secret", "stderr-secret"):
        assert secret not in serialized
    assert all(event.feedback.message == "" for event in state.decision_events)


def test_launch_failure_keeps_pending_decision_with_unknown_reward(client, tmp_path, monkeypatch):
    executor, state, store = configured_executor(client, tmp_path, monkeypatch, count=1)

    def start(job):
        raise AppError("EXECUTION_FAILED", "secret-error", status_code=500)

    executor.hashcat = SimpleNamespace(start=start)
    executor._run_worker(state.run_id)
    assert state.status == TaskStatus.FAILED
    events = list(store.events())
    assert [e["event_type"] for e in events] == [
        "run_started", "decision_started", "decision_interrupted", "run_finished",
    ]
    assert events[-2]["payload"]["reward"] is None
    assert events[-1]["payload"]["stop_reason"] == "launch_failed"
    assert "secret-error" not in json.dumps(events)


def test_cancel_before_first_batch_records_a_terminal_event(client, tmp_path, monkeypatch):
    executor, state, store = configured_executor(client, tmp_path, monkeypatch, count=1)
    state.cancel_requested = True
    executor._run_worker(state.run_id)
    assert state.status == TaskStatus.CANCELLED
    assert [e["event_type"] for e in store.events()] == ["run_started", "run_finished"]


def test_log_failure_prevents_unlogged_execution(client, tmp_path, monkeypatch):
    executor, state, store = configured_executor(client, tmp_path, monkeypatch, count=1)
    launched = []
    executor.hashcat = SimpleNamespace(start=lambda job: launched.append(job))

    def fail(event):
        raise OSError("disk unavailable")

    monkeypatch.setattr(store, "_append", fail)
    executor._run_worker(state.run_id)
    assert launched == []
    assert state.status == TaskStatus.FAILED


def test_completion_log_failure_stops_before_next_batch(client, tmp_path, monkeypatch):
    executor, state, store = configured_executor(client, tmp_path, monkeypatch, count=2)
    launched = []
    append = store._append

    def fail_completion(event):
        if event["event_type"] == "decision_completed":
            raise OSError("disk unavailable")
        append(event)

    def start(job):
        launched.append(job)
        result = HashcatResult(TaskStatus.COMPLETED, 0.01, 1, (), 0, "", "", "")
        return SimpleNamespace(wait=lambda: result)

    monkeypatch.setattr(store, "_append", fail_completion)
    executor.hashcat = SimpleNamespace(start=start)
    executor._run_worker(state.run_id)
    assert len(launched) == 1
    assert state.status == TaskStatus.FAILED
    assert not any(e["event_type"] == "decision_completed" for e in store.events())
