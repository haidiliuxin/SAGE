"""自适应切片的执行层测试：原生单元被切成多个可观测批次。

要点（对应 100 条评测暴露的问题）：原生攻击一次拉完整段键空间，调度器每轮只有一个
决策点，最终轮 332 个决策全是探索、利用阶段 0 次。本模块验证：

- S1（词表 × 规则链）按词区间 `-s/-l` 切成"探针 → 倍增"的多个批次，且不重不漏；
- S6（掩码阶梯）拆成可预测的子掩码切片；
- 每个批次都满足 `tested ≤ 提交量 ≤ 该批次分配预算`；
- 关闭开关时退回"一次跑完"的旧行为。
"""

from __future__ import annotations

import dataclasses
import json
import sys
import time
from pathlib import Path

from sage_pass.planner import NativeAttackSettings, RulePlanner

MD5_HEX = "0123456789abcdef0123456789abcdef"


def _install_fakes(client, tmp_path, monkeypatch, log_path: Path, **env) -> None:
    from sim_binaries import write_sim_scripts

    from sage_pass.hashcat_adapter import HashcatAdapter

    hashcat_script, _ = write_sim_scripts(tmp_path)
    monkeypatch.setenv("FAKE_HASHCAT_LOG", str(log_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    client.app.state.real_executor.hashcat = HashcatAdapter(
        [sys.executable, str(hashcat_script)]
    )


def _wait_terminal(client, run_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/runs/{run_id}/status").json()
        if status["status"] in {"completed", "failed", "cancelled"}:
            return status
        time.sleep(0.2)
    raise AssertionError("运行未在预期时间内结束")


def _configure(
    client,
    *,
    wordlist: Path,
    rules: Path,
    mask_ladder: tuple[str, ...] = (),
    hybrid_masks: tuple[str, ...] = (),
    **overrides,
):
    executor = client.app.state.real_executor
    settings = dataclasses.replace(
        executor.settings,
        wordlist_path=wordlist,
        wordlist_rule_paths=(rules,),
        mask_ladder=mask_ladder,
        hybrid_masks=hybrid_masks,
        adaptive_slicing=overrides.pop("adaptive_slicing", True),
        adaptive_probe_divisor=overrides.pop("adaptive_probe_divisor", 4),
        adaptive_min_slice_keys=overrides.pop("adaptive_min_slice_keys", 2),
        adaptive_growth=overrides.pop("adaptive_growth", 2.0),
        **overrides,
    )
    executor.settings = settings
    client.app.state.planner = RulePlanner(
        native_attacks=NativeAttackSettings(
            rule_paths=(str(rules),),
            wordlist_path=str(wordlist),
            mask_ladder=mask_ladder,
            hybrid_masks=hybrid_masks,
        )
    )


def _start_run(client, *, candidate_budget: int = 200_000) -> str:
    created = client.post(
        "/api/tasks",
        json={
            "name": "自适应切片",
            "target": {"type": "hash", "content": MD5_HEX, "file_id": None},
            "known_algorithm": "md5",
            "time_budget": 300,
            "candidate_budget": candidate_budget,
            "context": {},
        },
    )
    task_id = created.json()["task_id"]
    assert client.post(f"/api/tasks/{task_id}/analyze").status_code == 200
    assert client.post(f"/api/tasks/{task_id}/plan").status_code == 200
    started = client.post(f"/api/tasks/{task_id}/execute", json={"mode": "real"})
    assert started.status_code == 200, started.text
    return started.json()["run_id"]


def _entries(log_path: Path) -> list[dict]:
    if not log_path.is_file():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _flags(argv: list[str], flag: str) -> int | None:
    if flag not in argv:
        return None
    return int(argv[argv.index(flag) + 1])


def _slice_words(entry: dict, attack_mode: str = "0") -> int:
    """该批次实际喂给 hashcat 的词数（切片词表文件的行数）。

    argv 末尾的位置参数：`-a 0` 是 <target> <wordlist>；`-a 6` 是 <target> <wordlist> <mask>；
    `-a 7` 是 <target> <mask> <wordlist>；`-a 3` 只有 <target> <mask>。
    """
    from pathlib import Path

    argv = entry["argv"]
    if attack_mode == "0":
        path = Path(argv[-1])
    elif attack_mode == "6":
        path = Path(argv[-2])
    elif attack_mode == "7":
        path = Path(argv[-1])
    else:  # pragma: no cover - 掩码单元没有词表
        raise AssertionError("掩码单元没有切片词表")
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def test_wordlist_rule_arm_is_sliced_progressively(client, tmp_path, monkeypatch):
    """S1 词表 × 规则按词切片：探针 → 提交剩余，覆盖全部词且不重不漏。"""
    log_path = tmp_path / "sliced.jsonl"
    _install_fakes(client, tmp_path, monkeypatch, log_path, FAKE_HASHCAT_NO_RECOVER="1")

    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"word{i}" for i in range(16)) + "\n", encoding="utf-8")
    rules = tmp_path / "one.rule"
    rules.write_text(":\n", encoding="utf-8")  # 1 条恒等规则 → 放大倍数 1
    _configure(client, wordlist=wordlist, rules=rules)

    run_id = _start_run(client)
    _wait_terminal(client, run_id)

    launches = [
        entry for entry in _entries(log_path) if entry["attack_mode"] == "0" and entry["rules"]
    ]
    assert len(launches) >= 2, launches
    # 切片不允许再依赖 hashcat 的 -s/-l（与多规则文件/掩码文件冲突）
    for entry in launches:
        assert "-s" not in entry["argv"] and "-l" not in entry["argv"], entry["argv"]
    sizes = [_slice_words(entry) for entry in launches]
    # 探针 + 提交剩余：不重不漏地覆盖 16 条词
    assert sum(sizes) == 16, sizes
    assert sizes[0] == min(sizes), sizes
    assert sizes[0] < max(sizes), sizes


def test_sliced_batches_keep_accounting_invariants(client, tmp_path, monkeypatch):
    """每个切片的记账满足 tested ≤ 提交量 ≤ 该批次分配预算。"""
    log_path = tmp_path / "accounting.jsonl"
    _install_fakes(client, tmp_path, monkeypatch, log_path, FAKE_HASHCAT_NO_RECOVER="1")

    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"word{i}" for i in range(32)) + "\n", encoding="utf-8")
    rules = tmp_path / "one.rule"
    rules.write_text(":\n", encoding="utf-8")
    _configure(client, wordlist=wordlist, rules=rules, adaptive_probe_divisor=8)

    run_id = _start_run(client, candidate_budget=10_000)
    _wait_terminal(client, run_id)

    page = client.get(
        f"/api/runs/{run_id}/research/events", params={"limit": 200}
    ).json()
    batches = [
        event
        for event in page["items"]
        if event.get("event_type") == "decision_completed"
        and event["payload"]["decision"]["strategy_id"] == "S1"
    ]
    assert len(batches) >= 2, batches
    for event in batches:
        limit = event["payload"]["decision"]["candidate_limit"]
        feedback = event["payload"]["feedback"]
        assert feedback["tested"] <= feedback["candidate_count"] <= limit


