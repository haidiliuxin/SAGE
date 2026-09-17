from __future__ import annotations

import json
import hashlib
import math
import os
import re
import shutil
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
    # Argon2 家族（模式号取自本机 hashcat -hh：34000 Argon2 / 70000 Argon2id bridged）
    "argon2": 34000,
    "argon2i": 34000,
    "argon2d": 34000,
    "argon2id": 70000,
}

# 算法别名 -> 规范化名称（保留 '-'/'_' 与大小写差异）
ALGORITHM_ALIASES = {
    "md5": "md5",
    "sha": "sha1",
    "sha1": "sha1",
    "sha-1": "sha1",
    "sha_1": "sha1",
    "sha256": "sha256",
    "sha-256": "sha256",
    "sha_256": "sha256",
    "sha512": "sha512",
    "sha-512": "sha512",
    "sha_512": "sha512",
    "bcrypt": "bcrypt",
    "bcrypt-sha256": "bcrypt",
    "blowfish": "bcrypt",
    "argon2": "argon2",
    "argon2i": "argon2i",
    "argon2d": "argon2d",
    "argon2id": "argon2id",
    "winzip": "winzip",
    "winzip-aes": "zip-aes",
    "zip-aes": "zip-aes",
    "zipaes": "zip-aes",
}


def normalize_algorithm_name(algorithm: str) -> str:
    """把算法名称/别名归一为内部规范名（未知名称原样小写返回）。"""
    normalized = algorithm.strip().casefold()
    return ALGORITHM_ALIASES.get(normalized, normalized)


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
    session_dir: str | None = None
    attack_mode: int = 0
    rule_files: tuple[str, ...] = ()
    inline_rules: tuple[str, ...] = ()
    masks: tuple[str, ...] = ()
    custom_charsets: tuple[str, ...] = ()


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
    normalized = normalize_algorithm_name(algorithm)
    try:
        return HASHCAT_MODES[normalized]
    except KeyError as exc:
        raise AppError(
            "EXECUTION_FAILED",
            "无法自动确定 Hashcat 模式，请显式提供 hashcat_mode",
            status_code=422,
            details={"algorithm": algorithm, "normalized": normalized},
        ) from exc


