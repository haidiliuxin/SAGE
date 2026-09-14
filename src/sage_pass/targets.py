"""统一目标提取器（A 负责）：Hash / ZIP / PDF / Office。

约定：
- 每个提取器实现 `supports(target_type)` 与 `extract(...)`；
- 提取结果统一为 `ExtractedTarget`（可执行的候选 Hash 行 + 建议 Hashcat 模式）；
- PDF / Office 优先调用成熟开源工具（next week 交付；当前返回明确错误，
  不做静默降级）。

真实执行只消费 `ExtractedTarget`，不再关心具体文件格式。
"""

from __future__ import annotations

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


class PdfTargetExtractor:
    """PDF：下一周交付（调用成熟开源提取工具，不自行解析加密格式）。"""

    name = "pdf"

    def supports(self, target_type: TargetType) -> bool:
        return target_type == TargetType.PDF

    def extract(
        self,
        *,
        target_type: TargetType,
        content: str | None = None,
        file_path: Path | None = None,
    ) -> ExtractedTarget:
        del target_type, content, file_path
        raise AppError(
            "EXECUTION_FAILED",
            "PDF 目标提取将在下一阶段交付（当前真实执行支持 Hash 与 WinZip AES ZIP）",
            status_code=422,
            details={"extractor": self.name, "stage": "pending"},
        )


class OfficeTargetExtractor:
    """Office：下一周交付 DOCX/XLSX/PPTX（调用成熟开源提取工具）。"""

    name = "office"

    def supports(self, target_type: TargetType) -> bool:
        return target_type == TargetType.OFFICE

    def extract(
        self,
        *,
        target_type: TargetType,
        content: str | None = None,
        file_path: Path | None = None,
    ) -> ExtractedTarget:
        del target_type, content, file_path
        raise AppError(
            "EXECUTION_FAILED",
            "Office 目标提取将在下一阶段交付（当前真实执行支持 Hash 与 WinZip AES ZIP）",
            status_code=422,
            details={"extractor": self.name, "stage": "pending"},
        )


def build_target_extractors(
    settings: Settings,
    *,
    zip_extractor: ZipHashExtractor | None = None,
    zip_timeout: float = 30.0,
) -> tuple[TargetExtractor, ...]:
    return (
        HashTargetExtractor(),
        ZipTargetExtractor(
            zip_extractor or ZipHashExtractor(settings.zip2john_path),
            timeout=zip_timeout,
        ),
        PdfTargetExtractor(),
        OfficeTargetExtractor(),
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