def test_mask_ladder_is_split_into_predictable_slices(client, tmp_path, monkeypatch):
    """S6 掩码阶梯拆成子掩码切片，键空间不重不漏。"""
    log_path = tmp_path / "masks.jsonl"
    _install_fakes(client, tmp_path, monkeypatch, log_path, FAKE_HASHCAT_NO_RECOVER="1")

    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("alpha\n", encoding="utf-8")
    rules = tmp_path / "one.rule"
    rules.write_text(":\n", encoding="utf-8")
    _configure(
        client,
        wordlist=wordlist,
        rules=rules,
        mask_ladder=("?d?d?d?d",),
        adaptive_min_slice_keys=1_000,
        adaptive_growth=1.0,
        adaptive_probe_divisor=10,
    )

    run_id = _start_run(client)
    _wait_terminal(client, run_id)

    launches = [entry for entry in _entries(log_path) if entry["attack_mode"] == "3"]
    masks = [mask for entry in launches for mask in entry["masks"]]
    assert len(masks) >= 4, masks
    # 掩码切片必须可预测（固定首位）且不重复
    assert len(set(masks)) == len(masks), masks
    assert any(mask.endswith("?d?d?d") for mask in masks), masks


def test_slice_progress_follows_tested_not_requested(client, tmp_path, monkeypatch):
    """hashcat 被时间预算截断时，进度按**实测条数**推进，不能跳过没测过的词。"""
    log_path = tmp_path / "truncated.jsonl"
    _install_fakes(
        client,
        tmp_path,
        monkeypatch,
        log_path,
        FAKE_HASHCAT_NO_RECOVER="1",
        FAKE_HASHCAT_TESTED_CAP="3",  # 每批只实测 3 条候选
    )

    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"word{i}" for i in range(16)) + "\n", encoding="utf-8")
    rules = tmp_path / "one.rule"
    rules.write_text(":\n", encoding="utf-8")
    _configure(client, wordlist=wordlist, rules=rules, adaptive_probe_divisor=4)

    run_id = _start_run(client)
    _wait_terminal(client, run_id)

    launches = [
        entry for entry in _entries(log_path) if entry["attack_mode"] == "0" and entry["rules"]
    ]
    assert len(launches) >= 2, launches
    sizes = [_slice_words(entry) for entry in launches]
    # 首批请求 4 条词、实测只到 3 条 → 下一片从第 3 条词继续（16-3 = 13 条），
    # 而不是按"请求的 4 条"推进（那样会漏掉第 4 条词）。
    assert sizes[1] == 13, sizes
    for entry in launches:
        assert "-s" not in entry["argv"] and "-l" not in entry["argv"], entry["argv"]