class HashcatHandle:
    def __init__(self, command: Sequence[str], job: HashcatJob) -> None:
        # 校验必须先于任何资源创建，避免异常路径遗留临时目录。
        targets = _validated_lines(job.target_hashes, "target_hashes")
        candidates = _validated_lines(
            job.candidates[: job.candidate_budget], "candidates"
        )
        masks = _validated_lines(job.masks, "masks")
        rule_files = _validated_lines(job.rule_files, "rule_files")
        inline_rules = _validated_lines(job.inline_rules, "inline_rules")
        custom_charsets = _validated_lines(job.custom_charsets, "custom_charsets")
        _validate_attack_contract(job, candidates, masks, rule_files, inline_rules)
        if not targets:
            raise AppError(
                "EXECUTION_FAILED", "Hashcat 目标为空", status_code=422, details={}
            )
        if not candidates and job.attack_mode != 3:
            raise AppError(
                "EXECUTION_FAILED", "真实执行候选集为空", status_code=422, details={}
            )
        if job.attack_mode == 3 and not masks:
            raise AppError(
                "EXECUTION_FAILED", "Hashcat 掩码攻击缺少 mask", status_code=422, details={}
            )
        self.job = job
        self.candidate_count = len(candidates) if candidates else len(masks)
        self._lock = threading.RLock()
        self._started = time.monotonic()
        self._stop_reason: str | None = None
        self._result: HashcatResult | None = None
        self._temp_dir = (
            None
            if job.session_dir is not None
            else tempfile.TemporaryDirectory(prefix="sage-hashcat-")
        )
        working_dir = (
            Path(job.session_dir)
            if job.session_dir is not None
            else Path(self._temp_dir.name)
        )
        working_dir.mkdir(parents=True, exist_ok=True)
        self._target_path = working_dir / "target.hash"
        self._wordlist_path = working_dir / "candidates.txt"
        self._outfile_path = working_dir / "recovered.txt"
        self._stdout_path = working_dir / "stdout.log"
        self._stderr_path = working_dir / "stderr.log"
        self._restore_path = working_dir / "session.restore"
        self._metadata_path = working_dir / "session.json"
        self._mask_path = working_dir / "masks.hcmask"
        self._inline_rule_path = working_dir / "inline.rule"
        resuming = self._restore_path.is_file()
        if resuming:
            self._validate_resume_metadata(
                targets,
                candidates,
                masks,
                rule_files,
                inline_rules,
                custom_charsets,
            )
        else:
            self._target_path.write_text("\n".join(targets) + "\n", encoding="utf-8")
            if candidates:
                self._wordlist_path.write_text(
                    "\n".join(candidates) + "\n", encoding="utf-8"
                )
            if len(masks) > 1:
                self._mask_path.write_text(
                    "\n".join(masks) + "\n", encoding="utf-8"
                )
            if inline_rules:
                self._inline_rule_path.write_text(
                    "\n".join(inline_rules) + "\n", encoding="utf-8"
                )
            self._write_session_metadata(
                targets,
                candidates,
                masks,
                rule_files,
                inline_rules,
                custom_charsets,
                lifecycle="prepared",
                restore_used=False,
            )
        self._stdout_file = self._stdout_path.open("ab" if resuming else "wb")
        self._stderr_file = self._stderr_path.open("ab" if resuming else "wb")

        common = [
            *command,
            "--session",
            _safe_session_name(job.run_id),
            "--restore-file-path",
            str(self._restore_path),
        ]
        args = [*common, "--restore"] if resuming else [
                *common,
                "--hash-type",
                str(job.hash_mode),
                "--attack-mode",
                str(job.attack_mode),
                "--runtime",
                str(max(1, math.ceil(job.timeout_seconds))),
                "--status",
                "--status-json",
                "--status-timer",
                "1",
                "--potfile-disable",
                "--restore-timer",
                "1",
                "--outfile",
                str(self._outfile_path),
                "--outfile-format",
                "1,2",
                "--separator",
                "\t",
                *_custom_charset_args(custom_charsets),
                *_rule_args(rule_files, self._inline_rule_path if inline_rules else None),
                str(self._target_path),
                *_attack_position_args(
                    job.attack_mode,
                    wordlist_path=self._wordlist_path,
                    mask_path=self._mask_path,
                    masks=masks,
                ),
            ]
        try:
            # hashcat 的 OpenCL 内核目录按进程工作目录解析（./OpenCL/），
            # 因此当命令指向真实可执行文件时，须以其所在目录作为 cwd。
            run_cwd = _executable_dir(command) or working_dir
            self._write_session_metadata(
                targets,
                candidates,
                masks,
                rule_files,
                inline_rules,
                custom_charsets,
                lifecycle="running",
                restore_used=resuming,
            )
            self._process = subprocess.Popen(
                args,
                cwd=run_cwd,
                stdin=subprocess.DEVNULL,
                stdout=self._stdout_file,
                stderr=self._stderr_file,
                shell=False,
            )
        except (FileNotFoundError, OSError) as exc:
            self._close_files()
            if self._temp_dir is not None:
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
            if self._temp_dir is not None:
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
        try:
            self._write_session_metadata(
                _validated_lines(self.job.target_hashes, "target_hashes"),
                _validated_lines(
                    self.job.candidates[: self.job.candidate_budget],
                    "candidates",
                ),
                _validated_lines(self.job.masks, "masks"),
                _validated_lines(self.job.rule_files, "rule_files"),
                _validated_lines(self.job.inline_rules, "inline_rules"),
                _validated_lines(self.job.custom_charsets, "custom_charsets"),
                lifecycle="finished",
                restore_used=_metadata_restore_used(self._metadata_path),
                result=self._result,
            )
        except Exception:
            pass
        return self._result

    def _validate_resume_metadata(
        self,
        targets: tuple[str, ...],
        candidates: tuple[str, ...],
        masks: tuple[str, ...],
        rule_files: tuple[str, ...],
        inline_rules: tuple[str, ...],
        custom_charsets: tuple[str, ...],
    ) -> None:
        if not self._metadata_path.is_file():
            raise AppError(
                "EXECUTION_FAILED",
                "Hashcat restore 文件缺少会话元数据，无法验证恢复安全性",
                status_code=409,
                details={"session_dir": str(self._metadata_path.parent)},
            )
        try:
            metadata = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppError(
                "EXECUTION_FAILED",
                "Hashcat 会话元数据损坏，无法恢复",
                status_code=409,
                details={"reason": str(exc)},
            ) from exc
        expected = _session_fingerprint(
            self.job,
            targets,
            candidates,
            masks,
            rule_files,
            inline_rules,
            custom_charsets,
        )
        if metadata.get("fingerprint") != expected:
            raise AppError(
                "EXECUTION_FAILED",
                "Hashcat restore 与当前批次不匹配，已拒绝恢复",
                status_code=409,
                details={"session_dir": str(self._metadata_path.parent)},
            )
        if not self._target_path.is_file():
            raise AppError(
                "EXECUTION_FAILED",
                "Hashcat 会话文件不完整，无法恢复",
                status_code=409,
                details={"session_dir": str(self._metadata_path.parent)},
            )
        if candidates and not self._wordlist_path.is_file():
            raise AppError(
                "EXECUTION_FAILED",
                "Hashcat 会话文件不完整，无法恢复",
                status_code=409,
                details={"session_dir": str(self._metadata_path.parent)},
            )

    def _write_session_metadata(
        self,
        targets: tuple[str, ...],
        candidates: tuple[str, ...],
        masks: tuple[str, ...],
        rule_files: tuple[str, ...],
        inline_rules: tuple[str, ...],
        custom_charsets: tuple[str, ...],
        *,
        lifecycle: str,
        restore_used: bool,
        result: HashcatResult | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "schema_version": 1,
            "run_id": self.job.run_id,
            "hash_mode": self.job.hash_mode,
            "attack_mode": self.job.attack_mode,
            "candidate_count": len(candidates),
            "candidate_budget": self.job.candidate_budget,
            "mask_count": len(masks),
            "rule_file_count": len(rule_files),
            "inline_rule_count": len(inline_rules),
            "timeout_seconds": self.job.timeout_seconds,
            "fingerprint": _session_fingerprint(
                self.job,
                targets,
                candidates,
                masks,
                rule_files,
                inline_rules,
                custom_charsets,
            ),
            "lifecycle": lifecycle,
            "restore_used": restore_used,
            "restore_exists": self._restore_path.is_file(),
            "target_file": str(self._target_path),
            "wordlist_file": str(self._wordlist_path),
            "mask_file": str(self._mask_path) if masks else None,
            "outfile": str(self._outfile_path),
        }
        if result is not None:
            payload["result"] = {
                "status": result.status.value,
                "tested": result.tested,
                "recovered": len(result.recovered),
                "exit_code": result.exit_code,
                "duration": result.duration,
                "message": result.message,
            }
        _atomic_json_write(self._metadata_path, payload)

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

    def cleanup_run_sessions(self, root: Path, run_id: str) -> None:
        """Remove completed run session files while keeping crash recovery safe."""
        prepared_root = root.resolve()
        target = (prepared_root / _safe_session_name(run_id)).resolve()
        if target.parent != prepared_root:
            raise ValueError("invalid hashcat session path")
        if target.is_dir():
            shutil.rmtree(target)


