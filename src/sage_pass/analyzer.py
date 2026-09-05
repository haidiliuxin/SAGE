from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from .enums import TargetType, TaskStatus, VerificationCost
from .errors import AppError
from .models import PRIRModel
from .repository import FileRepository, PRIRRepository
from .schemas import PRIR, TaskContext, TaskDetail
from .service import now_iso
from .zip_adapter import ZipHashExtractor

ZIP_ALGORITHM = "zip-aes"


class MockAnalyzer:
    def __init__(
        self,
        session: Session,
        *,
        zip_extractor: ZipHashExtractor | None = None,
        upload_dir: Path | None = None,
    ) -> None:
        self.session = session
        self.zip_extractor = zip_extractor
        self.upload_dir = upload_dir

    def analyze(self, task: TaskDetail) -> PRIR:
        algorithm, salt, cost, confidence, warnings = _analyze_target(
            task,
            session=self.session,
            zip_extractor=self.zip_extractor,
            upload_dir=self.upload_dir,
        )
        prir = PRIR(
            task_id=task.task_id,
            target_type=task.target.type,
            algorithm=algorithm,
            salt=salt,
            verification_cost=cost,
            context_available=_has_context(task.context),
            candidate_space=None,
            time_budget=task.time_budget,
            candidate_budget=task.candidate_budget,
            status=TaskStatus.ANALYZED,
            confidence=confidence,
            warnings=warnings,
        )
        _save_prir(self.session, prir)
        return prir


def prir_to_schema(item: PRIRModel) -> PRIR:
    return PRIR(
        task_id=item.task_id,
        target_type=item.target_type,
        algorithm=item.algorithm,
        salt=item.salt,
        verification_cost=item.verification_cost,
        context_available=item.context_available,
        candidate_space=item.candidate_space,
        time_budget=item.time_budget,
        candidate_budget=item.candidate_budget,
        status=item.status,
        confidence=item.confidence,
        warnings=item.warnings,
    )


def _save_prir(session: Session, prir: PRIR) -> PRIRModel:
    repository = PRIRRepository(session)
    timestamp = now_iso()
    item = repository.get(prir.task_id)
    values = {
        "target_type": prir.target_type.value,
        "algorithm": prir.algorithm,
        "salt": prir.salt,
        "verification_cost": prir.verification_cost.value,
        "context_available": prir.context_available,
        "candidate_space": prir.candidate_space,
        "time_budget": prir.time_budget,
        "candidate_budget": prir.candidate_budget,
        "status": prir.status.value,
        "confidence": prir.confidence,
        "warnings": prir.warnings,
        "updated_at": timestamp,
    }
    if item is None:
        item = PRIRModel(task_id=prir.task_id, created_at=timestamp, **values)
        return repository.add(item)
    for key, value in values.items():
        setattr(item, key, value)
    return repository.save(item)


def _has_context(context: TaskContext) -> bool:
    return any(
        [
            context.keywords,
            context.years,
            context.region,
            context.organization,
            context.description,
        ]
    )


def _analyze_target(
    task: TaskDetail,
    *,
    session: Session,
    zip_extractor: ZipHashExtractor | None,
    upload_dir: Path | None,
) -> tuple[str, bool | None, VerificationCost, float, list[str]]:
    warnings: list[str] = []
    if task.known_algorithm:
        return (
            task.known_algorithm,
            _salt_for_algorithm(task.known_algorithm),
            _cost_for_algorithm(task.known_algorithm),
            0.9,
            warnings,
        )

    if task.target.type == TargetType.HASH:
        if not task.target.content:
            raise AppError(
                "ANALYZE_FAILED",
                "Hash 目标缺少内容",
                status_code=422,
                details={"field": "target.content"},
            )
        algorithm = _detect_hash_algorithm(task.target.content)
        if algorithm == "unknown":
            warnings.append("无法确认 Hash 算法")
            return algorithm, None, VerificationCost.UNKNOWN, 0.35, warnings
        return algorithm, _salt_for_algorithm(algorithm), _cost_for_algorithm(algorithm), 0.8, warnings

    if task.target.type == TargetType.ZIP:
        return _analyze_zip(
            task,
            session=session,
            zip_extractor=zip_extractor,
            upload_dir=upload_dir,
        )

    if task.target.type in {TargetType.PDF, TargetType.OFFICE}:
        warnings.append("该文件类型尚未接入真实解析，仅基于文件元数据生成 PRIR")
        return "unknown", None, VerificationCost.UNKNOWN, 0.45, warnings

    warnings.append("目标类型未知，无法确认算法")
    return "unknown", None, VerificationCost.UNKNOWN, 0.25, warnings