def test_probe_is_time_based_and_bounded(client, tmp_path):
    """探针规模按实测速率 × 目标时长估算，并受"整段 1/divisor"与下限约束。"""
    executor = client.app.state.real_executor

    executor.settings = dataclasses.replace(
        executor.settings,
        adaptive_min_slice_keys=100_000,
        adaptive_probe_divisor=8,
        adaptive_probe_seconds=1.0,
        adaptive_probe_keys=2_000_000,
    )
    executor._rate_estimate = 0.0
    # 未知速率 → 用保守起点 2M，且不超过整段 1/8
    assert executor._probe_slice_keys(248_000_000) == 2_000_000
    assert executor._probe_slice_keys(4_000_000) == 500_000
    # 已知速率 → 速率 × 秒数（并仍受上限约束）
    executor._rate_estimate = 17_000_000.0
    assert executor._probe_slice_keys(248_000_000) == 17_000_000
    assert executor._probe_slice_keys(10_000_000) == 1_250_000
    # 下限：极小键空间也不会退化成 0
    executor._rate_estimate = 1.0
    assert executor._probe_slice_keys(50) == 50
    assert executor._probe_slice_keys(0) == 1
    # 实测速率用 EWMA 更新（含启动开销 → 保守）
    executor._rate_estimate = 0.0
    executor._observe_native_rate(20_000_000, 10.0)
    assert executor._rate_estimate == 2_000_000.0
    executor._observe_native_rate(60_000_000, 10.0)
    assert executor._rate_estimate == 4_000_000.0
    executor._observe_native_rate(0, 0.0)
    assert executor._rate_estimate == 4_000_000.0


def test_probe_never_smaller_than_one_word(client, tmp_path, monkeypatch):
    """探针/提交切片不得小于"单个词的放大倍数"，否则一条词都塞不进切片。

    （真实故障：词表 81 词 × 规则链 2.25M = 每词 225 万键，而探针按 1 秒估出 200 万键，
    小于一个词的键空间 → 切片为空 → 该单元被跳过，整个运行 0 命中。）
    """
    log_path = tmp_path / "word_floor.jsonl"
    _install_fakes(client, tmp_path, monkeypatch, log_path, FAKE_HASHCAT_NO_RECOVER="1")

    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"word{i}" for i in range(4)) + "\n", encoding="utf-8")
    rules = tmp_path / "many.rule"
    rules.write_text("\n".join(f"${digit}" for digit in "0123456789") + "\n", encoding="utf-8")
    _configure(
        client,
        wordlist=wordlist,
        rules=rules,
        adaptive_min_slice_keys=1,
        adaptive_probe_keys=5,
        adaptive_probe_divisor=8,
    )

    run_id = _start_run(client, candidate_budget=10_000)
    status = _wait_terminal(client, run_id)

    launches = [
        entry for entry in _entries(log_path) if entry["attack_mode"] == "0" and entry["rules"]
    ]
    assert launches, "S1 必须真的跑起来（不能被空切片跳过）"
    assert status["status"] == "completed", status
    assert all(entry["candidate_count"] >= 1 for entry in launches), launches


def test_adaptive_slicing_disabled_keeps_single_batch(client, tmp_path, monkeypatch):
    """关闭开关时退回"一次跑完"的旧行为，保证可回退。"""
    log_path = tmp_path / "single.jsonl"
    _install_fakes(client, tmp_path, monkeypatch, log_path, FAKE_HASHCAT_NO_RECOVER="1")

    wordlist = tmp_path / "dict.txt"
    wordlist.write_text("\n".join(f"word{i}" for i in range(32)) + "\n", encoding="utf-8")
    rules = tmp_path / "one.rule"
    rules.write_text(":\n", encoding="utf-8")
    _configure(client, wordlist=wordlist, rules=rules, adaptive_slicing=False)

    run_id = _start_run(client)
    _wait_terminal(client, run_id)

    launches = [
        entry for entry in _entries(log_path) if entry["attack_mode"] == "0" and entry["rules"]
    ]
    assert len(launches) == 1, launches
    assert _flags(launches[0]["argv"], "-s") is None
