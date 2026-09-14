"""A 两周冲刺·第 1 周交付测试：

① 公共接口（ArmSpec/CandidateBatch/BatchOutcome/InformationProfile/DecisionEvent）；
② 算法识别链（hashcat_mode → known_algorithm → PRIR.algorithm）与别名归一；
③ Argon2 自动真实执行（模式映射、参数化 Hash 透传）；
④ adaptive 语义修正（SchedulerType、迁移报错、调度模式配置）；
⑤ 已完成真实运行的跨重启查询（持久化读取器）；
⑥ transfer 重复函数清理；
⑦ TargetExtractor 统一接口。
"""

from __future__ import annotations

import inspect
import json
import sys
import time

import pytest

from sage_pass.config import Settings
from sage_pass.database import Database
from sage_pass.enums import PlannerType, SchedulerType, StrategyId, TargetType
from sage_pass.errors import AppError
from sage_pass.hashcat_adapter import (
    HashcatAdapter,
    normalize_algorithm_name,
    resolve_hashcat_mode,
)
from sage_pass.interfaces import (
    ArmSpec,
    BatchOutcome,
    CandidateBatch,
    DecisionEvent,
    InformationProfile,
)
from sage_pass.real_executor import RealExecutor
from sage_pass.scheduler import (
    BanditScheduler,
    FixedOrderScheduler,
    RoundRobinScheduler,
    build_scheduler,
)
from sage_pass.targets import (
    HashTargetExtractor,
    OfficeTargetExtractor,
    PdfTargetExtractor,
    ZipTargetExtractor,
    extract_target,
)
from sage_pass.zip_adapter import ZipHashExtractor
from sage_pass import transfer

from sim_binaries import write_sim_scripts

MD5_HEX = "0123456789abcdef0123456789abcdef"
ARGON2ID = (
    "$argon2id$v=19$m=4096,t=2,p=1$c2FsdHNhbHRzYWx0c2E$"
    "5f4dcc3b5aa765d61d8327deb882cf99"
)


def _install_fake(client, tmp_path, monkeypatch, **env):
    script, zip_script = write_sim_scripts(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    zip_extractor = ZipHashExtractor([sys.executable, str(zip_script)])
    client.app.state.zip_extractor = zip_extractor
    client.app.state.real_executor.zip_extractor = zip_extractor
    client.app.state.real_executor.hashcat = HashcatAdapter(
        [sys.executable, str(script)]
    )
    return script


def _prepare(client, payload) -> str:
    created = client.post("/api/tasks", json=payload)
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]
    analyzed = client.post(f"/api/tasks/{task_id}/analyze")
    assert analyzed.status_code == 200, analyzed.text
    planned = client.post(f"/api/tasks/{task_id}/plan")
    assert planned.status_code == 200, planned.text
    return task_id


def _wait_terminal(client, run_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}/status")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(0.05)
    pytest.fail(f"run 未结束：{last}")


# ---------------------------------------------------------------- ① 公共接口


def test_public_interfaces_are_frozen_and_exported():
    assert ArmSpec(strategy_id="S1", priority=1, candidate_budget=10, time_budget=5)
    batch = CandidateBatch(StrategyId.S5, ("abc",))
    assert batch.strategy_id == StrategyId.S5

    outcome = BatchOutcome(
        run_id="R1",
        arm_id="S1",
        strategy_id="S1",
        batch_index=1,
        candidate_count=100,
        tested=90,
        recovered=3,
        duration=1.5,
        status="completed",  # 兼容字符串状态
    )
    assert outcome.success_rate == pytest.approx(3 / 90)
    assert outcome.as_dict()["strategy_id"] == "S1"

    profile = InformationProfile(
        has_personal_info=True,
        info_types=("name", "birthday"),
        has_history=False,
    )
    assert profile.as_dict()["info_types"] == ["name", "birthday"]

    event = DecisionEvent(
        run_id="R1",
        round_index=1,
        arm_id="S1",
        strategy_id="S1",
        candidate_limit=500,
        time_limit=10.0,
        exploration=True,
    )
    assert event.as_dict()["exploration"] is True
    assert event.as_dict()["feedback"] is None


