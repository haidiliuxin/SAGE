from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from .dependencies import get_session
from .analyzer import MockAnalyzer, prir_to_schema
from .enums import ExecutionMode, TaskStatus
from .errors import AppError
from .executor import MockExecutor
from .planner import MockPlanner
from .repository import FileRepository, PRIRRepository, TaskRepository
from .schemas import (
    ErrorResponse,
    ExecutionRequest,
    ExecutionStarted,
    FileCreated,
    PRIR,
    RunResult,
    RunStatus,
    StrategyPlan,
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


@router.post(
    "/tasks/{task_id}/analyze",
    response_model=PRIR,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    tags=["analyzer"],
)
def analyze_task(task_id: str, session: SessionDependency) -> PRIR:
    task = require_task(session, task_id)
    current = TaskStatus(task.status)
    if current == TaskStatus.ANALYZED:
        existing = PRIRRepository(session).get(task_id)
        if existing is not None:
            return prir_to_schema(existing)
    if current != TaskStatus.CREATED:
        raise AppError(
            "INVALID_TASK",
            "任务状态不允许分析",
            status_code=409,
            details={"from": current.value, "to": TaskStatus.ANALYZED.value},
        )
    prir = MockAnalyzer(session).analyze(task_to_schema(task))
    update_task_status(session, task, TaskStatus.ANALYZED)
    return prir


@router.post(
    "/tasks/{task_id}/plan",
    response_model=StrategyPlan,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    tags=["planner"],
)
def plan_task(task_id: str, session: SessionDependency) -> StrategyPlan:
    task = require_task(session, task_id)
    current = TaskStatus(task.status)
    if current not in {TaskStatus.ANALYZED, TaskStatus.PLANNED}:
        raise AppError(
            "INVALID_TASK",
            "任务状态不允许生成策略计划",
            status_code=409,
            details={"from": current.value, "to": TaskStatus.PLANNED.value},
        )
    prir_model = PRIRRepository(session).get(task_id)
    if prir_model is None:
        raise AppError(
            "PLAN_FAILED",
            "任务尚未生成 PRIR",
            status_code=409,
            details={"task_id": task_id},
        )
    plan = MockPlanner().plan(prir_to_schema(prir_model))
    if current == TaskStatus.ANALYZED:
        update_task_status(session, task, TaskStatus.PLANNED)
    return plan


@router.post(
    "/tasks/{task_id}/execute",
    response_model=ExecutionStarted,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    tags=["executor"],
)
def execute_task(
    task_id: str, payload: ExecutionRequest, session: SessionDependency
) -> ExecutionStarted:
    if payload.mode != ExecutionMode.MOCK:
        raise AppError(
            "EXECUTION_FAILED",
            "第一周仅支持 mock 执行模式",
            status_code=422,
            details={"mode": payload.mode.value},
        )
    task = require_task(session, task_id)
    current = TaskStatus(task.status)
    if current != TaskStatus.PLANNED:
        raise AppError(
            "INVALID_TASK",
            "任务状态不允许启动执行",
            status_code=409,
            details={"from": current.value, "to": TaskStatus.RUNNING.value},
        )
    prir_model = PRIRRepository(session).get(task_id)
    if prir_model is None:
        raise AppError(
            "EXECUTION_FAILED",
            "任务尚未生成 PRIR",
            status_code=409,
            details={"task_id": task_id},
        )
    plan = MockPlanner().plan(prir_to_schema(prir_model))
    started = MockExecutor(session).start(task_to_schema(task), plan)
    update_task_status(session, task, TaskStatus.RUNNING)
    return started


@router.get(
    "/runs/{run_id}/status",
    response_model=RunStatus,
    responses={404: {"model": ErrorResponse}},
    tags=["executor"],
)
def get_run_status(run_id: str, session: SessionDependency) -> RunStatus:
    status_result = MockExecutor(session).status(run_id)
    if status_result.status == TaskStatus.COMPLETED:
        task = require_task(session, status_result.task_id)
        if TaskStatus(task.status) == TaskStatus.RUNNING:
            update_task_status(session, task, TaskStatus.COMPLETED)
    return status_result


@router.get(
    "/runs/{run_id}/result",
    response_model=RunResult,
    responses={404: {"model": ErrorResponse}},
    tags=["executor"],
)
def get_run_result(run_id: str, session: SessionDependency) -> RunResult:
    result = MockExecutor(session).result(run_id)
    task = require_task(session, result.task_id)
    if TaskStatus(task.status) == TaskStatus.RUNNING:
        update_task_status(session, task, TaskStatus.COMPLETED)
    return result
