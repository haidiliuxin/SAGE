from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from sage_pass.candidate_generator import CandidateBatch
from sage_pass.config import Settings
from sage_pass.database import Database
from sage_pass.enums import PlannerType, StrategyId, TaskStatus
from sage_pass.feedback import FeedbackConfig, FeedbackService
from sage_pass.hashcat_adapter import (
    HashcatJob,
    HashcatResult,
    RecoveredCredential,
)
from sage_pass.main import create_app
from sage_pass.models import FeedbackRunModel
from sage_pass.repository import PatternKnowledgeRepository
from sage_pass.scheduler import ArmSpec, BanditScheduler
from sage_pass.schemas import StrategyItem, StrategyPlan
from sage_pass.transfer import load_transfer_knowledge


MD5_HEX = "0123456789abcdef0123456789abcdef"


def _observe(scheduler, strategy_id, recovered):
    scheduler.observe(
        strategy_id,
        candidate_count=2,
        tested=2,
        recovered=recovered,
        duration=0.1,
    )


def test_s5_explores_then_high_gain_gets_more_batches():
    scheduler = BanditScheduler(
        [
            ArmSpec("S1", 1, 8, 10, transfer_score=0.2),
            ArmSpec("S5", 2, 8, 10, transfer_score=0.9),
        ],
        total_candidate_budget=16,
        total_time_budget=20,
    )
    available = {"S1": 2, "S5": 2}
    first = scheduler.select(available)
    assert first.strategy_id == "S1" and first.exploration
    _observe(scheduler, "S1", 0)
    second = scheduler.select(available)
    assert second.strategy_id == "S5" and second.exploration
    _observe(scheduler, "S5", 1)
    assert scheduler.select(available).strategy_id == "S5"


def test_s5_low_gain_does_not_keep_budget_from_productive_arm():
    scheduler = BanditScheduler(
        [
            ArmSpec("S1", 1, 8, 10, transfer_score=0.5),
            ArmSpec("S5", 2, 8, 10, transfer_score=0.5),
        ],
        total_candidate_budget=16,
        total_time_budget=20,
    )
    available = {"S1": 2, "S5": 2}
    assert scheduler.select(available).strategy_id == "S1"
    _observe(scheduler, "S1", 1)
    assert scheduler.select(available).strategy_id == "S5"
    _observe(scheduler, "S5", 0)
    assert scheduler.select(available).strategy_id == "S1"


class _StaticS5Planner:
    def plan(self, prir):
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
                    strategy_id=StrategyId.S5,
                    strategy_name="Transfer",
                    priority=2,
                    time_budget=10,
                    candidate_budget=6,
                    reason="test",
                ),
            ],
        )


class _ScriptedS5Candidates:
    def iter_plan_batches(self, plan, **kwargs):
        assert kwargs["transfer_patterns"]
        del plan
        for strategy_id, prefix in ((StrategyId.S1, "cold"), (StrategyId.S5, "hot")):
            for index in range(3):
                values = (f"{prefix}-{index}-a", f"{prefix}-{index}-b")
                yield CandidateBatch(strategy_id, values)


class _ImmediateHandle:
    def __init__(self, result):
        self.result = result

    def wait(self):
        return self.result

    def stop(self):
        return self.result


class _S5GainHashcat:
    def __init__(self):
        self.jobs: list[HashcatJob] = []

    def start(self, job: HashcatJob):
        self.jobs.append(job)
        recovered = ()
        if "-S5-" in job.run_id:
            recovered = (RecoveredCredential(
                target=job.target_hashes[0], plaintext=job.candidates[0]
            ),)
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


def _create_analyzed_task(client, name, budget=12):
    created = client.post("/api/tasks", json={
        "name": name,
        "target": {"type": "hash", "content": MD5_HEX, "file_id": None},
        "known_algorithm": "md5",
        "time_budget": 20,
        "candidate_budget": budget,
        "context": {},
    })
    task_id = created.json()["task_id"]
    assert client.post(f"/api/tasks/{task_id}/analyze").status_code == 200
    return task_id


def _wait_terminal(client, run_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}/status").json()
        if body["status"] in {"completed", "failed", "cancelled"}:
            return body
        time.sleep(0.01)
    pytest.fail("run did not reach terminal state")


