"""第二周真实执行链路的端到端测试。

测试通过注入“仿真 hashcat / zip2john”（用当前解释器执行脚本）来验证
API 真实执行链：analyze → plan → execute(real) → status → result，
以及时间预算自动停止、取消和输入校验。
"""

from __future__ import annotations

import sys
import time

import pytest

from sage_pass.hashcat_adapter import HashcatAdapter
from sage_pass.zip_adapter import ZipHashExtractor

from sim_binaries import write_sim_scripts

MD5_HEX = "0123456789abcdef0123456789abcdef"


def _install_fakes(client, tmp_path, monkeypatch, **env):
    """把真实执行器与分析器切换为仿真外部程序。"""
    hashcat_script, zip_script = write_sim_scripts(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    zip_extractor = ZipHashExtractor([sys.executable, str(zip_script)])
    client.app.state.zip_extractor = zip_extractor
    client.app.state.real_executor.zip_extractor = zip_extractor
    client.app.state.real_executor.hashcat = HashcatAdapter(
        [sys.executable, str(hashcat_script)]
    )


def _create_hash_task(
    client,
    *,
    content: str = MD5_HEX,
    known_algorithm: str | None = "md5",
    time_budget: int = 60,
    candidate_budget: int = 50,
    context: dict | None = None,
) -> str:
    payload = {
        "name": "真实执行任务",
        "target": {"type": "hash", "content": content, "file_id": None},
        "known_algorithm": known_algorithm,
        "time_budget": time_budget,
        "candidate_budget": candidate_budget,
        "context": context or {},
    }
    created = client.post("/api/tasks", json=payload)
    assert created.status_code == 201, created.text
    return created.json()["task_id"]


def _analyze_plan(client, task_id: str) -> None:
    analyzed = client.post(f"/api/tasks/{task_id}/analyze")
    assert analyzed.status_code == 200, analyzed.text
    planned = client.post(f"/api/tasks/{task_id}/plan")
    assert planned.status_code == 200, planned.text


def _wait_terminal(client, run_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}/status")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(0.05)
    pytest.fail(f"run {run_id} 未在 {timeout}s 内结束：{last}")


def _finish_real(client, task_id: str, candidates: list[str]) -> dict:
    started = client.post(
        f"/api/tasks/{task_id}/execute",
        json={"mode": "real", "candidates": candidates},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    _wait_terminal(client, run_id)
    result = client.get(f"/api/runs/{run_id}/result")
    assert result.status_code == 200, result.text
    return result.json()


def test_real_execution_hash_recovers_per_strategy(
    client, tmp_path, monkeypatch
):
    _install_fakes(client, tmp_path, monkeypatch)
    task_id = _create_hash_task(
        client,
        time_budget=30,
        candidate_budget=50,
        context={"keywords": ["张三"], "years": [2024]},
    )
    _analyze_plan(client, task_id)
    candidates = [f"cand-{index:02d}" for index in range(30)]

    result = _finish_real(client, task_id, candidates)

    assert result["status"] == "completed"
    assert result["total_tested"] == 30
    assert [item["strategy_id"] for item in result["strategy_results"]] == [
        "S1",
        "S4",
    ]
    # S1 使用前 10 个候选，S4 使用其后 20 个，各自恢复切片内第一个候选。
    assert result["total_recovered"] == 2
    plaintexts = {item["plaintext"] for item in result["recovered_items"]}
    assert plaintexts == {"cand-00", "cand-10"}
    for item in result["recovered_items"]:
        assert item["target"] == MD5_HEX
    # 任务被标记为完成。
    detail = client.get(f"/api/tasks/{task_id}").json()
    assert detail["status"] == "completed"


def test_real_execution_zip_winzip_recovery(client, tmp_path, monkeypatch):
    _install_fakes(client, tmp_path, monkeypatch, FAKE_ZIP2JOHN_KIND="winzip")
    uploaded = client.post(
        "/api/files",
        files={"file": ("secret.zip", b"PK\x03\x04fake", "application/zip")},
    )
    assert uploaded.status_code == 201
    file_id = uploaded.json()["file_id"]
    created = client.post(
        "/api/tasks",
        json={
            "name": "ZIP真实执行",
            "target": {"type": "zip", "content": None, "file_id": file_id},
            "known_algorithm": None,
            "time_budget": 60,
            "candidate_budget": 30,
            "context": {},
        },
    )
    task_id = created.json()["task_id"]

    analyzed = client.post(f"/api/tasks/{task_id}/analyze")
    assert analyzed.status_code == 200, analyzed.text
    prir = analyzed.json()
    assert prir["algorithm"] == "zip-aes"
    assert prir["verification_cost"] == "medium"
    assert prir["salt"] is True

    planned = client.post(f"/api/tasks/{task_id}/plan")
    assert planned.status_code == 200, planned.text
    started = client.post(
        f"/api/tasks/{task_id}/execute",
        json={
            "mode": "real",
            "candidates": ["passw0rd", "123456", "letmein"],
        },
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    _wait_terminal(client, run_id)
    result = client.get(f"/api/runs/{run_id}/result").json()

    assert result["status"] == "completed"
    assert result["total_recovered"] == 1
    recovered = result["recovered_items"][0]
    assert recovered["plaintext"] == "passw0rd"
    assert recovered["target"].startswith("$zip2$")


def test_real_execution_timeout_auto_stops(client, tmp_path, monkeypatch):
    _install_fakes(client, tmp_path, monkeypatch, FAKE_HASHCAT_SLOW="8")
    task_id = _create_hash_task(client, candidate_budget=5)
    _analyze_plan(client, task_id)

    started = client.post(
        f"/api/tasks/{task_id}/execute",
        json={"mode": "real", "timeout": 1, "candidates": ["a", "b", "c"]},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    terminal = _wait_terminal(client, run_id, timeout=6.0)

    assert terminal["status"] == "completed"
    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["total_time"] < 6.0
    # 超时前只来得及执行预算内切片的一部分（仿真脚本被终止，无恢复结果）。
    assert result["total_recovered"] == 0
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "completed"


def test_real_execution_can_be_cancelled(client, tmp_path, monkeypatch):
    _install_fakes(client, tmp_path, monkeypatch, FAKE_HASHCAT_SLOW="30")
    task_id = _create_hash_task(client, candidate_budget=20)
    _analyze_plan(client, task_id)

    started = client.post(
        f"/api/tasks/{task_id}/execute",
        json={
            "mode": "real",
            "candidates": ["x", "y", "z", "w"],
        },
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    time.sleep(0.3)

    running = client.get(f"/api/runs/{run_id}/status").json()
    assert running["status"] == "running"

    cancelled = client.patch(
        f"/api/tasks/{task_id}/status", json={"status": "cancelled"}
    )
    assert cancelled.status_code == 200, cancelled.text

    _wait_terminal(client, run_id, timeout=6.0)
    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["status"] == "cancelled"
    assert result["message"] == "执行已停止"
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "cancelled"


def test_real_execution_requires_planned_task_and_candidates(
    client, tmp_path, monkeypatch
):
    _install_fakes(client, tmp_path, monkeypatch)
    task_id = _create_hash_task(client)
    _analyze_plan(client, task_id)

    empty = client.post(
        f"/api/tasks/{task_id}/execute",
        json={"mode": "real", "candidates": []},
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["message"] == "真实执行候选集为空，请先提供 candidates"
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "planned"

    unplanned_task = _create_hash_task(client)
    not_planned = client.post(
        f"/api/tasks/{unplanned_task}/execute",
        json={"mode": "real", "candidates": ["a"]},
    )
    assert not_planned.status_code == 409


def test_execution_request_validates_candidate_lines(client, tmp_path, monkeypatch):
    _install_fakes(client, tmp_path, monkeypatch)
    task_id = _create_hash_task(client)
    _analyze_plan(client, task_id)

    response = client.post(
        f"/api/tasks/{task_id}/execute",
        json={
            "mode": "real",
            "candidates": ["good", "", "bad\nnewline", "x" * 2048],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_TASK"


def test_real_execution_result_waits_until_finished(client, tmp_path, monkeypatch):
    _install_fakes(client, tmp_path, monkeypatch, FAKE_HASHCAT_SLOW="2")
    task_id = _create_hash_task(client)
    _analyze_plan(client, task_id)

    started = client.post(
        f"/api/tasks/{task_id}/execute",
        json={"mode": "real", "candidates": ["a", "b"]},
    )
    run_id = started.json()["run_id"]
    time.sleep(0.2)

    early = client.get(f"/api/runs/{run_id}/result")
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "RUN_IN_PROGRESS"

    _wait_terminal(client, run_id, timeout=6.0)
    result = client.get(f"/api/runs/{run_id}/result")
    assert result.status_code == 200