# ------------------------------------------------- ② 算法识别链 / 别名归一


def test_algorithm_aliases_are_normalized():
    assert normalize_algorithm_name("SHA-1") == "sha1"
    assert normalize_algorithm_name("sha_256") == "sha256"
    assert normalize_algorithm_name("Argon2id") == "argon2id"
    assert normalize_algorithm_name("WinZip-AES") == "zip-aes"
    assert resolve_hashcat_mode("sha-1") == 100
    assert resolve_hashcat_mode("sha_512") == 1700


def test_real_execution_uses_prir_algorithm_when_known_algorithm_missing(
    client, tmp_path, monkeypatch
):
    log_path = tmp_path / "hashcat.jsonl"
    _install_fake(client, tmp_path, monkeypatch, FAKE_HASHCAT_LOG=str(log_path))
    # 不填 known_algorithm：Analyzer 识别 md5，Real 模式应能自动执行。
    task_id = _prepare(
        client,
        {
            "name": "chain",
            "target": {"type": "hash", "content": MD5_HEX, "file_id": None},
            "known_algorithm": None,
            "time_budget": 30,
            "candidate_budget": 5000,
            "context": {},
        },
    )
    started = client.post(
        f"/api/tasks/{task_id}/execute", json={"mode": "real"}
    )
    assert started.status_code == 200, started.text
    terminal = _wait_terminal(client, started.json()["run_id"])
    assert terminal["status"] == "completed", terminal
    payloads = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert payloads and payloads[0]["hash_type"] == "0"


def test_real_execution_requires_mode_for_unknown_algorithm(
    client, tmp_path, monkeypatch
):
    _install_fake(client, tmp_path, monkeypatch)
    task_id = _prepare(
        client,
        {
            "name": "unknown-algo",
            "target": {"type": "hash", "content": "not-a-known-hash", "file_id": None},
            "known_algorithm": None,
            "time_budget": 30,
            "candidate_budget": 1000,
            "context": {},
        },
    )
    response = client.post(
        f"/api/tasks/{task_id}/execute", json={"mode": "real"}
    )
    assert response.status_code == 422
    message = response.json()["error"]["message"]
    assert "hashcat_mode" in message and "Analyzer" in message

    # 显式模式仍可覆盖未知算法。
    override = client.post(
        f"/api/tasks/{task_id}/execute",
        json={"mode": "real", "hashcat_mode": 0},
    )
    assert override.status_code == 200, override.text


# ------------------------------------------------------------ ③ Argon2


def test_argon2id_is_detected_mapped_and_passed_through_intact(
    client, tmp_path, monkeypatch
):
    log_path = tmp_path / "argon2.jsonl"
    _install_fake(client, tmp_path, monkeypatch, FAKE_HASHCAT_LOG=str(log_path))
    created = client.post(
        "/api/tasks",
        json={
            "name": "argon2",
            "target": {"type": "hash", "content": ARGON2ID, "file_id": None},
            "known_algorithm": None,
            "time_budget": 30,
            "candidate_budget": 2000,
            "context": {},
        },
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]

    prir = client.post(f"/api/tasks/{task_id}/analyze")
    assert prir.status_code == 200, prir.text
    assert prir.json()["algorithm"] == "argon2id"
    assert prir.json()["verification_cost"] == "high"
    planned = client.post(f"/api/tasks/{task_id}/plan")
    assert planned.status_code == 200, planned.text

    started = client.post(
        f"/api/tasks/{task_id}/execute", json={"mode": "real"}
    )
    assert started.status_code == 200, started.text
    terminal = _wait_terminal(client, started.json()["run_id"])
    assert terminal["status"] == "completed", terminal
    payloads = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert payloads
    # Argon2id 使用 hashcat 70000；参数化 Hash 完整透传、不被截断。
    assert payloads[0]["hash_type"] == "70000"
    assert payloads[0]["targets"] == [ARGON2ID]


