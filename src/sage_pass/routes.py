from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from .dependencies import get_session
from .repository import FileRepository, TaskRepository
from .schemas import (
    ErrorResponse,
    FileCreated,
    TaskCreate,
    TaskCreated,
    TaskDetail,
    TaskList,
    TaskStatusUpdate,
)
from .service import (
    create_task,
    file_to_schema,
    require_task,
    store_upload,
    task_to_schema,
    update_task_status,
)


router = APIRouter(prefix="/api")
SessionDependency = Annotated[Session, Depends(get_session)]


@router.post(
    "/files",
    response_model=FileCreated,
    status_code=status.HTTP_201_CREATED,
    responses={413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    tags=["files"],
)
async def upload_file(
    request: Request,
    session: SessionDependency,
    file: Annotated[UploadFile, File(description="待评测目标文件")],
) -> FileCreated:
    item = await store_upload(
        session,
        file,
        request.app.state.settings.upload_dir,
        request.app.state.settings.max_upload_bytes,
    )
    return file_to_schema(item)


@router.get(
    "/files/{file_id}",
    response_model=FileCreated,
    responses={404: {"model": ErrorResponse}},
    tags=["files"],
)
def get_file(file_id: str, session: SessionDependency) -> FileCreated:
    item = FileRepository(session).get(file_id)
    if item is None:
        from .errors import AppError

        raise AppError(
            "TASK_NOT_FOUND",
            "文件不存在",
            status_code=404,
            details={"file_id": file_id},
        )
    return file_to_schema(item)


@router.post(
    "/tasks",
    response_model=TaskCreated,
    status_code=status.HTTP_201_CREATED,
    responses={422: {"model": ErrorResponse}},
    tags=["tasks"],
)
def post_task(payload: TaskCreate, session: SessionDependency) -> TaskCreated:
    task = create_task(session, payload)
    return TaskCreated.model_validate(task)


@router.get("/tasks", response_model=TaskList, tags=["tasks"])
def list_tasks(
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskList:
    items, total = TaskRepository(session).list(limit=limit, offset=offset)
    return TaskList(
        items=[task_to_schema(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskDetail,
    responses={404: {"model": ErrorResponse}},
    tags=["tasks"],
)
def get_task(task_id: str, session: SessionDependency) -> TaskDetail:
    return task_to_schema(require_task(session, task_id))


@router.patch(
    "/tasks/{task_id}/status",
    response_model=TaskDetail,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    tags=["tasks"],
)
def patch_task_status(
    task_id: str, payload: TaskStatusUpdate, session: SessionDependency
) -> TaskDetail:
    task = require_task(session, task_id)
    return task_to_schema(update_task_status(session, task, payload.status))