def test_real_executor_s5_transfer_score_exploration_and_auto_feedback(client):
    executor = client.app.state.real_executor
    seed_a = _create_analyzed_task(client, "seed-a")
    seed_b = _create_analyzed_task(client, "seed-b")
    executor.feedback_service.process_completed_run(
        run_id="R-SEED-A",
        task_id=seed_a,
        recovered_items=[RecoveredCredential(MD5_HEX, "Admin2025!")],
    )
    executor.feedback_service.process_completed_run(
        run_id="R-SEED-B",
        task_id=seed_b,
        recovered_items=[RecoveredCredential(MD5_HEX, "Hello2026!")],
    )
    with client.app.state.database.session_factory() as session:
        _, before_summary = load_transfer_knowledge(
            PatternKnowledgeRepository(session),
            target_type="hash",
            algorithm="md5",
            candidate_budget=12,
            config=executor.feedback_config,
        )
    assert before_summary.available

    task_id = _create_analyzed_task(client, "s5-e2e")
    client.app.state.planner = _StaticS5Planner()
    fake = _S5GainHashcat()
    executor.hashcat = fake
    executor.candidate_generator = _ScriptedS5Candidates()
    assert client.post(f"/api/tasks/{task_id}/plan").status_code == 200
    started = client.post(
        f"/api/tasks/{task_id}/execute", json={"mode": "real"}
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    assert _wait_terminal(client, run_id)["status"] == "completed"

    order = ["S5" if "-S5-" in job.run_id else "S1" for job in fake.jobs]
    assert order[:2] == ["S1", "S5"]
    assert order[2] == "S5"
    state = executor._registry[run_id]
    assert state.scheduler.score("S5", 1).transfer == before_summary.transfer_score

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with client.app.state.database.session_factory() as session:
            marker = session.query(FeedbackRunModel).filter_by(run_id=run_id).one_or_none()
        if marker is not None:
            break
        time.sleep(0.01)
    assert marker is not None
    assert marker.pattern_count > 0

    with client.app.state.database.session_factory() as session:
        before = {
            (row.pattern_type, row.pattern_signature): row.observation_count
            for row in PatternKnowledgeRepository(session).list(limit=500)
        }
    duplicate = executor.feedback_service.process_completed_run(
        run_id=run_id,
        task_id=task_id,
        recovered_items=[RecoveredCredential(MD5_HEX, "hot-0-a")],
    )
    assert duplicate.processed is False
    with client.app.state.database.session_factory() as session:
        after = {
            (row.pattern_type, row.pattern_signature): row.observation_count
            for row in PatternKnowledgeRepository(session).list(limit=500)
        }
    assert after == before

    response = client.get("/api/feedback/patterns", params={
        "target_type": "hash",
        "algorithm": "MD5",
        "minimum_confidence": 0.0,
        "limit": 10,
    })
    assert response.status_code == 200
    assert response.json()
    assert "Admin2025!" not in response.text
    assert MD5_HEX not in response.text


def test_pattern_knowledge_survives_database_restart(tmp_path):
    url = f"sqlite:///{(tmp_path / 'restart.db').as_posix()}"
    db = Database(url)
    db.create_all()
    from test_feedback_v2 import _seed_task

    _seed_task(db, "T1")
    FeedbackService(db.session_factory).process_completed_run(
        run_id="R1",
        task_id="T1",
        recovered_items=[RecoveredCredential(MD5_HEX, "Admin2025!")],
    )
    db.dispose()

    reopened = Database(url)
    reopened.create_all()
    with reopened.session_factory() as session:
        rows = PatternKnowledgeRepository(session).list(limit=100)
        marker = session.query(FeedbackRunModel).filter_by(run_id="R1").one()
        assert rows
        assert marker.pattern_count > 0
    reopened.dispose()


def test_mock_feedback_is_excluded_by_default_and_test_opt_in_is_idempotent(tmp_path):
    def run_case(enabled: bool, filename: str) -> int:
        settings = Settings(
            database_url=f"sqlite:///{(tmp_path / filename).as_posix()}",
            upload_dir=tmp_path / f"uploads-{filename}",
            max_upload_bytes=1024,
            cors_origins=(),
            feedback_enable_mock=enabled,
        )
        with TestClient(create_app(settings)) as local_client:
            task_id = _create_analyzed_task(local_client, f"mock-{enabled}")
            assert local_client.post(f"/api/tasks/{task_id}/plan").status_code == 200
            started = local_client.post(
                f"/api/tasks/{task_id}/execute", json={"mode": "mock"}
            )
            run_id = started.json()["run_id"]
            assert local_client.get(f"/api/runs/{run_id}/result").status_code == 200
            assert local_client.get(f"/api/runs/{run_id}/result").status_code == 200
            with local_client.app.state.database.session_factory() as session:
                return session.query(FeedbackRunModel).count()

    assert run_case(False, "mock-off.db") == 0
    assert run_case(True, "mock-on.db") == 1
