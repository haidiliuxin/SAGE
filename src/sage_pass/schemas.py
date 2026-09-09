from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    ExecutionMode,
    PlannerType,
    StrategyId,
    TargetType,
    TaskStatus,
    VerificationCost,
)


class TargetInput(BaseModel):
    type: TargetType
    content: str | None = None
    file_id: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "TargetInput":
        if self.type == TargetType.HASH and not self.content:
            raise ValueError("hash 目标必须提供 target.content")
        if self.type in {TargetType.ZIP, TargetType.PDF, TargetType.OFFICE} and not self.file_id:
            raise ValueError("文件目标必须提供 target.file_id")
        if self.type == TargetType.UNKNOWN and not (self.content or self.file_id):
            raise ValueError("unknown 目标必须提供 content 或 file_id")
        return self


class TaskContext(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)
    region: str | None = None
    organization: str | None = None
    description: str | None = None


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target: TargetInput
    known_algorithm: str | None = Field(default=None, max_length=100)
    time_budget: int = Field(gt=0)
    candidate_budget: int = Field(gt=0)
    context: TaskContext = Field(default_factory=TaskContext)


class TaskCreated(BaseModel):
    task_id: str
    status: TaskStatus
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskDetail(TaskCreated):
    name: str
    target: TargetInput
    known_algorithm: str | None
    time_budget: int
    candidate_budget: int
    context: TaskContext
    updated_at: datetime


class TaskList(BaseModel):
    items: list[TaskDetail]
    total: int
    limit: int
    offset: int


class TaskStatusUpdate(BaseModel):
    status: TaskStatus


class PRIR(BaseModel):
    task_id: str
    target_type: TargetType
    algorithm: str = "unknown"
    salt: bool | None = None
    verification_cost: VerificationCost = VerificationCost.UNKNOWN
    context_available: bool
    candidate_space: int | None = Field(default=None, ge=0)
    time_budget: int = Field(gt=0)
    candidate_budget: int = Field(gt=0)
    status: TaskStatus = TaskStatus.ANALYZED
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class StrategyItem(BaseModel):
    strategy_id: StrategyId
    strategy_name: str
    priority: int = Field(gt=0)
    time_budget: int = Field(ge=0)
    candidate_budget: int = Field(ge=0)
    reason: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class StrategyPlan(BaseModel):
    task_id: str
    planner_type: PlannerType = PlannerType.MOCK
    total_time_budget: int = Field(gt=0)
    strategies: list[StrategyItem]
    status: TaskStatus = TaskStatus.PLANNED
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_budget(self) -> "StrategyPlan":
        allocated = sum(item.time_budget for item in self.strategies)
        if allocated > self.total_time_budget:
            raise ValueError("策略时间预算之和不能超过任务总时间预算")
        return self


class ExecutionRequest(BaseModel):
    mode: ExecutionMode = ExecutionMode.MOCK
    candidates: list[str] = Field(default_factory=list, max_length=100_000)
    hashcat_mode: int | None = Field(default=None, ge=0, le=99_999)
    timeout: int | None = Field(default=None, gt=0, le=86_400)

    @model_validator(mode="after")
    def validate_candidates(self) -> "ExecutionRequest":
        for index, candidate in enumerate(self.candidates):
            if not candidate or len(candidate) > 1024 or "\n" in candidate or "\r" in candidate:
                raise ValueError(f"candidates[{index}] 必须为 1～1024 个字符的单行文本")
        return self


class ExecutionStarted(BaseModel):
    task_id: str
    run_id: str
    status: TaskStatus
    started_at: datetime


class RunStatus(BaseModel):
    task_id: str
    run_id: str
    status: TaskStatus
    progress: float = Field(ge=0.0, le=1.0)
    current_strategy: StrategyId | None = None
    elapsed_time: float = Field(ge=0.0)
    tested: int = Field(ge=0)
    recovered: int = Field(ge=0)
    message: str


class StrategyResult(BaseModel):
    strategy_id: StrategyId
    time: float = Field(ge=0.0)
    tested: int = Field(ge=0)
    recovered: int = Field(ge=0)
    success_rate: float = Field(ge=0.0)


class RecoveredItem(BaseModel):
    target: str
    plaintext: str


class RunResult(BaseModel):
    task_id: str
    run_id: str
    status: TaskStatus
    total_time: float = Field(ge=0.0)
    total_tested: int = Field(ge=0)
    total_recovered: int = Field(ge=0)
    strategy_results: list[StrategyResult]
    finished_at: datetime
    recovered_items: list[RecoveredItem] = Field(default_factory=list)
    message: str | None = None


class FileCreated(BaseModel):
    file_id: str
    filename: str
    content_type: str | None
    size: int
    sha256: str
    created_at: datetime


class PatternKnowledgeResponse(BaseModel):
    pattern_id: int
    scope: str
    pattern_type: str
    pattern_signature: str
    feature_data: dict[str, Any]
    observation_count: int = Field(ge=0)
    task_count: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    last_seen_at: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail
