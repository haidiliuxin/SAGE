from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .enums import TaskStatus
from .errors import AppError


HASHCAT_MODES = {
    "md5": 0,
    "sha1": 100,
    "sha-1": 100,
    "sha256": 1400,
    "sha-256": 1400,
    "sha512": 1700,
    "sha-512": 1700,
    "bcrypt": 3200,
    "winzip": 13600,
    "zip-aes": 13600,
}


@dataclass(frozen=True, slots=True)
class RecoveredCredential:
    target: str
    plaintext: str


@dataclass(frozen=True, slots=True)
class HashcatJob:
    run_id: str
    target_hashes: tuple[str, ...]
    hash_mode: int
    candidates: tuple[str, ...]
    timeout_seconds: float
    candidate_budget: int


@dataclass(frozen=True, slots=True)
class HashcatResult:
    status: TaskStatus
    duration: float
    tested: int
    recovered: tuple[RecoveredCredential, ...]
    exit_code: int | None
    message: str
    stdout: str
    stderr: str


def resolve_hashcat_mode(algorithm: str) -> int:
    normalized = algorithm.strip().lower()
    try:
        return HASHCAT_MODES[normalized]
    except KeyError as exc:
        raise AppError(
            "EXECUTION_FAILED",
            "无法自动确定 Hashcat 模式，请显式提供 hashcat_mode",
            status_code=422,
            details={"algorithm": algorithm},
        ) from exc


class HashcatHandle:
    def __init__(self, command: Sequence[str], job: HashcatJob) -> None:
        # 校验必须先于任何资源创建，避免异常路径遗留临时目录。
        targets = _validated_lines(job.target_hashes, "target_hashes")
        candidates = _validated_lines(
            job.candidates[: job.candidate_budget], "candidates"
        )
        if not targets:
            raise AppError(
                "EXECUTION_FAILED", "Hashcat 目标为空", status_code=422, details={}
            )
        if not candidates:
            raise AppError(
                "EXECUTION_FAILED", "真实执行候选集为空", status_code=422, details={}
            )
        self.job = job
        self.candidate_count = len(candidates)
        self._lock = threading.RLock()
        self._started = time.monotonic()
        self._stop_reason: str | None = None
        self._result: HashcatResult | None = None
        self._temp_dir = tempfile.TemporaryDirectory(prefix="sage-hashcat-")
        working_dir = Path(self._temp_dir.name)
        self._target_path = working_dir / "target.hash"
        self._wordlist_path = working_dir / "candidates.txt"
        self._outfile_path = working_dir / "recovered.txt"
        self._stdout_path = working_dir / "stdout.log"
        self._stderr_path = working_dir / "stderr.log"
        self._target_path.write_text("\n".join(targets) + "\n", encoding="utf-8")
        self._wordlist_path.write_text("\n".join(candidates) + "\n", encoding="utf-8")
        self._stdout_file = self._stdout_path.open("wb")
        self._stderr_file = self._stderr_path.open("wb")

        args = [
            *command,
            "--hash-type",
            str(job.hash_mode),
            "--attack-mode",
            "0",
            "--session",
            _safe_session_name(job.run_id),
            "--runtime",
            str(max(1, math.ceil(job.timeout_seconds))),
            "--status",
            "--status-json",
            "--status-timer",
            "1",
            "--potfile-disable",
            "--restore-disable",
            "--outfile",
            str(self._outfile_path),
            "--outfile-format",
            "1,2",
            "--separator",
            "\t",
            str(self._target_path),
            str(self._wordlist_path),
        ]
        try:
            self._process = subprocess.Popen(
                args,
                cwd=working_dir,
                stdin=subprocess.DEVNULL,
                stdout=self._stdout_file,
                stderr=self._stderr_file,
                shell=False,
            )
        except (FileNotFoundError, OSError) as exc:
            self._close_files()
            self._temp_dir.cleanup()
            raise AppError(
                "EXECUTION_FAILED",
                "无法启动 Hashcat，请检查 SAGE_HASHCAT_PATH",
                status_code=503,
                details={"reason": str(exc)},
            ) from exc
        self._timer = threading.Timer(job.timeout_seconds, self._on_timeout)
        self._timer.daemon = True
        self._timer.start()

    def poll(self) -> HashcatResult | None:
        with self._lock:
            if self._result is not None:
                return self._result
            exit_code = self._process.poll()
            if exit_code is None:
                return None
            return self._finalize(exit_code)

    def live_progress(self) -> int | None:
        """读取 stdout 日志中的最近一次进度（真实运行轮询用），结束后返回 None。"""
        with self._lock:
            if self._result is not None:
                return None
            return _parse_progress(_read_log(self._stdout_path))

    def wait(self, timeout: float | None = None) -> HashcatResult:
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("Hashcat 仍在运行") from exc
        result = self.poll()
        if result is None:  # pragma: no cover - process.wait guarantees termination
            raise RuntimeError("Hashcat result unavailable")
        return result

    def stop(self) -> HashcatResult:
        with self._lock:
            if self._result is not None:
                return self._result
            if self._process.poll() is None:
                self._stop_reason = "cancelled"
                self._terminate()
            return self._finalize(self._process.returncode)

    def close(self) -> None:
        with self._lock:
            if self._result is None and self._process.poll() is None:
                self._stop_reason = "cancelled"
                self._terminate()
            if self._result is None:
                self._finalize(self._process.returncode)
            self._temp_dir.cleanup()

    def _on_timeout(self) -> None:
        with self._lock:
            if self._result is not None or self._process.poll() is not None:
                return
            self._stop_reason = "timeout"
            self._terminate()
            self._finalize(self._process.returncode)

    def _terminate(self) -> None:
        self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)

    def _finalize(self, exit_code: int | None) -> HashcatResult:
        if self._result is not None:
            return self._result
        self._timer.cancel()
        self._close_files()
        stdout = _read_log(self._stdout_path)
        stderr = _read_log(self._stderr_path)
        recovered = _parse_outfile(self._outfile_path)
        tested = _parse_progress(stdout)
        if tested is None:
            tested = self.candidate_count if exit_code == 1 else len(recovered)
        tested = min(max(tested, len(recovered)), self.candidate_count)

        if self._stop_reason == "cancelled":
            status = TaskStatus.CANCELLED
            message = "执行已停止"
        elif self._stop_reason == "timeout":
            status = TaskStatus.COMPLETED
            message = "已达到时间预算，Hashcat 已停止"
        elif exit_code in {0, 1}:
            status = TaskStatus.COMPLETED
            message = "Hashcat 已恢复目标" if recovered else "候选集已执行完毕，未恢复目标"
        else:
            status = TaskStatus.FAILED
            message = "Hashcat 执行失败"

        self._result = HashcatResult(
            status=status,
            duration=max(0.0, time.monotonic() - self._started),
            tested=tested,
            recovered=recovered,
            exit_code=exit_code,
            message=message,
            stdout=stdout,
            stderr=stderr,
        )
        return self._result

    def _close_files(self) -> None:
        for stream in (self._stdout_file, self._stderr_file):
            if not stream.closed:
                stream.close()


class HashcatAdapter:
    def __init__(self, command: str | Sequence[str] = "hashcat") -> None:
        self.command = (command,) if isinstance(command, str) else tuple(command)
        if not self.command:
            raise ValueError("Hashcat command cannot be empty")

    def start(self, job: HashcatJob) -> HashcatHandle:
        if job.timeout_seconds <= 0 or job.candidate_budget <= 0:
            raise AppError(
                "BUDGET_EXCEEDED",
                "时间预算和候选预算必须大于 0",
                status_code=422,
                details={},
            )
        return HashcatHandle(self.command, job)


def _validated_lines(values: Sequence[str], field: str) -> tuple[str, ...]:
    for index, value in enumerate(values):
        if not value or "\n" in value or "\r" in value:
            raise AppError(
                "EXECUTION_FAILED",
                "Hashcat 输入必须为非空单行文本",
                status_code=422,
                details={"field": field, "index": index},
            )
    return tuple(values)


def _safe_session_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)[:64] or "sage"


def _read_log(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-64_000:]


def _parse_outfile(path: Path) -> tuple[RecoveredCredential, ...]:
    if not path.exists():
        return ()
    recovered: list[RecoveredCredential] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "\t" not in line:
            continue
        target, plaintext = line.rsplit("\t", 1)
        recovered.append(
            RecoveredCredential(target=target, plaintext=_decode_plaintext(plaintext))
        )
    return tuple(recovered)


def _decode_plaintext(value: str) -> str:
    match = re.fullmatch(r"\$HEX\[([0-9A-Fa-f]+)]", value)
    if match and len(match.group(1)) % 2 == 0:
        raw = bytes.fromhex(match.group(1))
        return raw.decode("utf-8", errors="replace")
    return value


def _parse_progress(output: str) -> int | None:
    latest: int | None = None
    for line in output.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            match = re.search(r"Progress\.*:\s*([0-9,]+)", line)
            if match:
                latest = int(match.group(1).replace(",", ""))
            continue
        progress = payload.get("progress")
        if isinstance(progress, list) and progress and isinstance(progress[0], int):
            latest = progress[0]
    return latest
