from __future__ import annotations

import inspect
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from .dependencies import get_session
from .analyzer import MockAnalyzer, prir_to_schema
from .enums import ExecutionMode, TaskStatus
from .errors import AppError
from .executor import MockExecutor
from .repository import (
    FileRepository,
    PRIRRepository,
    PatternKnowledgeRepository,
    StrategyRunRepository,
    TaskRepository,
)
from .run_control import RunControl
from .schemas import (
    ErrorResponse,
    ExecutionRequest,
    ExecutionStarted,
    FileCreated,
    PRIR,
    PatternKnowledgeResponse,
    RunResult,
    RunStatus,
    StrategyPlan,
    TaskCreate,
    TaskCreated,
    TaskDetail,
    TaskList,
    TaskStatusUpdate,
)
from .feedback import FeedbackConfig
from .transfer import KnowledgeSummary, load_transfer_knowledge
from .service import (
    create_task,
    file_to_schema,
    require_task,
    store_upload,
    task_to_schema,
    update_task_status,
)


router = APIRouter(prefix="/api")
LOGGER = logging.getLogger(__name__)
SessionDependency = Annotated[Session, Depends(get_session)]


def _feedback_config(request: Request) -> FeedbackConfig:
    settings = request.app.state.settings
    return FeedbackConfig(
        minimum_observations=settings.feedback_minimum_observations,
        minimum_tasks=settings.feedback_minimum_tasks,
        maximum_patterns_per_scope=settings.feedback_maximum_patterns_per_scope,
        recency_half_life_days=settings.feedback_recency_half_life_days,
    )


def _knowledge_for_prir(
    request: Request, session: Session, prir: PRIR
) -> tuple[list, KnowledgeSummary]:
    return load_transfer_knowledge(
        PatternKnowledgeRepository(session),
        target_type=prir.target_type.value,
        algorithm=prir.algorithm,
        candidate_budget=prir.candidate_budget,
        config=_feedback_config(request),
    )


def _plan_with_knowledge(planner, prir: PRIR, summary: KnowledgeSummary):
    parameters = inspect.signature(planner.plan).parameters
    if "knowledge_summary" in parameters:
        return planner.plan(prir, knowledge_summary=summary)
    return planner.plan(prir)


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
    request: Request,
    task_id: str,
    payload: TaskStatusUpdate,
    session: SessionDependency,
) -> TaskDetail:
    task = require_task(session, task_id)
    current = TaskStatus(task.status)
    run_ids = StrategyRunRepository(session).active_run_ids(task_id)
    control: RunControl = request.app.state.run_control
    if payload.status == TaskStatus.PAUSED and current == TaskStatus.RUNNING:
        control.pause_many(run_ids)
    elif payload.status == TaskStatus.RUNNING and current == TaskStatus.PAUSED:
        control.resume_many(run_ids)
    elif payload.status == TaskStatus.CANCELLED and current in {
        TaskStatus.RUNNING,
        TaskStatus.PAUSED,
    }:
        request.app.state.real_executor.cancel_task_runs(task_id)
        control.clear_many(run_ids)
    return task_to_schema(update_task_status(session, task, payload.status))


@router.post(
    "/tasks/{task_id}/analyze",
    response_model=PRIR,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    tags=["analyzer"],
)
def analyze_task(
    request: Request, task_id: str, session: SessionDependency
) -> PRIR:
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
    settings = request.app.state.settings
    prir = MockAnalyzer(
        session,
        zip_extractor=request.app.state.zip_extractor,
        upload_dir=settings.upload_dir,
    ).analyze(task_to_schema(task))
    update_task_status(session, task, TaskStatus.ANALYZED)
    return prir