def test_argon2_modes_are_registered():
    assert resolve_hashcat_mode("argon2") == 34000
    assert resolve_hashcat_mode("argon2i") == 34000
    assert resolve_hashcat_mode("argon2id") == 70000
    with pytest.raises(AppError):
        resolve_hashcat_mode("argon3")


# --------------------------------------------------- ④ adaptive / scheduler


def test_adaptive_planner_config_reports_migration(monkeypatch):
    monkeypatch.setenv("SAGE_PLANNER_TYPE", "adaptive")
    with pytest.raises(RuntimeError) as excinfo:
        Settings.from_env()
    assert "SAGE_SCHEDULER_TYPE" in str(excinfo.value)

    monkeypatch.setenv("SAGE_PLANNER_TYPE", "nonsense")
    with pytest.raises(RuntimeError):
        Settings.from_env()

    monkeypatch.setenv("SAGE_PLANNER_TYPE", "rule")
    monkeypatch.setenv("SAGE_SCHEDULER_TYPE", "unknown-scheduler")
    with pytest.raises(RuntimeError):
        Settings.from_env()

    monkeypatch.setenv("SAGE_SCHEDULER_TYPE", "fixed")
    settings = Settings.from_env()
    assert settings.planner_type == PlannerType.RULE
    assert settings.scheduler_type == SchedulerType.FIXED


def _arms():
    return [
        ArmSpec(strategy_id="S1", priority=1, candidate_budget=4, time_budget=5),
        ArmSpec(strategy_id="S2", priority=2, candidate_budget=4, time_budget=5),
    ]


def test_scheduler_factory_builds_fixed_and_round_robin():
    fixed = build_scheduler(
        SchedulerType.FIXED,
        _arms(),
        total_candidate_budget=8,
        total_time_budget=10,
    )
    assert isinstance(fixed, FixedOrderScheduler)
    assert fixed.select({"S1": 2, "S2": 2}).strategy_id == "S1"

    robin = build_scheduler(
        SchedulerType.ROUND_ROBIN,
        _arms(),
        total_candidate_budget=8,
        total_time_budget=10,
    )
    assert isinstance(robin, RoundRobinScheduler)
    first = robin.select({"S1": 2, "S2": 2}).strategy_id
    second = robin.select({"S1": 2, "S2": 2}).strategy_id
    assert first != second

    bandit = build_scheduler(
        SchedulerType.HEURISTIC_BANDIT,
        _arms(),
        total_candidate_budget=8,
        total_time_budget=10,
    )
    assert isinstance(bandit, BanditScheduler)

    with pytest.raises(ValueError):
        build_scheduler(
            SchedulerType.UCB,
            _arms(),
            total_candidate_budget=8,
            total_time_budget=10,
        )


def test_system_config_exposes_planner_and_scheduler(client):
    response = client.get("/api/system/config")
    assert response.status_code == 200
    body = response.json()
    assert body["planner_type"] in {"mock", "rule", "llm"}
    assert body["scheduler_type"] in {item.value for item in SchedulerType}


# --------------------------------------------- ⑤ 跨重启查询（持久化读取器）