def _analyze_zip(
    task: TaskDetail,
    *,
    session: Session,
    zip_extractor: ZipHashExtractor | None,
    upload_dir: Path | None,
) -> tuple[str, bool | None, VerificationCost, float, list[str]]:
    warnings: list[str] = []
    if zip_extractor is None or upload_dir is None:
        warnings.append("ZIP 真实解析不可用（缺少 zip2john 配置或上传目录）")
        return "unknown", None, VerificationCost.UNKNOWN, 0.45, warnings

    if not task.target.file_id:
        warnings.append("ZIP 目标缺少文件引用，无法解析加密结构")
        return "unknown", None, VerificationCost.UNKNOWN, 0.45, warnings

    file_item = FileRepository(session).get(task.target.file_id)
    if file_item is None:
        warnings.append("ZIP 目标文件记录不存在，无法解析加密结构")
        return "unknown", None, VerificationCost.UNKNOWN, 0.45, warnings

    archive = upload_dir / file_item.stored_name
    if not archive.is_file():
        warnings.append("ZIP 目标文件已丢失，无法解析加密结构")
        return "unknown", None, VerificationCost.UNKNOWN, 0.45, warnings

    try:
        extracted = zip_extractor.extract(archive)
    except AppError as exc:
        if exc.status_code == 503:
            warnings.append(
                "未检测到 zip2john，ZIP 真实解析需要 John the Ripper 并配置 SAGE_ZIP2JOHN_PATH"
            )
        elif exc.status_code == 504:
            warnings.append("zip2john 解析超时，未解析加密结构")
        else:
            warnings.append(exc.message or "未能解析该 ZIP 的加密结构")
        return "unknown", None, VerificationCost.UNKNOWN, 0.45, warnings

    if not extracted.hashes:
        warnings.append("未从 ZIP 中提取到受支持的加密目标")
        return "unknown", None, VerificationCost.UNKNOWN, 0.45, warnings
    return (
        ZIP_ALGORITHM,
        True,
        VerificationCost.MEDIUM,
        0.85,
        warnings,
    )


def _detect_hash_algorithm(content: str) -> str:
    value = content.strip()
    if value.startswith(("$2a$", "$2b$", "$2y$")):
        return "bcrypt"
    if value.startswith("$argon2"):
        return "argon2"
    if re.fullmatch(r"[a-fA-F0-9]{32}", value):
        return "md5"
    if re.fullmatch(r"[a-fA-F0-9]{40}", value):
        return "sha1"
    if re.fullmatch(r"[a-fA-F0-9]{64}", value):
        return "sha256"
    if re.fullmatch(r"[a-fA-F0-9]{128}", value):
        return "sha512"
    return "unknown"


def _salt_for_algorithm(algorithm: str) -> bool | None:
    normalized = algorithm.lower()
    if normalized in {"bcrypt", "argon2"}:
        return True
    if normalized in {"md5", "sha1", "sha256", "sha512"}:
        return False
    return None


def _cost_for_algorithm(algorithm: str) -> VerificationCost:
    normalized = algorithm.lower()
    if normalized in {"bcrypt", "argon2"}:
        return VerificationCost.HIGH
    if normalized in {"sha256", "sha512"}:
        return VerificationCost.MEDIUM
    if normalized in {"md5", "sha1"}:
        return VerificationCost.LOW
    return VerificationCost.UNKNOWN