@router.post(
    "/tasks/{task_id}/plan",
    response_model=StrategyPlan,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    tags=["planner"],
)
def plan_task(
    request: Request, task_id: str, session: SessionDependency
) -> StrategyPlan:
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
    prir = prir_to_schema(prir_model)
    _, knowledge_summary = _knowledge_for_prir(request, session, prir)
    plan = _plan_with_knowledge(request.app.state.planner, prir, knowledge_summary)
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
    request: Request,
    task_id: str,
    payload: ExecutionRequest,
    session: SessionDependency,
) -> ExecutionStarted:
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
    prir = prir_to_schema(prir_model)
    _, knowledge_summary = _knowledge_for_prir(request, session, prir)
    plan = _plan_with_knowledge(request.app.state.planner, prir, knowledge_summary)
    task_detail = task_to_schema(task)
    if payload.mode == ExecutionMode.REAL:
        started = request.app.state.real_executor.start(
            session, task_detail, plan, payload
        )
    elif payload.mode == ExecutionMode.MOCK:
        started = MockExecutor(session).start(task_detail, plan)
    else:  # pragma: no cover - 枚举已穷尽
        raise AppError(
            "EXECUTION_FAILED",
            "未知执行模式",
            status_code=422,
            details={"mode": payload.mode.value},
        )
    update_task_status(session, task, TaskStatus.RUNNING)
    return started


@router.get(
    "/runs/{run_id}/status",
    response_model=RunStatus,
    responses={404: {"model": ErrorResponse}},
    tags=["executor"],
)
def get_run_status(
    request: Request, run_id: str, session: SessionDependency
) -> RunStatus:
    real_executor = request.app.state.real_executor
    control = request.app.state.run_control
    if real_executor.has_run(run_id):
        status_result = real_executor.status(run_id)
        _sync_task_status_after_run(
            session, status_result.task_id, status_result.status
        )
        return status_result
    status_result = MockExecutor(session, control=control).status(run_id)
    if status_result.status == TaskStatus.COMPLETED:
        _sync_task_status_after_run(
            session, status_result.task_id, TaskStatus.COMPLETED
        )
    return status_result


@router.get(
    "/runs/{run_id}/result",
    response_model=RunResult,
    responses={404: {"model": ErrorResponse}},
    tags=["executor"],
)
def get_run_result(
    request: Request, run_id: str, session: SessionDependency
) -> RunResult:
    real_executor = request.app.state.real_executor
    if real_executor.has_run(run_id):
        result = real_executor.result(run_id)
        _sync_task_status_after_run(session, result.task_id, result.status)
        return result
    result = MockExecutor(session).result(run_id)
    _sync_task_status_after_run(session, result.task_id, TaskStatus.COMPLETED)
    if request.app.state.settings.feedback_enable_mock:
        try:
            request.app.state.real_executor.feedback_service.process_completed_run(
                run_id=result.run_id,
                task_id=result.task_id,
                recovered_items=result.recovered_items,
            )
        except Exception as exc:  # test-only opt-in must not change run outcome
            LOGGER.error(
                "mock feedback processing failed for run_id=%s type=%s",
                result.run_id,
                type(exc).__name__,
            )
    return result


@router.get(
    "/feedback/patterns",
    response_model=list[PatternKnowledgeResponse],
    tags=["feedback"],
)
def list_feedback_patterns(
    session: SessionDependency,
    target_type: str | None = None,
    algorithm: str | None = None,
    pattern_type: str | None = None,
    minimum_confidence: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[PatternKnowledgeResponse]:
    rows = PatternKnowledgeRepository(session).list(
        target_type=target_type,
        algorithm=algorithm.strip().casefold() if algorithm else None,
        pattern_type=pattern_type,
        minimum_confidence=minimum_confidence,
        limit=limit,
    )
    return [
        PatternKnowledgeResponse(
            pattern_id=row.id,
            scope=row.scope,
            pattern_type=row.pattern_type,
            pattern_signature=row.pattern_signature,
            feature_data=row.feature_data,
            observation_count=row.observation_count,
            task_count=row.task_count,
            confidence=row.confidence,
            last_seen_at=datetime.fromisoformat(row.last_seen_at),
        )
        for row in rows
    ]


def _sync_task_status_after_run(
    session: Session, task_id: str, target: TaskStatus
) -> None:
    """run 到达终态后，把任务状态同步到终态。

    任务可能停留在 running 或 paused；两者都可以向终态推进，避免
    结果已产生但任务仍悬挂。仍在执行中的 run 不处理。
    """
    if target in {TaskStatus.RUNNING, TaskStatus.PAUSED}:
        return
    task = require_task(session, task_id)
    current = TaskStatus(task.status)
    if current in {TaskStatus.RUNNING, TaskStatus.PAUSED}:
        update_task_status(session, task, target)
