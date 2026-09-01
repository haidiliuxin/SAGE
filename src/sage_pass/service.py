from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from .enums import TaskStatus
from .errors import AppError
from .models import FileModel, TaskModel
from .repository import FileRepository, TaskRepository
from .schemas import FileCreated, TargetInput, TaskCreate, TaskDetail


SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def public_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:12].upper()}"


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.ANALYZED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.ANALYZED: {TaskStatus.PLANNED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.PLANNED: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}


def task_to_schema(task: TaskModel) -> TaskDetail:
    return TaskDetail(
        task_id=task.task_id,
        name=task.name,
        target=TargetInput(
            type=task.target_type,
            content=task.target_content,
            file_id=task.file_id,
        ),
        known_algorithm=task.known_algorithm,
        time_budget=task.time_budget,
        candidate_budget=task.candidate_budget,
        context=task.context,
        status=task.status,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def create_task(session: Session, payload: TaskCreate) -> TaskModel:
    if payload.target.file_id:
        file_item = FileRepository(session).get(payload.target.file_id)
        if file_item is None:
            raise AppError(
                "INVALID_TASK",
                "目标文件不存在",
                status_code=422,
                details={"field": "target.file_id"},
            )
    timestamp = now_iso()
    task = TaskModel(
        task_id=public_id("T"),
        name=payload.name,
        target_type=payload.target.type.value,
        target_content=payload.target.content,
        file_id=payload.target.file_id,
        known_algorithm=payload.known_algorithm,
        time_budget=payload.time_budget,
        candidate_budget=payload.candidate_budget,
        context=payload.context.model_dump(),
        status=TaskStatus.CREATED.value,
        created_at=timestamp,
        updated_at=timestamp,
    )
    return TaskRepository(session).add(task)


def require_task(session: Session, task_id: str) -> TaskModel:
    task = TaskRepository(session).get(task_id)
    if task is None:
        raise AppError(
            "TASK_NOT_FOUND",
            "任务不存在",
            status_code=404,
            details={"task_id": task_id},
        )
    return task


def update_task_status(
    session: Session, task: TaskModel, requested: TaskStatus
) -> TaskModel:
    current = TaskStatus(task.status)
    if requested == current:
        return task
    if requested not in ALLOWED_TRANSITIONS[current]:
        raise AppError(
            "INVALID_TASK",
            "非法的任务状态转换",
            status_code=409,
            details={"from": current.value, "to": requested.value},
        )
    task.status = requested.value
    task.updated_at = now_iso()
    return TaskRepository(session).save(task)


async def store_upload(
    session: Session,
    upload: UploadFile,
    upload_dir: Path,
    max_upload_bytes: int,
) -> FileModel:
    original_name = Path(upload.filename or "upload.bin").name[:255]
    suffix = Path(original_name).suffix.lower()[:16]
    file_id = public_id("F")
    stored_name = f"{file_id}{suffix}"
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / stored_name
    digest = hashlib.sha256()
    size = 0
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(dir=upload_dir, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_upload_bytes:
                    raise AppError(
                        "INVALID_TASK",
                        "上传文件超过大小限制",
                        status_code=413,
                        details={"max_bytes": max_upload_bytes},
                    )
                digest.update(chunk)
                temp_file.write(chunk)
        if size == 0:
            raise AppError(
                "INVALID_TASK", "上传文件不能为空", status_code=422, details={}
            )
        os.replace(temp_path, destination)
        temp_path = None
        item = FileModel(
            file_id=file_id,
            original_name=original_name,
            stored_name=stored_name,
            content_type=upload.content_type,
            size=size,
            sha256=digest.hexdigest(),
            created_at=now_iso(),
        )
        try:
            return FileRepository(session).add(item)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    finally:
        await upload.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def file_to_schema(item: FileModel) -> FileCreated:
    return FileCreated(
        file_id=item.file_id,
        filename=item.original_name,
        content_type=item.content_type,
        size=item.size,
        sha256=item.sha256,
        created_at=item.created_at,
    )
