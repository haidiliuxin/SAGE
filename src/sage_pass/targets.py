"""统一目标提取器（A 负责）：Hash / ZIP / PDF / Office。

约定：
- 每个提取器实现 `supports(target_type)` 与 `extract(...)`；
- 提取结果统一为 `ExtractedTarget`（可执行的候选 Hash 行 + 建议 Hashcat 模式）；
- PDF / Office 调用成熟开源提取工具（pdf2john / office2john），不自行解析
  加密格式；工具缺失、未加密、旧格式或损坏时返回明确错误，不做静默降级。

真实执行只消费 `ExtractedTarget`，不再关心具体文件格式。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence

from .config import Settings
from .enums import TargetType
from .errors import AppError
from .zip_adapter import ZipHashExtractor


@dataclass(frozen=True, slots=True)
class ExtractedTarget:
    hashes: tuple[str, ...]
    source: str
    hashcat_mode: int | None = None
    algorithm: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


class TargetExtractor(Protocol):
    name: str

    def supports(self, target_type: TargetType) -> bool: ...

    def extract(
        self,
        *,
        target_type: TargetType,
        content: str | None = None,
        file_path: Path | None = None,
    ) -> ExtractedTarget: ...


class HashTargetExtractor:
    """行内 Hash 文本：只做行切分与规范化，模式由执行器按算法链解析。"""

    name = "hash"

    def supports(self, target_type: TargetType) -> bool:
        return target_type == TargetType.HASH

    def extract(
        self,
        *,
        target_type: TargetType,
        content: str | None = None,
        file_path: Path | None = None,
    ) -> ExtractedTarget:
        del target_type, file_path
        lines = tuple(
            line.strip()
            for line in (content or "").splitlines()
            if line.strip()
        )
        if not lines:
            raise AppError(
                "EXECUTION_FAILED",
                "Hash 目标缺少内容",
                status_code=422,
                details={},
            )
        return ExtractedTarget(hashes=lines, source=self.name)


class ZipTargetExtractor:
    """WinZip AES ZIP：调用 zip2john 提取 `$zip2$`（hashcat 13600）。"""

    name = "zip"

    def __init__(
        self, extractor: ZipHashExtractor, *, timeout: float = 30.0
    ) -> None:
        self.extractor = extractor
        self.timeout = timeout

    def supports(self, target_type: TargetType) -> bool:
        return target_type == TargetType.ZIP

    def extract(
        self,
        *,
        target_type: TargetType,
        content: str | None = None,
        file_path: Path | None = None,
    ) -> ExtractedTarget:
        del target_type, content
        if file_path is None:
            raise AppError(
                "EXECUTION_FAILED",
                "ZIP 目标缺少文件路径",
                status_code=422,
                details={},
            )
        extracted = self.extractor.extract(file_path, timeout=self.timeout)
        if not extracted.hashes:
            raise AppError(
                "EXECUTION_FAILED",
                "ZIP 中未提取到受支持的加密目标",
                status_code=422,
                details={"file_id": file_path.name},
            )
        return ExtractedTarget(
            hashes=extracted.hashes,
            source=self.name,
            hashcat_mode=extracted.hashcat_mode,
            algorithm="zip-aes",
        )


class _JohnHashExtractor:
    """通过 John the Ripper 的 *2john 脚本提取加密文件 Hash。

    - 命令可配置（SAGE_PDF2JOHN_PATH / SAGE_OFFICE2JOHN_PATH）；
    - 只解析输出中的 Hash 行，不自行解析加密格式；
    - 工具缺失 / 超时 / 未加密 / 不支持的格式都返回明确错误。
    """

    name = "john"

    def __init__(
        self, command: str | Sequence[str], *, timeout: float = 30.0
    ) -> None:
        self.command = (command,) if isinstance(command, str) else tuple(command)
        if not self.command:
            raise ValueError("extractor command cannot be empty")
        self.timeout = timeout

    def _run(self, file_path: Path) -> str:
        try:
            completed = subprocess.run(
                [*self.command, str(file_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppError(
                "ANALYZE_FAILED",
                f"{self.name} 提取超时",
                status_code=504,
                details={"timeout": self.timeout},
            ) from exc
        except (FileNotFoundError, OSError) as exc:
            raise AppError(
                "ANALYZE_FAILED",
                f"无法启动 {self.name} 提取工具，请检查对应的 *_PATH 配置",
                status_code=503,
                details={"reason": str(exc), "command": self.command[0]},
            ) from exc
        return "\n".join((completed.stdout, completed.stderr))


class PdfTargetExtractor(_JohnHashExtractor):
    """加密 PDF：调用 pdf2john 提取（hashcat 10400/10500/10600/10700/10510）。"""

    name = "pdf2john"

    # pdf2john 输出的 `$pdf$N$` 版本号 -> hashcat 模式（依据本机 hashcat -hh）。
    VERSION_MODES = {1: 10400, 2: 10500, 3: 10600, 4: 10700, 5: 10510}
    _HASH_RE = re.compile(r"(\$pdf\$\d\*[^\s:]+)")

    def supports(self, target_type: TargetType) -> bool:
        return target_type == TargetType.PDF

    def extract(
        self,
        *,
        target_type: TargetType,
        content: str | None = None,
        file_path: Path | None = None,
    ) -> ExtractedTarget:
        del target_type, content
        if file_path is None or not file_path.is_file():
            raise AppError(
                "EXECUTION_FAILED",
                "PDF 目标文件不存在",
                status_code=422,
                details={"filename": file_path.name if file_path else None},
            )
        output = self._run(file_path)
        hashes = tuple(dict.fromkeys(self._HASH_RE.findall(output)))
        if not hashes:
            raise AppError(
                "EXECUTION_FAILED",
                "未从 PDF 中提取到受支持的加密目标（可能未加密或使用了不支持的加密版本）",
                status_code=422,
                details={"extractor": self.name},
            )
        version_match = re.match(r"\$pdf\$(\d)\*", hashes[0])
        version = int(version_match.group(1)) if version_match else None
        mode = self.VERSION_MODES.get(version) if version is not None else None
        if mode is None:
            raise AppError(
                "EXECUTION_FAILED",
                "暂不支持该 PDF 加密版本",
                status_code=422,
                details={"version": version, "supported": sorted(self.VERSION_MODES)},
            )
        return ExtractedTarget(
            hashes=hashes,
            source=self.name,
            hashcat_mode=mode,
            algorithm="pdf",
        )


class OfficeTargetExtractor(_JohnHashExtractor):
    """加密 Office：调用 office2john 提取（hashcat 9700/9800/9400/9500/9600）。"""

    name = "office2john"

    # (匹配标记, hashcat 模式, 说明)
    SIGNATURES: tuple[tuple[str, int, str], ...] = (
        ("$oldoffice$0", 9700, "MS Office <= 2003 ($0/$1)"),
        ("$oldoffice$1", 9700, "MS Office <= 2003 ($0/$1)"),
        ("$oldoffice$3", 9800, "MS Office <= 2003 ($3/$4)"),
        ("$oldoffice$4", 9800, "MS Office <= 2003 ($3/$4)"),
        ("*2007*", 9400, "MS Office 2007"),
        ("*2010*", 9500, "MS Office 2010"),
        ("*2013*", 9600, "MS Office 2013+"),
    )
    _HASH_RE = re.compile(r"(\$(?:oldoffice|office)\$[^\s:]+)")

    def supports(self, target_type: TargetType) -> bool:
        return target_type == TargetType.OFFICE

    def extract(
        self,
        *,
        target_type: TargetType,
        content: str | None = None,
        file_path: Path | None = None,
    ) -> ExtractedTarget:
        del target_type, content
        if file_path is None or not file_path.is_file():
            raise AppError(
                "EXECUTION_FAILED",
                "Office 目标文件不存在",
                status_code=422,
                details={"filename": file_path.name if file_path else None},
            )
        output = self._run(file_path)
        hashes = tuple(dict.fromkeys(self._HASH_RE.findall(output)))
        if not hashes:
            raise AppError(
                "EXECUTION_FAILED",
                "未从 Office 文档中提取到受支持的加密目标（可能未加密或为旧版格式）",
                status_code=422,
                details={"extractor": self.name},
            )
        for marker, mode, label in self.SIGNATURES:
            if marker in hashes[0]:
                return ExtractedTarget(
                    hashes=hashes,
                    source=self.name,
                    hashcat_mode=mode,
                    algorithm="office",
                    warnings=(f"识别为 {label}",),
                )
        raise AppError(
            "EXECUTION_FAILED",
            "暂不支持该 Office 加密变体",
            status_code=422,
            details={"extractor": self.name, "hash_prefix": hashes[0][:24]},
        )


def build_target_extractors(
    settings: Settings,
    *,
    zip_extractor: ZipHashExtractor | None = None,
    pdf_extractor: PdfTargetExtractor | None = None,
    office_extractor: OfficeTargetExtractor | None = None,
    zip_timeout: float = 30.0,
) -> tuple[TargetExtractor, ...]:
    timeout = settings.extraction_timeout_seconds or zip_timeout
    return (
        HashTargetExtractor(),
        ZipTargetExtractor(
            zip_extractor or ZipHashExtractor(settings.zip2john_path),
            timeout=timeout,
        ),
        pdf_extractor
        or PdfTargetExtractor(settings.pdf2john_path, timeout=timeout),
        office_extractor
        or OfficeTargetExtractor(settings.office2john_path, timeout=timeout),
    )


def extract_target(
    extractors: Sequence[TargetExtractor],
    *,
    target_type: TargetType,
    content: str | None = None,
    file_path: Path | None = None,
) -> ExtractedTarget:
    for extractor in extractors:
        if extractor.supports(target_type):
            return extractor.extract(
                target_type=target_type, content=content, file_path=file_path
            )
    raise AppError(
        "EXECUTION_FAILED",
        f"暂不支持 {target_type.value} 目标的真实执行",
        status_code=422,
        details={"target_type": target_type.value},
    )
