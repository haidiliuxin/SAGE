from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import AppError


WINZIP_MODE = 13600
WINZIP_PATTERN = re.compile(r"(\$zip2\$\*.*?\*\$/zip2\$)")
PKZIP_PATTERN = re.compile(r"(\$pkzip2\$.*?\*\$/pkzip2\$)")

# 传统 PKZIP（ZipCrypto）在 hashcat 中按压缩方式与文件数分为多个模式。
# 实测结论：17225 同时接受单文件压缩/未压缩与多文件压缩/混合；仅“只含校验和”的
# 变体需要 17230。单文件且压缩方式一致时仍使用更精确的 17200 / 17210。
PKZIP_SINGLE_COMPRESSED_MODE = 17200
PKZIP_SINGLE_UNCOMPRESSED_MODE = 17210
PKZIP_MIXED_MODE = 17225
PKZIP_CHECKSUM_ONLY_MODE = 17230
PKZIP_MODE_BY_METHOD = {0: PKZIP_SINGLE_UNCOMPRESSED_MODE, 8: PKZIP_SINGLE_COMPRESSED_MODE}
PKZIP_ALGORITHM = "zip-legacy"


@dataclass(frozen=True, slots=True)
class ExtractedZipTarget:
    hashes: tuple[str, ...]
    hashcat_mode: int = WINZIP_MODE
    algorithm: str = "zip-aes"
    warnings: tuple[str, ...] = ()


def _pkzip2_fields(value: str) -> list[str]:
    body = value
    if body.startswith("$pkzip2$"):
        body = body[len("$pkzip2$") :]
    for suffix in ("*$/pkzip2$", "$/pkzip2$"):
        if body.endswith(suffix):
            body = body[: -len(suffix)]
            break
    return body.split("*")


def _is_hex(value: str) -> bool:
    return bool(value) and all(character in "0123456789abcdefABCDEF" for character in value)


def pkzip2_hashcat_mode(value: str) -> int:
    """由 `$pkzip2$` 哈希头推断 hashcat 模式（无法判定时回退到通吃的 17225）。"""
    fields = _pkzip2_fields(value)
    try:
        file_count = int(fields[0])
        data_type = int(fields[2])
    except (IndexError, ValueError):
        return PKZIP_MIXED_MODE

    single_file = data_type == 2 or file_count <= 1
    if single_file:
        if len(fields) > 9 and fields[9].isdigit():
            return PKZIP_MODE_BY_METHOD.get(int(fields[9]), PKZIP_SINGLE_COMPRESSED_MODE)
        return PKZIP_SINGLE_COMPRESSED_MODE

    long_hex = [field for field in fields[3:] if len(field) >= 16 and _is_hex(field)]
    if long_hex and all(len(field) == 32 for field in long_hex):
        return PKZIP_CHECKSUM_ONLY_MODE
    return PKZIP_MIXED_MODE


def _resolve_pkzip2(hashes: tuple[str, ...]) -> tuple[tuple[str, ...], int, tuple[str, ...]]:
    """为一组 `$pkzip2$` 哈希选出一个统一模式（hashcat 单次运行只能用一个模式）。"""
    modes: dict[int, list[str]] = {}
    for value in hashes:
        modes.setdefault(pkzip2_hashcat_mode(value), []).append(value)
    if len(modes) == 1:
        mode = next(iter(modes))
        return tuple(modes[mode]), mode, ()

    common = max(modes, key=lambda candidate: (len(modes[candidate]), -candidate))
    kept = tuple(modes[common])
    dropped = sum(len(items) for mode, items in modes.items() if mode != common)
    warning = (
        f"该 ZIP 内的传统 PKZIP 目标需要 {len(modes)} 种 hashcat 模式，"
        f"已按模式 {common} 处理 {len(kept)} 个目标，另有 {dropped} 个目标本次未纳入"
    )
    return kept, common, (warning,)


class ZipHashExtractor:
    def __init__(self, command: str | Sequence[str] = "zip2john") -> None:
        self.command = (command,) if isinstance(command, str) else tuple(command)
        if not self.command:
            raise ValueError("zip2john command cannot be empty")

    def extract(self, archive: Path, timeout: float = 30) -> ExtractedZipTarget:
        resolved = archive.resolve()
        if not resolved.is_file() or resolved.suffix.lower() != ".zip":
            raise AppError(
                "ANALYZE_FAILED",
                "ZIP 目标文件无效",
                status_code=422,
                details={"filename": archive.name},
            )
        try:
            completed = subprocess.run(
                [*self.command, str(resolved)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppError(
                "ANALYZE_FAILED",
                "zip2john 解析超时",
                status_code=504,
                details={"timeout": timeout},
            ) from exc
        except (FileNotFoundError, OSError) as exc:
            raise AppError(
                "ANALYZE_FAILED",
                "无法启动 zip2john，请检查 SAGE_ZIP2JOHN_PATH",
                status_code=503,
                details={"reason": str(exc)},
            ) from exc

        output = "\n".join((completed.stdout, completed.stderr))
        hashes = tuple(dict.fromkeys(WINZIP_PATTERN.findall(output)))
        if hashes:
            return ExtractedZipTarget(hashes=hashes)

        legacy = tuple(dict.fromkeys(PKZIP_PATTERN.findall(output)))
        if legacy:
            kept, mode, warnings = _resolve_pkzip2(legacy)
            return ExtractedZipTarget(
                hashes=kept,
                hashcat_mode=mode,
                algorithm=PKZIP_ALGORITHM,
                warnings=warnings,
            )

        if "zip2john" in output and "not encrypted" in output.lower():
            message = "该 ZIP 未加密，无需口令评测"
        else:
            message = "未从 ZIP 中提取到受支持的加密目标"
        raise AppError(
            "ANALYZE_FAILED",
            message,
            status_code=422,
            details={"zip2john_exit_code": completed.returncode},
        )