def _executable_dir(command: Sequence[str]) -> str | None:
    """若命令首项是真实文件的路径，返回其所在目录。

    hashcat 的 OpenCL 内核目录（./OpenCL/）按进程工作目录解析，因此
    通过完整路径调用 hashcat 时必须以其安装目录作为进程 cwd。
    """
    if not command:
        return None
    executable = command[0]
    if not isinstance(executable, str) or not executable:
        return None
    has_separator = os.path.sep in executable or (
            os.path.altsep is not None and os.path.altsep in executable
    )
    if not has_separator:
        return None
    resolved = os.path.abspath(executable)
    if not os.path.isfile(resolved):
        return None
    return os.path.dirname(resolved)


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


def _validate_attack_contract(
    job: HashcatJob,
    candidates: Sequence[str],
    masks: Sequence[str],
    rule_files: Sequence[str],
    inline_rules: Sequence[str],
) -> None:
    if job.attack_mode not in {0, 3, 6, 7}:
        raise AppError(
            "EXECUTION_FAILED",
            "暂仅支持 Hashcat 词表、掩码与混合攻击模式",
            status_code=422,
            details={"attack_mode": job.attack_mode},
        )
    if (rule_files or inline_rules) and job.attack_mode != 0:
        raise AppError(
            "EXECUTION_FAILED",
            "Hashcat 规则文件仅支持词表攻击模式",
            status_code=422,
            details={"attack_mode": job.attack_mode},
        )
    if job.attack_mode in {3, 6, 7} and not masks:
        raise AppError(
            "EXECUTION_FAILED",
            "Hashcat 掩码/混合攻击缺少 mask",
            status_code=422,
            details={"attack_mode": job.attack_mode},
        )
    if job.attack_mode in {6, 7} and not candidates:
        raise AppError(
            "EXECUTION_FAILED",
            "Hashcat 混合攻击缺少词表种子",
            status_code=422,
            details={"attack_mode": job.attack_mode},
        )


