"""Hashcat / zip2john 适配器单元测试（基于可控仿真可执行程序）。"""

from __future__ import annotations

import os
import sys
import time

from pathlib import Path

import pytest

from sage_pass.enums import TaskStatus
from sage_pass.errors import AppError
from sage_pass.hashcat_adapter import (
    HashcatAdapter,
    HashcatJob,
    RecoveredCredential,
    _executable_dir,
    resolve_hashcat_mode,
)
from sage_pass.zip_adapter import ZipHashExtractor

from sim_binaries import write_sim_scripts

TARGET = "$2b$12$abcdefghijklmnopqrstuv"


def make_job(
    candidates=("alpha", "beta", "gamma"),
    *,
    budget: int = 100,
    timeout: float = 20.0,
) -> HashcatJob:
    return HashcatJob(
        run_id="R1",
        target_hashes=(TARGET,),
        hash_mode=3200,
        candidates=candidates,
        timeout_seconds=timeout,
        candidate_budget=budget,
    )


def fake_hashcat(tmp_path, monkeypatch, **env) -> HashcatAdapter:
    script, _ = write_sim_scripts(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return HashcatAdapter([sys.executable, str(script)])


# ------------------------------------------------------------------ 模式解析


def test_resolve_hashcat_mode_maps_common_algorithms():
    assert resolve_hashcat_mode("sha256") == 1400
    assert resolve_hashcat_mode("sha-512") == 1700
    assert resolve_hashcat_mode("bcrypt") == 3200
    assert resolve_hashcat_mode("zip-aes") == 13600


def test_resolve_hashcat_mode_rejects_unknown_algorithm():
    with pytest.raises(AppError) as excinfo:
        resolve_hashcat_mode("scrypt-custom")
    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "EXECUTION_FAILED"


def test_executable_dir_resolves_real_binary_path():
    # 完整路径（真实存在）→ 返回所在目录；hashcat 内核目录按 cwd 解析。
    assert _executable_dir((sys.executable,)) == os.path.dirname(sys.executable)
    # 纯命令名 / 空 → None（保持临时目录作为 cwd）。
    assert _executable_dir(("hashcat",)) is None
    assert _executable_dir(()) is None
    # 带路径但不存在 → None。
    assert _executable_dir((r"C:\definitely\missing\hashcat.exe",)) is None


# ------------------------------------------------------------ Hashcat 适配器


def test_handle_recovers_first_candidate_and_reports_progress(
    tmp_path, monkeypatch
):
    adapter = fake_hashcat(tmp_path, monkeypatch)
    result = adapter.start(make_job()).wait()

    assert result.status == TaskStatus.COMPLETED
    assert result.exit_code == 0
    assert result.recovered == (
        RecoveredCredential(target=TARGET, plaintext="alpha"),
    )
    assert result.tested == 3
    assert result.message == "Hashcat 已恢复目标"


def test_handle_decodes_hex_plaintext(tmp_path, monkeypatch):
    adapter = fake_hashcat(
        tmp_path, monkeypatch, FAKE_HASHCAT_HEX_PLAIN="1"
    )
    result = adapter.start(make_job()).wait()
    assert result.recovered[0].plaintext == "alpha"


def test_handle_no_recovery_reports_exhausted(tmp_path, monkeypatch):
    adapter = fake_hashcat(
        tmp_path, monkeypatch, FAKE_HASHCAT_NO_RECOVER="1"
    )
    result = adapter.start(make_job()).wait()
    assert result.status == TaskStatus.COMPLETED
    assert result.recovered == ()
    assert result.tested == 3
    assert result.message == "候选集已执行完毕，未恢复目标"


def test_handle_truncates_candidates_to_budget(tmp_path, monkeypatch):
    adapter = fake_hashcat(tmp_path, monkeypatch)
    job = make_job(("a", "b", "c", "d"), budget=2)
    result = adapter.start(job).wait()
    assert result.tested == 2
    assert len(result.recovered) == 1


def test_timeout_stops_long_run(tmp_path, monkeypatch):
    adapter = fake_hashcat(tmp_path, monkeypatch, FAKE_HASHCAT_SLOW="8")
    started = time.monotonic()
    result = adapter.start(make_job(timeout=0.5)).wait()
    elapsed = time.monotonic() - started

    assert elapsed < 5
    assert result.status == TaskStatus.COMPLETED
    assert "时间预算" in result.message
    assert result.exit_code is not None


def test_stop_cancels_running_handle(tmp_path, monkeypatch):
    adapter = fake_hashcat(tmp_path, monkeypatch, FAKE_HASHCAT_SLOW="30")
    handle = adapter.start(make_job(timeout=60))
    time.sleep(0.3)
    result = handle.stop()

    assert result.status == TaskStatus.CANCELLED
    assert result.message == "执行已停止"
    assert handle.poll() is result


def test_missing_hashcat_executable_raises_service_error():
    adapter = HashcatAdapter(("hashcat-not-installed-xyz",))
    with pytest.raises(AppError) as excinfo:
        adapter.start(make_job())
    assert excinfo.value.status_code == 503
    assert "无法启动 Hashcat" in excinfo.value.message


def test_empty_or_invalid_candidates_are_rejected(tmp_path, monkeypatch):
    adapter = fake_hashcat(tmp_path, monkeypatch)
    with pytest.raises(AppError) as excinfo:
        adapter.start(make_job(candidates=()))
    assert excinfo.value.status_code == 422
    assert "候选集为空" in excinfo.value.message

    with pytest.raises(AppError) as excinfo:
        adapter.start(make_job(candidates=("ok", "bad\nline")))
    assert excinfo.value.status_code == 422
    assert "非空单行文本" in excinfo.value.message


def test_hashcat_job_requires_budget_above_zero(tmp_path, monkeypatch):
    adapter = fake_hashcat(tmp_path, monkeypatch)
    with pytest.raises(AppError) as excinfo:
        adapter.start(
            HashcatJob(
                run_id="R1",
                target_hashes=(TARGET,),
                hash_mode=0,
                candidates=("a",),
                timeout_seconds=0,
                candidate_budget=0,
            )
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "BUDGET_EXCEEDED"


# ------------------------------------------------------------ zip2john 适配器


def _zip_file(tmp_path) -> Path:
    archive = tmp_path / "sample.zip"
    archive.write_bytes(b"PK\x03\x04 not really a zip")
    return archive


def fake_zip2john(tmp_path, monkeypatch, kind: str = "winzip") -> ZipHashExtractor:
    _, script = write_sim_scripts(tmp_path)
    monkeypatch.setenv("FAKE_ZIP2JOHN_KIND", kind)
    return ZipHashExtractor([sys.executable, str(script)])


def test_zip_extractor_extracts_winzip_aes_hash(tmp_path, monkeypatch):
    extractor = fake_zip2john(tmp_path, monkeypatch, kind="winzip")
    extracted = extractor.extract(_zip_file(tmp_path))
    assert len(extracted.hashes) == 1
    assert extracted.hashes[0].startswith("$zip2$")
    assert extracted.hashes[0].endswith("$/zip2$")
    assert extracted.hashcat_mode == 13600


def test_zip_extractor_rejects_legacy_pkzip(tmp_path, monkeypatch):
    extractor = fake_zip2john(tmp_path, monkeypatch, kind="pkzip")
    with pytest.raises(AppError) as excinfo:
        extractor.extract(_zip_file(tmp_path))
    assert excinfo.value.status_code == 422
    assert "仅支持 WinZip AES" in excinfo.value.message


def test_zip_extractor_reports_empty_extraction(tmp_path, monkeypatch):
    extractor = fake_zip2john(tmp_path, monkeypatch, kind="garbage")
    with pytest.raises(AppError) as excinfo:
        extractor.extract(_zip_file(tmp_path))
    assert excinfo.value.status_code == 422
    assert "未从 ZIP 中提取到受支持的加密目标" in excinfo.value.message
    assert excinfo.value.details["zip2john_exit_code"] == 1


def test_zip_extractor_missing_executable_raises_service_error(tmp_path):
    extractor = ZipHashExtractor(("zip2john-not-installed-xyz",))
    with pytest.raises(AppError) as excinfo:
        extractor.extract(_zip_file(tmp_path))
    assert excinfo.value.status_code == 503
    assert "无法启动 zip2john" in excinfo.value.message


def test_zip_extractor_validates_archive_path(tmp_path, monkeypatch):
    extractor = fake_zip2john(tmp_path, monkeypatch)
    with pytest.raises(AppError) as excinfo:
        extractor.extract(tmp_path / "missing.zip")
    assert excinfo.value.status_code == 422
    assert excinfo.value.message == "ZIP 目标文件无效"
