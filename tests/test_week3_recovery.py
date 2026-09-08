"""第三周甲后半：真实运行持久化与重启断点续跑测试。"""

from __future__ import annotations

import sys
import time

import pytest

from sage_pass.candidate_generator import CandidateGenerator
from sage_pass.config import Settings
from sage_pass.database import Database
from sage_pass.enums import ExecutionMode, TaskStatus
from sage_pass.hashcat_adapter import HashcatAdapter
from sage_pass.models import RunRecordModel, StrategyRunModel, TaskModel
from sage_pass.real_executor import (
    RealExecutor,
    _serialize_snapshot,
)
from sage_pass.repository import RunRecordRepository, StrategyRunRepository
from sage_pass.schemas import (
    ExecutionRequest,
    StrategyItem,
    StrategyPlan,
    TaskDetail,
)
from sage_pass.service import (
    finalize_interrupted_tasks,
    now_iso,
    public_id,
)

from sim_binaries import write_sim_scripts

MD5_HEX = "0123456789abcdef0123456789abcdef"
TOTAL_CANDIDATES = 3000  # S1 预算 3000 → 每批 1000，共 3 批


def _seed_task(db: Database, *, status: str = TaskStatus.RUNNING.value) -> TaskModel:
    with db.session_factory() as session:
        task = TaskModel(
            task_id=public_id("T"),
            name="recovery",
            target_type="hash",
            target_content=MD5_HEX,
            file_id=None,
            known_algorithm="md5",
            time_budget=300,
            candidate_budget=TOTAL_CANDIDATES,
            context={},
            status=status,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


def _seed_record(
    db: Database,
    task: TaskModel,
    *,
    consumed: int,
    run_status: str = TaskStatus.RUNNING.value,
    scheduler_stats: dict | None = None,
) -> str:
    plan = StrategyPlan(
        task_id=task.task_id,
        planner_type="rule",
        total_time_budget=300,
        strategies=[
            StrategyItem(
                strategy_id="S1",
                strategy_name="Baseline",
                priority=1,
                time_budget=60,
                candidate_budget=TOTAL_CANDIDATES,
                reason="recovery",
                parameters={},
            )
        ],
        status=TaskStatus.PLANNED,
        warnings=[],
    )
    supplied = [f"cand-{index:05d}" for index in range(TOTAL_CANDIDATES)]
    generator = CandidateGenerator()
    batches: dict[str, list] = {}
    for batch in generator.iter_plan_batches(
        plan, supplied_candidates=supplied
    ):
        batches.setdefault(batch.strategy_id.value, []).append(batch)
    payload = ExecutionRequest(
        mode=ExecutionMode.REAL, candidates=supplied
    )
    snapshot = _serialize_snapshot(
        plan=plan,
        payload=payload,
        targets=(MD5_HEX,),
        hash_mode=0,
        batches_by_strategy=batches,
        expected=TOTAL_CANDIDATES,
        total_candidate_budget=task.candidate_budget,
    )
    progress = {
        "started_at": now_iso(),
        "expected_candidates": TOTAL_CANDIDATES,
        "strategies": {
            "S1": {
                "consumed": consumed,
                "tested": consumed * 1000,
                "recovered": 0,
                "time_cost": float(consumed) * 0.1,
                "recovered_items": [],
            }
        },
        "scheduler_stats": scheduler_stats or {},
    }
    run_id = public_id("R")
    with db.session_factory() as session:
        RunRecordRepository(session).add(
            RunRecordModel(
                run_id=run_id,
                task_id=task.task_id,
                mode=ExecutionMode.REAL.value,
                status=run_status,
                started_at=progress["started_at"],
                snapshot=snapshot,
                progress=progress,
                created_at=progress["started_at"],
                updated_at=progress["started_at"],
            )
        )
        StrategyRunRepository(session).add_many(
            [
                StrategyRunModel(
                    run_id=run_id,
                    task_id=task.task_id,
                    strategy_id=entry["strategy_id"],
                    strategy_name=entry.get("strategy_name", entry["strategy_id"]),
                    priority=entry["priority"],
                    time_budget=entry["time_budget"],
                    candidate_budget=entry["candidate_budget"],
                    parameters=entry.get("parameters", {}),
                    status=run_status,
                    tested=consumed * 1000
                    if run_status != TaskStatus.CANCELLED.value
                    else 0,
                    recovered=0,
                    started_at=progress["started_at"],
                )
                for entry in snapshot["plan"]["strategies"]
            ]
        )
    return run_id


def _wait_finished(executor: RealExecutor, run_id: str, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        status = executor.status(run_id)
        last = status
        if status.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            return status
        time.sleep(0.2)
    raise AssertionError(f"recovered run not terminal: {last}")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script, _ = write_sim_scripts(tmp_path)
    monkeypatch.setenv("FAKE_HASHCAT_NO_RECOVER", "1")
    return HashcatAdapter([sys.executable, str(script)])


def test_recover_resumes_remaining_batches_after_restart(tmp_path, fake):
    db = Database(f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    db.create_all()
    task = _seed_task(db)
    run_id = _seed_record(db, task, consumed=1)  # 已执行 1 批，剩 500 候选

    executor = RealExecutor(
        session_factory=db.session_factory,
        settings=Settings.from_env(),
        hashcat=fake,
    )
    resumed = executor.recover_after_restart()
    assert resumed == {task.task_id}

    terminal = _wait_finished(executor, run_id)
    assert terminal.status == TaskStatus.COMPLETED
    result = executor.result(run_id)
    # 已消费的第 1 批（1000 候选）不再重跑，只续跑剩余 2 批（2000）：
    # 总 tested = 恢复的 1000 + 新执行 2000。
    assert result.total_tested == TOTAL_CANDIDATES
    assert result.total_recovered == 0

    with db.session_factory() as session:
        record = RunRecordRepository(session).get(run_id)
        assert TaskStatus(record.status) == TaskStatus.COMPLETED
        assert TaskStatus(session.query(TaskModel).one().status) == TaskStatus.COMPLETED
        row = StrategyRunRepository(session).list_by_run_id(run_id)[0]
        assert TaskStatus(row.status) == TaskStatus.COMPLETED
        assert row.tested == TOTAL_CANDIDATES
    executor.shutdown()
    db.dispose()


def test_recover_resumes_paused_record_after_restart(tmp_path, fake):
    db = Database(f"sqlite:///{(tmp_path / 'r2.db').as_posix()}")
    db.create_all()
    task = _seed_task(db)
    run_id = _seed_record(
        db, task, consumed=1, run_status=TaskStatus.PAUSED.value
    )

    executor = RealExecutor(
        session_factory=db.session_factory,
        settings=Settings.from_env(),
        hashcat=fake,
    )
    resumed = executor.recover_after_restart()
    assert task.task_id in resumed
    terminal = _wait_finished(executor, run_id)
    assert terminal.status == TaskStatus.COMPLETED
    result = executor.result(run_id)
    assert result.total_tested == TOTAL_CANDIDATES
    executor.shutdown()
    db.dispose()


def test_recover_failure_finalizes_record_and_task(tmp_path, fake):
    db = Database(f"sqlite:///{(tmp_path / 'r3.db').as_posix()}")
    db.create_all()
    task = _seed_task(db)
    run_id = public_id("R")
    with db.session_factory() as session:
        RunRecordRepository(session).add(
            RunRecordModel(
                run_id=run_id,
                task_id=task.task_id,
                mode=ExecutionMode.REAL.value,
                status=TaskStatus.RUNNING.value,
                started_at=now_iso(),
                snapshot={"broken": True},
                progress={},
                created_at=now_iso(),
                updated_at=now_iso(),
            )
        )

    executor = RealExecutor(
        session_factory=db.session_factory,
        settings=Settings.from_env(),
        hashcat=fake,
    )
    resumed = executor.recover_after_restart()
    assert task.task_id not in resumed

    with db.session_factory() as session:
        record = RunRecordRepository(session).get(run_id)
        assert TaskStatus(record.status) == TaskStatus.FAILED
        assert "恢复失败" in (record.message or "")
        assert TaskStatus(session.query(TaskModel).one().status) == TaskStatus.FAILED
    executor.shutdown()
    db.dispose()


def test_finalize_respects_excluded_task_ids(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'r4.db').as_posix()}")
    db.create_all()
    task = _seed_task(db)

    processed = finalize_interrupted_tasks(
        db.session_factory, exclude_task_ids={task.task_id}
    )
    assert processed == 0
    with db.session_factory() as session:
        assert TaskStatus(session.query(TaskModel).one().status) == TaskStatus.RUNNING

    processed = finalize_interrupted_tasks(db.session_factory)
    assert processed == 1
    with db.session_factory() as session:
        assert TaskStatus(session.query(TaskModel).one().status) == TaskStatus.FAILED
    db.dispose()