def _custom_charset_args(custom_charsets: Sequence[str]) -> list[str]:
    args: list[str] = []
    for index, value in enumerate(custom_charsets[:4], start=1):
        args.extend((f"-{index}", value))
    return args


def _rule_args(
    rule_files: Sequence[str],
    inline_rule_path: Path | None,
) -> list[str]:
    args: list[str] = []
    for path in rule_files:
        args.extend(("-r", path))
    if inline_rule_path is not None:
        args.extend(("-r", str(inline_rule_path)))
    return args


def _attack_position_args(
    attack_mode: int,
    *,
    wordlist_path: Path,
    mask_path: Path,
    masks: Sequence[str],
) -> list[str]:
    mask_arg = str(mask_path) if len(masks) > 1 else masks[0] if masks else ""
    if attack_mode == 0:
        return [str(wordlist_path)]
    if attack_mode == 3:
        return [mask_arg]
    if attack_mode == 6:
        return [str(wordlist_path), mask_arg]
    if attack_mode == 7:
        return [mask_arg, str(wordlist_path)]
    raise ValueError(f"unsupported hashcat attack mode: {attack_mode}")


def _session_fingerprint(
    job: HashcatJob,
    targets: Sequence[str],
    candidates: Sequence[str],
    masks: Sequence[str],
    rule_files: Sequence[str],
    inline_rules: Sequence[str],
    custom_charsets: Sequence[str],
) -> dict[str, object]:
    return {
        "hash_mode": job.hash_mode,
        "attack_mode": job.attack_mode,
        "targets_sha256": _lines_digest(targets),
        "candidates_sha256": _lines_digest(candidates),
        "masks_sha256": _lines_digest(masks),
        "rule_files_sha256": _lines_digest(rule_files),
        "inline_rules_sha256": _lines_digest(inline_rules),
        "custom_charsets_sha256": _lines_digest(custom_charsets),
        "target_count": len(targets),
        "candidate_count": len(candidates),
        "mask_count": len(masks),
    }


def _lines_digest(values: Sequence[str]) -> str:
    digest = json.JSONEncoder(
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode(list(values))
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def _metadata_restore_used(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("restore_used"))


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
