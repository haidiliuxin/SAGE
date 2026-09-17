"""第 1 步（面向真实场景）的端到端测试：原生词表攻击与命中即停。

- 原生词表攻击：配置 `SAGE_WORDLIST_PATH` 后，S1 由 hashcat 直接读整本字典
  （单进程、不经过 Python 候选列表），因此不写临时 candidates.txt。
- 命中即停：`stop_on_hit` 命中后立即结束，不再消耗剩余候选，
  研究停止原因为 `all_targets_recovered`。
"""

from __future__ import annotations

import dataclasses
import json
import sys
import time
from pathlib import Path

from sage_pass.hashcat_adapter import HashcatAdapter
from sage_pass.zip_adapter import ZipHashExtractor

from sim_binaries import write_sim_scripts

MD5_HEX = "0123456789abcdef0123456789abcdef"


def _install_fakes(client, tmp_path, monkeypatch, **env):
    hashcat_script, zip_script = write_sim_scripts(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    client.app.state.real_executor.hashcat = HashcatAdapter(
        [sys.executable, str(hashcat_script)]
    )
    client.app.state.real_executor.zip_extractor = ZipHashExtractor(
        [sys.executable, str(zip_script)]
    )


def _set_settings(client, **overrides):
    executor = client.app.state.real_executor
    executor.settings = dataclasses.replace(executor.settings, **overrides)


def _create_hash_task(client, *, candidate_budget: int = 50, time_budget: int = 60) -> str:
    created = client.post(
        "/api/tasks",
        json={
            "name": "第1步真实场景",
            "target": {"type": "hash", "content": MD5_HEX, "file_id": None},
            "known_algorithm": "md5",
            "time_budget": time_budget,
            "candidate_budget": candidate_budget,
            "context": {},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["task_id"]


def _wait_terminal(client, run_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/runs/{run_id}/status").json()
        if status["status"] in {"completed", "failed", "cancelled"}:
            return status
        time.sleep(0.2)
    raise AssertionError("运行未在预期时间内结束")


def test_native_wordlist_attack_reads_dictionary_directly(client, tmp_path, monkeypatch):
    log_path = tmp_path / "hashcat-native.log"
    _install_fakes(client, tmp_path, monkeypatch, FAKE_HASHCAT_LOG=str(log_path))
    dictionary = tmp_path / "rockyou-mini.txt"
    dictionary.write_text("hunter2\nletmein\ncorrect horse\n", encoding="utf-8")
    _set_settings(client, wordlist_path=dictionary)

    task_id = _create_hash_task(client)
    assert client.post(f"/api/tasks/{task_id}/analyze").status_code == 200
    assert client.post(f"/api/tasks/{task_id}/plan").status_code == 200
    started = client.post(f"/api/tasks/{task_id}/execute", json={"mode": "real"})
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    _wait_terminal(client, run_id)
    result = client.get(f"/api/runs/{run_id}/result").json()

    assert result["status"] == "completed"
    # 第一个词表条目被恢复，且没有经过 Python 候选列表。
    assert [item["plaintext"] for item in result["recovered_items"]] == ["hunter2"]
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    native_entries = [entry for entry in entries if entry["attack_mode"] == "0"]
    assert native_entries, entries
    assert any(str(dictionary) in entry["targets"] or str(dictionary) in entry["argv"] for entry in native_entries)
    # 原生攻击不应把字典复制成临时 candidates.txt。
    assert not any("candidates.txt" in " ".join(entry["argv"]) for entry in native_entries)


def test_uploaded_wordlist_file_drives_native_attack(client, tmp_path, monkeypatch):
    """上传的词表文件直接交给 hashcat 原生读取（无 10 万条上限）。"""
    log_path = tmp_path / "hashcat-uploaded.log"
    _install_fakes(client, tmp_path, monkeypatch, FAKE_HASHCAT_LOG=str(log_path))
    upload = client.post(
        "/api/files",
        files={"file": ("company.dict", b"hunter2\nletmein\nP@ssw0rd2024\n", "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    wordlist_file_id = upload.json()["file_id"]

    created = client.post(
        "/api/tasks",
        json={
            "name": "上传词表任务",
            "target": {"type": "hash", "content": MD5_HEX, "file_id": None},
            "known_algorithm": "md5",
            "time_budget": 60,
            "candidate_budget": 5000,
            "context": {},
            "wordlist_file_id": wordlist_file_id,
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]
    assert client.get(f"/api/tasks/{task_id}").json()["wordlist_file_id"] == wordlist_file_id
    assert client.post(f"/api/tasks/{task_id}/analyze").status_code == 200
    assert client.post(f"/api/tasks/{task_id}/plan").status_code == 200
    started = client.post(f"/api/tasks/{task_id}/execute", json={"mode": "real"})
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    _wait_terminal(client, run_id)
    result = client.get(f"/api/runs/{run_id}/result").json()

    assert result["status"] == "completed"
    assert [item["plaintext"] for item in result["recovered_items"]] == ["hunter2"]
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert entries and entries[0]["attack_mode"] == "0"
    # hashcat 收到的是上传文件的落盘路径（按需流式读取），而不是 Python 生成的候选文件。
    assert entries[0]["argv"][-1].endswith(".dict"), entries[0]["argv"]


def test_wordlist_file_must_be_a_text_dictionary(client, tmp_path, monkeypatch):
    _install_fakes(client, tmp_path, monkeypatch)
    upload = client.post(
        "/api/files",
        files={"file": ("archive.zip", b"PK\x03\x04fake", "application/zip")},
    )
    assert upload.status_code == 201
    rejected = client.post(
        "/api/tasks",
        json={
            "name": "错误词表",
            "target": {"type": "hash", "content": MD5_HEX, "file_id": None},
            "known_algorithm": "md5",
            "time_budget": 60,
            "candidate_budget": 100,
            "context": {},
            "wordlist_file_id": upload.json()["file_id"],
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert "纯文本字典" in rejected.text


def test_stop_on_hit_ends_run_after_first_recovery(client, tmp_path, monkeypatch):
    log_path = tmp_path / "hashcat-stophit.log"
    _install_fakes(client, tmp_path, monkeypatch, FAKE_HASHCAT_LOG=str(log_path))
    task_id = _create_hash_task(client, candidate_budget=5000)
    assert client.post(f"/api/tasks/{task_id}/analyze").status_code == 200
    assert client.post(f"/api/tasks/{task_id}/plan").status_code == 200
    started = client.post(
        f"/api/tasks/{task_id}/execute",
        json={"mode": "real", "stop_on_hit": True, "candidates": ["first-hit"]},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    _wait_terminal(client, run_id)

    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["total_recovered"] == 1
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    # 命中即停：只启动了一个 hashcat 批次。
    assert len(entries) == 1, entries
    research = client.get(f"/api/runs/{run_id}/research").json()
    assert research["stop_reason"] == "all_targets_recovered"

    # 同一配置在关闭命中即停时会继续消耗剩余候选（需要新任务，完成态不可重跑）。
    log_path.write_text("", encoding="utf-8")
    second_task = _create_hash_task(client, candidate_budget=5000)
    assert client.post(f"/api/tasks/{second_task}/analyze").status_code == 200
    assert client.post(f"/api/tasks/{second_task}/plan").status_code == 200
    started_again = client.post(
        f"/api/tasks/{second_task}/execute",
        json={"mode": "real", "stop_on_hit": False, "candidates": ["first-hit"]},
    )
    assert started_again.status_code == 200, started_again.text
    again_run_id = started_again.json()["run_id"]
    _wait_terminal(client, again_run_id)
    again = client.get(f"/api/runs/{again_run_id}/research").json()
    # 未开启命中即停时不会以“全部目标已恢复”提前结束。
    assert again["stop_reason"] != "all_targets_recovered"
    assert again["totals"]["recovered_targets"] == 1
