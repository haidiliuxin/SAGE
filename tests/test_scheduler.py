from __future__ import annotations

import time

import pytest

from sage_pass.candidate_generator import CandidateBatch
from sage_pass.enums import PlannerType, StrategyId, TaskStatus
from sage_pass.hashcat_adapter import (
    HashcatJob,
    HashcatResult,
    RecoveredCredential,
)
from sage_pass.scheduler import (
    ArmSpec,
    BanditScheduler,
    SchedulerStopReason,
)
from sage_pass.schemas import StrategyItem, StrategyPlan


def _observe(
    scheduler: BanditScheduler,
    strategy_id: str,
    *,
    recovered: int = 0,
    duration: float = 1.0,
    candidate_count: int = 2,
) -> None:
    scheduler.observe(
        strategy_id,
        candidate_count=candidate_count,
        tested=candidate_count,
        recovered=recovered,
        duration=duration,
    )


def test_bandit_explores_every_arm_then_selects_the_higher_gain_arm():
    scheduler = BanditScheduler(
        [
            ArmSpec("S1", priority=1, candidate_budget=6, time_budget=10),
            ArmSpec("S2", priority=2, candidate_budget=6, time_budget=10),
        ],
        total_candidate_budget=12,
        total_time_budget=20,
    )
    available = {"S1": 2, "S2": 2}

    first = scheduler.select(available)
    assert first is not None
    assert first.strategy_id == "S1"
    assert first.exploration is True
    _observe(scheduler, "S1")

    second = scheduler.select(available)
    assert second is not None
    assert second.strategy_id == "S2"
    assert second.exploration is True
    _observe(scheduler, "S2", recovered=1)

    third = scheduler.select(available)
    assert third is not None
    assert third.strategy_id == "S2"
    assert third.exploration is False
    assert third.score.score > scheduler.score("S1", 2).score

    productive = scheduler.statistics("S2")
    assert productive.tested == 2
    assert productive.recovered == 1
    assert productive.success_rate == 0.5
    assert productive.recent_gain == 1.0


def test_bandit_truncates_a_batch_at_the_total_candidate_budget():
    scheduler = BanditScheduler(
        [
            ArmSpec("S1", priority=1, candidate_budget=5, time_budget=10),
            ArmSpec("S2", priority=2, candidate_budget=5, time_budget=10),
        ],
        total_candidate_budget=6,
        total_time_budget=20,
    )

    first = scheduler.select({"S1": 4, "S2": 4})
    assert first is not None
    assert first.candidate_limit == 4
    _observe(scheduler, "S1", candidate_count=4)

    second = scheduler.select({"S1": 1, "S2": 4})
    assert second is not None
    assert second.strategy_id == "S2"
    assert second.candidate_limit == 2
    _observe(scheduler, "S2", candidate_count=2)

    available = {"S1": 1, "S2": 2}
    assert scheduler.select(available) is None
    assert scheduler.stop_reason(available) == SchedulerStopReason.CANDIDATE_BUDGET


def test_score_rewards_gain_and_penalizes_consumed_time():
    scheduler = BanditScheduler(
        [
            ArmSpec(
                "S1", priority=1, candidate_budget=200, time_budget=10,
                transfer_score=0.5,
            ),
            ArmSpec(
                "S2", priority=2, candidate_budget=200, time_budget=10,
                transfer_score=0.5,
            ),
        ],
        total_candidate_budget=400,
        total_time_budget=20,
    )
    scheduler.observe(
        "S1", candidate_count=100, tested=100, recovered=1, duration=1
    )
    scheduler.observe(
        "S2", candidate_count=100, tested=100, recovered=1, duration=8
    )

    fast = scheduler.score("S1", 100)
    slow = scheduler.score("S2", 100)
    assert fast.success_probability == slow.success_probability
    assert fast.recent_gain == slow.recent_gain
    assert fast.transfer == slow.transfer
    assert fast.cost < slow.cost
    assert fast.score > slow.score


