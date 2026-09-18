"""计划层激活测试：S6/S7 纳入计划，并真正以 hashcat 原生攻击执行。"""

from __future__ import annotations

import dataclasses
import json
import sys
import time
from pathlib import Path

from sage_pass.enums import StrategyId
from sage_pass.hashcat_adapter import HashcatAdapter
from sage_pass.planner import NativeAttackSettings, RulePlanner
from sage_pass.schemas import PRIR

MD5_HEX = "0123456789abcdef0123456789abcdef"


def _prir(**overrides) -> PRIR:
    values = {
        "task_id": "T-NATIVE",
        "target_type": "hash",
        "algorithm": "md5",
        "salt": False,
        "verification_cost": "low",
        "context_available": False,
        "candidate_space": 1_000_000,
        "time_budget": 300,
        "candidate_budget": 100_000,
        "status": "analyzed",
        "confidence": 0.8,
        "warnings": [],
        "information_profile": None,
    }
    values.update(overrides)
    return PRIR(**values)


NATIVE = NativeAttackSettings(
    rules_path="rules/best64.rule",
    mask_ladder=("?d?d?d?d", "?l?l?l?l"),
    hybrid_masks=("?d?d",),
)


def test_rule_planner_adds_mask_and_hybrid_units_when_configured():
    plan = RulePlanner(native_attacks=NATIVE).plan(_prir())
    ids = [item.strategy_id for item in plan.strategies]

    assert StrategyId.S6 in ids, ids
    assert StrategyId.S7 in ids, ids
    by_id = {item.strategy_id: item for item in plan.strategies}
    # 词表 × 规则由 S1 的原生词表攻击承担（hashcat 最经典的主力攻击）。
    assert by_id[StrategyId.S1].parameters["hashcat_rule_files"] == [
        "rules/best64.rule"
    ]
    assert "hashcat_rule_files" not in by_id[StrategyId.S2].parameters
    # 掩码阶梯按候选预算裁剪：?l?l?l?l（456976）装不进本单元的预算，只保留 ?d?d?d?d。
    assert by_id[StrategyId.S6].parameters == {
        "hashcat_attack_mode": 3,
        "hashcat_masks": ["?d?d?d?d"],
    }
    assert by_id[StrategyId.S6].candidate_budget >= 10_000
    assert by_id[StrategyId.S7].parameters == {
        "hashcat_attack_mode": 6,
        "hashcat_hybrid_mask": ["?d?d"],
    }
    assert by_id[StrategyId.S6].strategy_name == "Mask / Brute"


def test_rule_planner_without_native_settings_keeps_original_units():
    plan = RulePlanner().plan(_prir())
    ids = [item.strategy_id for item in plan.strategies]
    assert StrategyId.S6 not in ids
    assert StrategyId.S7 not in ids
    assert "hashcat_rule_files" not in plan.strategies[0].parameters


def _install_fakes(client, tmp_path, monkeypatch, **env):
    from sim_binaries import write_sim_scripts

    hashcat_script, _ = write_sim_scripts(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    client.app.state.real_executor.hashcat = HashcatAdapter(
        [sys.executable, str(hashcat_script)]
    )


def _wait_terminal(client, run_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/runs/{run_id}/status").json()
        if status["status"] in {"completed", "failed", "cancelled"}:
            return status
        time.sleep(0.2)
    raise AssertionError("运行未在预期时间内结束")


def test_executor_runs_mask_and_hybrid_units_as_native_attacks(
    client, tmp_path, monkeypatch
):
    """掩码单元以 -a 3 执行、混合单元以 -a 6 执行（掩码直接作为 hashcat 参数）。"""
    log_path = tmp_path / "native-attacks.jsonl"
    _install_fakes(client, tmp_path, monkeypatch, FAKE_HASHCAT_LOG=str(log_path))

    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("alpha\nbeta\n", encoding="utf-8")
    executor = client.app.state.real_executor
    executor.settings = dataclasses.replace(executor.settings, wordlist_path=wordlist)
    client.app.state.planner = RulePlanner(native_attacks=NATIVE)

    created = client.post(
        "/api/tasks",
        json={
            "name": "原生攻击单元",
            "target": {"type": "hash", "content": MD5_HEX, "file_id": None},
            "known_algorithm": "md5",
            "time_budget": 300,
            "candidate_budget": 100_000,
            "context": {},
        },
    )
    task_id = created.json()["task_id"]
    assert client.post(f"/api/tasks/{task_id}/analyze").status_code == 200
    plan = client.post(f"/api/tasks/{task_id}/plan").json()
    assert {item["strategy_id"] for item in plan["strategies"]} >= {"S6", "S7"}

    started = client.post(f"/api/tasks/{task_id}/execute", json={"mode": "real"})
    assert started.status_code == 200, started.text
    _wait_terminal(client, started.json()["run_id"])

    entries = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    modes = {entry["attack_mode"] for entry in entries}
    assert "3" in modes, entries          # 掩码阶梯：-a 3
    assert "6" in modes, entries          # 混合攻击：-a 6
    mask_entry = next(e for e in entries if e["attack_mode"] == "3")
    assert mask_entry["masks"], mask_entry
    hybrid_entry = next(e for e in entries if e["attack_mode"] == "6")
    assert hybrid_entry["masks"], hybrid_entry
    # 混合攻击的字典来自服务器端词表，直接作为 hashcat 位置参数传入。
    assert str(wordlist) in " ".join(hybrid_entry["argv"]), hybrid_entry["argv"]
