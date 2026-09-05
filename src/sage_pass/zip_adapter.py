from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import AppError


WINZIP_MODE = 13600
WINZIP_PATTERN = re.compile(r"(\$zip2\$\*.*?\*\$/zip2\$)")


@dataclass(frozen=True, slots=True)
class ExtractedZipTarget:
    hashes: tuple[str, ...]
    hashcat_mode: int = WINZIP_MODE


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
        if "$pkzip2$" in output:
            raise AppError(
                "ANALYZE_FAILED",
                "当前 ZIP 适配器仅支持 WinZip AES（$zip2$），不支持传统 PKZIP",
                status_code=422,
                details={"supported_hashcat_mode": WINZIP_MODE},
            )
        raise AppError(
            "ANALYZE_FAILED",
            "未从 ZIP 中提取到受支持的加密目标",
            status_code=422,
            details={"zip2john_exit_code": completed.returncode},
        )