def test_bandit_stops_on_time_budget_or_exhausted_candidates():
    scheduler = BanditScheduler(
        [ArmSpec("S1", priority=1, candidate_budget=10, time_budget=5)],
        total_candidate_budget=10,
        total_time_budget=1,
    )
    decision = scheduler.select({"S1": 5})
    assert decision is not None
    assert decision.time_limit == 1
    _observe(scheduler, "S1", duration=1, candidate_count=5)
    assert scheduler.select({"S1": 5}) is None
    assert scheduler.stop_reason({"S1": 5}) == SchedulerStopReason.TIME_BUDGET

    empty = BanditScheduler(
        [ArmSpec("S1", priority=1, candidate_budget=10, time_budget=5)],
        total_candidate_budget=10,
        total_time_budget=5,
    )
    assert empty.select({"S1": 0}) is None
    assert empty.stop_reason({"S1": 0}) == SchedulerStopReason.CANDIDATES_EXHAUSTED

    limited = BanditScheduler(
        [ArmSpec("S1", priority=1, candidate_budget=2, time_budget=1)],
        total_candidate_budget=10,
        total_time_budget=10,
    )
    _observe(limited, "S1", duration=1)
    assert limited.select({"S1": 2}) is None
    assert limited.stop_reason({"S1": 2}) == SchedulerStopReason.STRATEGY_BUDGETS


class _StaticPlanner:
    def plan(self, prir) -> StrategyPlan:
        return StrategyPlan(
            task_id=prir.task_id,
            planner_type=PlannerType.ADAPTIVE,
            total_time_budget=20,
            strategies=[
                StrategyItem(
                    strategy_id=StrategyId.S1,
                    strategy_name="Baseline",
                    priority=1,
                    time_budget=10,
                    candidate_budget=6,
                    reason="test",
                ),
                StrategyItem(
                    strategy_id=StrategyId.S2,
                    strategy_name="Rule",
                    priority=2,
                    time_budget=10,
                    candidate_budget=6,
                    reason="test",
                    parameters={"all_upper": True},
                ),
            ],
        )


class _ScriptedCandidates:
    def iter_plan_batches(self, plan, **kwargs):
        del plan, kwargs
        for strategy_id, prefix in ((StrategyId.S1, "cold"), (StrategyId.S2, "hot")):
            for index in range(3):
                values = (f"{prefix}-{index}-a", f"{prefix}-{index}-b")
                yield CandidateBatch(strategy_id, values)


class _ImmediateHandle:
    def __init__(self, result: HashcatResult) -> None:
        self.result = result

    def wait(self) -> HashcatResult:
        return self.result

    def stop(self) -> HashcatResult:
        return self.result


class _FeedbackHashcat:
    def __init__(self) -> None:
        self.jobs: list[HashcatJob] = []

    def start(self, job: HashcatJob) -> _ImmediateHandle:
        self.jobs.append(job)
        recovered = ()
        if job.candidates[0].startswith("hot"):
            recovered = (
                RecoveredCredential(
                    target=job.target_hashes[0],
                    plaintext=job.candidates[0],
                ),
            )
        return _ImmediateHandle(HashcatResult(
            status=TaskStatus.COMPLETED,
            duration=0.1,
            tested=len(job.candidates),
            recovered=recovered,
            exit_code=0,
            message="done",
            stdout="",
            stderr="",
        ))


def test_real_executor_uses_batch_feedback_and_preserves_result_contract(client):
    planner = _StaticPlanner()
    hashcat = _FeedbackHashcat()
    client.app.state.planner = planner
    client.app.state.real_executor.hashcat = hashcat
    client.app.state.real_executor.candidate_generator = _ScriptedCandidates()

    created = client.post("/api/tasks", json={
        "name": "bandit-executor",
        "target": {
            "type": "hash",
            "content": "0123456789abcdef0123456789abcdef",
            "file_id": None,
        },
        "known_algorithm": "md5",
        "time_budget": 20,
        "candidate_budget": 12,
        "context": {},
    })
    task_id = created.json()["task_id"]
    assert client.post(f"/api/tasks/{task_id}/analyze").status_code == 200
    assert client.post(f"/api/tasks/{task_id}/plan").status_code == 200

    started = client.post(
        f"/api/tasks/{task_id}/execute", json={"mode": "real"}
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get(f"/api/runs/{run_id}/status").json()
        if status["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.01)
    else:
        pytest.fail("adaptive real execution did not finish")

    result = client.get(f"/api/runs/{run_id}/result")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "completed"
    assert body["total_tested"] == 12
    assert [job.candidates[0].split("-", 1)[0] for job in hashcat.jobs] == [
        "cold", "hot", "hot", "hot", "cold", "cold"
    ]
    statistics = {
        item["strategy_id"]: item for item in body["strategy_results"]
    }
    assert statistics["S1"]["tested"] == 6
    assert statistics["S1"]["recovered"] == 0
    assert statistics["S1"]["time"] == pytest.approx(0.3)
    assert statistics["S1"]["success_rate"] == 0
    assert statistics["S2"]["tested"] == 6
    assert statistics["S2"]["recovered"] == 3
    assert statistics["S2"]["time"] == pytest.approx(0.3)
    assert statistics["S2"]["success_rate"] == 0.5
