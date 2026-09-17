from __future__ import annotations

import json
import sys

import pytest

from sage_pass.candidate_generator import CandidateGenerator
from sage_pass.enums import PlannerType, StrategyId, TaskStatus
from sage_pass.errors import AppError
from sage_pass.hashcat_adapter import HashcatAdapter, HashcatJob
from sage_pass.real_executor import _hashcat_job_for_strategy
from sage_pass.schemas import StrategyItem, StrategyPlan


TARGET = "0123456789abcdef0123456789abcdef"


def plan() -> StrategyPlan:
    return StrategyPlan(
        task_id="T-STREAM",
        planner_type=PlannerType.RULE,
        total_time_budget=20,
        strategies=[
            StrategyItem(
                strategy_id=StrategyId.S1,
                strategy_name="Baseline",
                priority=1,
                time_budget=10,
                candidate_budget=3,
                reason="stream",
            ),
            StrategyItem(
                strategy_id=StrategyId.S2,
                strategy_name="Rule",
                priority=2,
                time_budget=10,
                candidate_budget=3,
                reason="stream",
                parameters={"all_lower": True, "all_upper": True},
            ),
        ],
    )


def test_plan_stream_is_lazy_and_restores_cross_generator_dedupe():
    generator = CandidateGenerator(baseline_candidates=("Alpha", "beta"))
    stream = generator.open_plan_stream(plan())
    initial = stream.snapshot()
    assert initial["total_accepted"] == 0
    assert initial["seen_digests"] == []
    assert initial["arms"]["S1"]["generator_state"]["cursor"] == 0
    assert initial["arms"]["S2"]["generator_state"]["cursor"] == 0

    first = stream.pull(StrategyId.S1, 1)
    assert first.candidates == ("Alpha",)
    snapshot = stream.snapshot()
    json.dumps(snapshot)

    expected = stream.pull(StrategyId.S2, 3)
    restored = generator.restore_plan_stream(snapshot)
    actual = restored.pull(StrategyId.S2, 3)
    assert actual.candidates == expected.candidates
    assert actual.records == expected.records
    assert "Alpha" not in actual.candidates


def test_hashcat_session_uses_persistent_restore_metadata(tmp_path):
    log_path = tmp_path / "argv.json"
    script = (
        "import json,sys; "
        f"open({str(log_path)!r},'w',encoding='utf-8').write(json.dumps(sys.argv[1:])); "
        "print('{\"progress\":[0,0]}'); raise SystemExit(1)"
    )
    session_dir = tmp_path / "sessions" / "R1-S1-1"
    adapter = HashcatAdapter((sys.executable, "-c", script))
    job = HashcatJob(
        run_id="R1-S1-1",
        target_hashes=(TARGET,),
        hash_mode=0,
        candidates=("alpha", "beta"),
        timeout_seconds=10,
        candidate_budget=2,
        session_dir=str(session_dir),
    )
    result = adapter.start(job).wait()
    argv = json.loads(log_path.read_text(encoding="utf-8"))
    assert result.status == TaskStatus.COMPLETED
    assert "--session" in argv
    assert "--restore-file-path" in argv
    # hashcat 7.1.2 没有 --restore-timer（真实工具会报 unknown option），恢复文件默认自动写入。
    assert "--restore-timer" not in argv
    assert "--restore-disable" not in argv
    assert (session_dir / "candidates.txt").read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_hashcat_uses_restore_command_when_checkpoint_exists(tmp_path):
    log_path = tmp_path / "restore-argv.json"
    script = (
        "import json,sys; "
        f"open({str(log_path)!r},'w',encoding='utf-8').write(json.dumps(sys.argv[1:])); "
        "raise SystemExit(1)"
    )
    session_dir = tmp_path / "sessions" / "R2-S1-1"
    adapter = HashcatAdapter((sys.executable, "-c", script))
    adapter.start(HashcatJob(
        run_id="R2-S1-1",
        target_hashes=(TARGET,),
        hash_mode=0,
        candidates=("alpha",),
        timeout_seconds=10,
        candidate_budget=1,
        session_dir=str(session_dir),
    )).wait()
    (session_dir / "session.restore").write_bytes(b"checkpoint")
    result = adapter.start(HashcatJob(
        run_id="R2-S1-1",
        target_hashes=(TARGET,),
        hash_mode=0,
        candidates=("alpha",),
        timeout_seconds=10,
        candidate_budget=1,
        session_dir=str(session_dir),
    )).wait()
    argv = json.loads(log_path.read_text(encoding="utf-8"))
    assert result.status == TaskStatus.COMPLETED
    assert argv[-1] == "--restore"
    assert "--attack-mode" not in argv
    metadata = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    assert metadata["restore_used"] is True
    assert metadata["result"]["status"] == "completed"


def test_hashcat_restore_rejects_mismatched_candidate_file(tmp_path):
    script = "raise SystemExit(1)"
    session_dir = tmp_path / "sessions" / "R3-S1-1"
    adapter = HashcatAdapter((sys.executable, "-c", script))
    adapter.start(HashcatJob(
        run_id="R3-S1-1",
        target_hashes=(TARGET,),
        hash_mode=0,
        candidates=("alpha",),
        timeout_seconds=10,
        candidate_budget=1,
        session_dir=str(session_dir),
    )).wait()
    (session_dir / "session.restore").write_bytes(b"checkpoint")

    with pytest.raises(AppError, match="restore 与当前批次不匹配"):
        adapter.start(HashcatJob(
            run_id="R3-S1-1",
            target_hashes=(TARGET,),
            hash_mode=0,
            candidates=("beta",),
            timeout_seconds=10,
            candidate_budget=1,
            session_dir=str(session_dir),
        ))


def test_executor_maps_strategy_parameters_to_native_hashcat_attack():
    mask_job = _hashcat_job_for_strategy(
        run_id="R-mask",
        target_hashes=(TARGET,),
        hash_mode=0,
        candidates=("?l?l?d?d",),
        timeout_seconds=10,
        candidate_budget=1,
        parameters={"hashcat_masks": ["?l?l?d?d"]},
        session_dir=None,
    )
    assert mask_job.attack_mode == 3
    assert mask_job.candidates == ()
    assert mask_job.masks == ("?l?l?d?d",)

    hybrid_job = _hashcat_job_for_strategy(
        run_id="R-hybrid",
        target_hashes=(TARGET,),
        hash_mode=0,
        candidates=("seed",),
        timeout_seconds=10,
        candidate_budget=1,
        parameters={
            "hashcat_hybrid_mask": "?d?d",
            "hashcat_hybrid_position": "right",
        },
        session_dir=None,
    )
    assert hybrid_job.attack_mode == 6
    assert hybrid_job.candidates == ("seed",)
    assert hybrid_job.masks == ("?d?d",)
