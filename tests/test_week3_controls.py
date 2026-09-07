"""第三周甲：暂停/继续/取消 + 实时状态冻结 + 异常恢复（收尾）测试。"""

from __future__ import annotations

import sys
import time

import pytest

from sage_pass.config import Settings
from sage_pass.database import Database
from sage_pass.enums import TaskStatus
from sage_pass.hashcat_adapter import HashcatAdapter
from sage_pass.main import create_app
from sage_pass.models import StrategyRunModel, TaskModel
from sage_pass.service import finalize_interrupted_tasks, now_iso, public_id
from sage_pass.zip_adapter import ZipHashExtractor

from sim_binaries import write_sim_scripts

MD5_HEX = "0123456789abcdef0123456789abcdef"


def _prepare(client, payload) -> str:
    task_id = client.post("/api/tasks", json=payload).json()["task_id"]
    client.post(f"/api/tasks/{task_id}/analyze").raise_for_status()
    client.post(f"/api/tasks/{task_id}/plan").raise_for_status()
    return task_id


def _run_mock_to_running(client, payload) -> tuple[str, str]:
    task_id = _prepare(client, payload)
    started = client.post(
        f"/api/tasks/{task_id}/execute", json={"mode": "mock"}
    )
    assert started.status_code == 200, started.text
    return task_id, started.json()["run_id"]


def _wait_status(client, run_id, wanted, timeout=12.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}/status")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == wanted:
            return last
        time.sleep(0.1)
    pytest.fail(f"run 未在 {timeout}s 内进入 {wanted}：{last}")


def test_status_machine_controls_pause_resume_cancel(client, hash_task_payload):
    task_id = client.post("/api/tasks", json=hash_task_payload).json()["task_id"]

    # 非 running 不允许暂停
    early = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "paused"}
    )
    assert early.status_code == 409

    client.post(f"/api/tasks/{task_id}/analyze")
    client.post(f"/api/tasks/{task_id}/plan")
    started = client.post(
        f"/api/tasks/{task_id}/execute", json={"mode": "mock"}
    )
    assert started.json()["status"] == "running"

    paused = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "paused"}
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "running"}
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"

    cancelled = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "cancelled"}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_mock_pause_freezes_progress_and_resume_continues(client):
    payload = {
        "name": "mock-pause",
        "target": {"type": "hash", "content": MD5_HEX, "file_id": None},
        "known_algorithm": "md5",
        "time_budget": 300,
        "candidate_budget": 200000,
        "context": {},
    }
    task_id, run_id = _run_mock_to_running(client, payload)

    time.sleep(0.5)
    before = client.get(f"/api/runs/{run_id}/status").json()
    assert before["status"] == "running"
    assert before["tested"] > 0

    paused = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "paused"}
    )
    assert paused.status_code == 200

    time.sleep(0.6)
    frozen = client.get(f"/api/runs/{run_id}/status").json()
    assert frozen["status"] == "paused"
    assert frozen["tested"] == before["tested"]
    assert "暂停" in frozen["message"]

    time.sleep(0.5)
    still = client.get(f"/api/runs/{run_id}/status").json()
    assert still["status"] == "paused"
    assert still["tested"] == before["tested"]
    # 暂停期间进度冻结（两次轮询读数一致）。
    assert still["progress"] == frozen["progress"]
    assert still["elapsed_time"] == frozen["elapsed_time"]

    resumed = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "running"}
    )
    assert resumed.status_code == 200
    deadline = time.monotonic() + 5
    advanced = False
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}/status").json()
        if body["tested"] > before["tested"] or body["status"] in {
            "completed",
            "cancelled",
        }:
            advanced = True
            break
        time.sleep(0.1)
    assert advanced, "继续后进度应恢复推进"

    cancelled = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "cancelled"}
    )
    assert cancelled.status_code == 200


