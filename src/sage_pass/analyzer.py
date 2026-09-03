from __future__ import annotations

import re

from sqlalchemy.orm import Session

from .enums import TargetType, TaskStatus, VerificationCost
from .errors import AppError
from .models import PRIRModel
from .repository import PRIRRepository
from .schemas import PRIR, TaskContext, TaskDetail
from .service import now_iso


class MockAnalyzer:
    def __init__(self, session: Session) -> None:
        self.session = session

    def analyze(self, task: TaskDetail) -> PRIR:
        algorithm, salt, cost, confidence, warnings = _analyze_target(task)
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
) -> tuple[str, bool | None, VerificationCost, float, list[str]]:
    warnings: list[str] = []
    if task.known_algorithm:
        return task.known_algorithm, None, _cost_for_algorithm(task.known_algorithm), 0.9, warnings

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

    if task.target.type in {TargetType.ZIP, TargetType.PDF, TargetType.OFFICE}:
        warnings.append("第一周仅基于文件元数据生成 PRIR，未解析加密结构")
        return "unknown", None, VerificationCost.UNKNOWN, 0.45, warnings

    warnings.append("目标类型未知，无法确认算法")
    return "unknown", None, VerificationCost.UNKNOWN, 0.25, warnings


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