def _run_real_to_completion(client, tmp_path, monkeypatch, password="secret123"):
    _install_fake(client, tmp_path, monkeypatch)
    task_id = _prepare(
        client,
        {
            "name": "persisted",
            "target": {"type": "hash", "content": MD5_HEX, "file_id": None},
            "known_algorithm": "md5",
            "time_budget": 30,
            "candidate_budget": 2000,
            "context": {},
        },
    )
    started = client.post(
        f"/api/tasks/{task_id}/execute",
        json={"mode": "real", "candidates": [password]},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    _wait_terminal(client, run_id)
    result = client.get(f"/api/runs/{run_id}/result").json()
    return task_id, run_id, result


def test_completed_real_run_is_queryable_after_restart(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sage_pass.main import create_app

    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'p.db').as_posix()}",
        upload_dir=tmp_path / "u",
        max_upload_bytes=1024,
        cors_origins=(),
    )
    with TestClient(create_app(settings)) as client:
        task_id, run_id, original = _run_real_to_completion(
            client, tmp_path, monkeypatch
        )
        assert original["total_recovered"] == 1

    # 进程内 registry 为空（模拟重启），结果必须由持久化记录重建。
    with TestClient(create_app(settings)) as restarted:
        status = restarted.get(f"/api/runs/{run_id}/status")
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["status"] == "completed"
        assert body["tested"] == original["total_tested"]

        result = restarted.get(f"/api/runs/{run_id}/result")
        assert result.status_code == 200, result.text
        rebuilt = result.json()
        assert rebuilt["total_tested"] == original["total_tested"]
        assert rebuilt["recovered_items"] == original["recovered_items"]
        assert rebuilt["status"] == "completed"
        assert restarted.get(f"/api/tasks/{task_id}").json()["status"] == "completed"


def test_corrupt_run_record_returns_clear_error(tmp_path):
    from sage_pass.models import RunRecordModel
    from sage_pass.repository import RunRecordRepository

    db = Database(f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    db.create_all()
    with db.session_factory() as session:
        RunRecordRepository(session).add(
            RunRecordModel(
                run_id="RBROKEN",
                task_id="T-BROKEN",
                mode="real",
                status="completed",
                started_at="2026-01-01T00:00:00+08:00",
                snapshot={"broken": True},
                progress={},
                created_at="2026-01-01T00:00:00+08:00",
                updated_at="2026-01-01T00:00:00+08:00",
            )
        )
    executor = RealExecutor(
        session_factory=db.session_factory,
        settings=Settings.from_env(),
    )
    assert executor.has_record("RBROKEN") is True
    with pytest.raises(AppError) as excinfo:
        executor.load_persisted_result("RBROKEN")
    assert excinfo.value.status_code == 409
    assert "损坏" in excinfo.value.message
    db.dispose()


# ------------------------------------------------ ⑥ transfer 重复函数清理


def test_transfer_seed_helper_is_defined_once():
    source = inspect.getsource(transfer)
    assert source.count("def current_task_transfer_seeds") == 1
    seeds = transfer.current_task_transfer_seeds(["123456"], {"keywords": ["张三"]})
    assert seeds[0] == "123456"
    assert "张三" in seeds


# ------------------------------------------------------ ⑦ TargetExtractor


def test_target_extractors_cover_hash_zip_pdf_office(tmp_path, monkeypatch):
    script, zip_script = write_sim_scripts(tmp_path)
    monkeypatch.setenv("FAKE_ZIP2JOHN_KIND", "winzip")
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"PK\x03\x04fake")
    extractors = (
        HashTargetExtractor(),
        ZipTargetExtractor(ZipHashExtractor([sys.executable, str(zip_script)])),
        PdfTargetExtractor(),
        OfficeTargetExtractor(),
    )

    hash_target = extract_target(
        extractors, target_type=TargetType.HASH, content=f"{MD5_HEX}\n"
    )
    assert hash_target.hashes == (MD5_HEX,)
    assert hash_target.source == "hash"

    zip_target = extract_target(
        extractors, target_type=TargetType.ZIP, file_path=archive
    )
    assert zip_target.hashes and zip_target.hashcat_mode == 13600
    assert zip_target.algorithm == "zip-aes"

    for target_type, keyword in (
        (TargetType.PDF, "PDF"),
        (TargetType.OFFICE, "Office"),
    ):
        with pytest.raises(AppError) as excinfo:
            extract_target(
                extractors, target_type=target_type, file_path=archive
            )
        assert excinfo.value.status_code == 422
        assert keyword in excinfo.value.message