def test_real_execution_pause_resume_cancel(client, tmp_path, monkeypatch):
    script, _ = write_sim_scripts(tmp_path)
    monkeypatch.setenv("FAKE_HASHCAT_SLOW", "0.6")
    client.app.state.real_executor.hashcat = HashcatAdapter(
        [sys.executable, str(script)]
    )
    payload = {
        "name": "real-pause",
        "target": {"type": "hash", "content": MD5_HEX, "file_id": None},
        "known_algorithm": "md5",
        "time_budget": 300,
        "candidate_budget": 20000,
        "context": {},
    }
    task_id = _prepare(client, payload)

    candidates = [f"w{index:05d}" for index in range(3000)]  # 至少 3 批
    started = client.post(
        f"/api/tasks/{task_id}/execute",
        json={"mode": "real", "candidates": candidates},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    time.sleep(0.5)

    paused = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "paused"}
    )
    assert paused.status_code == 200, paused.text

    # 暂停在批次边界生效：在批内时先显示受理中，随后进入 paused。
    _wait_status(client, run_id, "paused", timeout=15.0)

    resumed = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "running"}
    )
    assert resumed.status_code == 200, resumed.text

    # 继续后先回到 running（或极快完成）。
    terminal = None
    deadline = time.monotonic() + 15
    observed_running = False
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}/status").json()
        if body["status"] == "running":
            observed_running = True
            break
        if body["status"] in {"completed", "failed", "cancelled"}:
            terminal = body["status"]
            break
        time.sleep(0.1)
    assert observed_running or terminal in {"completed", "failed"}

    # 结束：取消（仍运行中）或已完成即取结果。
    detail = client.get(f"/api/tasks/{task_id}").json()
    if detail["status"] == "running":
        cancel = client.patch(
            f"/api/tasks/{task_id}/status", json={"status": "cancelled"}
        )
        assert cancel.status_code == 200, cancel.text
        _wait_status(client, run_id, "cancelled", timeout=15.0)
    else:
        result = client.get(f"/api/runs/{run_id}/result")
        assert result.status_code == 200, result.text


def test_finalize_interrupted_tasks_marks_stale_running_as_failed(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'i.db').as_posix()}")
    db.create_all()
    with db.session_factory() as session:
        task = TaskModel(
            task_id=public_id("T"),
            name="stale",
            target_type="hash",
            target_content=MD5_HEX,
            file_id=None,
            known_algorithm="md5",
            time_budget=60,
            candidate_budget=100,
            context={},
            status=TaskStatus.RUNNING.value,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        session.add(task)
        session.commit()
        run_id = public_id("R")
        session.add_all(
            [
                StrategyRunModel(
                    run_id=run_id,
                    task_id=task.task_id,
                    strategy_id="S1",
                    strategy_name="Baseline",
                    priority=1,
                    time_budget=12,
                    candidate_budget=20,
                    parameters={},
                    status=TaskStatus.RUNNING.value,
                    tested=3,
                    recovered=0,
                    started_at=now_iso(),
                )
            ]
        )
        session.commit()

    processed = finalize_interrupted_tasks(db.session_factory)
    assert processed == 1

    with db.session_factory() as session:
        task = session.query(TaskModel).one()
        assert TaskStatus(task.status) == TaskStatus.FAILED
        row = session.query(StrategyRunModel).one()
        assert TaskStatus(row.status) == TaskStatus.FAILED
        assert row.finished_at is not None
    db.dispose()


def test_finalize_interrupted_tasks_completes_when_rows_finished(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'i2.db').as_posix()}")
    db.create_all()
    with db.session_factory() as session:
        task = TaskModel(
            task_id=public_id("T"),
            name="stale2",
            target_type="hash",
            target_content=MD5_HEX,
            file_id=None,
            known_algorithm="md5",
            time_budget=60,
            candidate_budget=100,
            context={},
            status=TaskStatus.RUNNING.value,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        session.add(task)
        session.commit()
        run_id = public_id("R")
        session.add(
            StrategyRunModel(
                run_id=run_id,
                task_id=task.task_id,
                strategy_id="S1",
                strategy_name="Baseline",
                priority=1,
                time_budget=12,
                candidate_budget=20,
                parameters={},
                status=TaskStatus.COMPLETED.value,
                tested=20,
                recovered=0,
                started_at=now_iso(),
                finished_at=now_iso(),
            )
        )
        session.commit()

    processed = finalize_interrupted_tasks(db.session_factory)
    assert processed == 1
    with db.session_factory() as session:
        task = session.query(TaskModel).one()
        assert TaskStatus(task.status) == TaskStatus.COMPLETED
    db.dispose()
