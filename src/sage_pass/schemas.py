from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    ExecutionMode,
    InformationScenario,
    InformationType,
    PlannerType,
    SchedulerType,
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
    model_config = ConfigDict(extra="forbid")

    # Week 1 compatibility fields. ``keywords`` are treated as explicitly
    # authorized keywords and ``years`` as birthday/year information.
    keywords: list[str] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)
    region: str | None = None
    organization: str | None = None
    description: str | None = None

    # Explicit personal-information fields used by the information scenarios.
    name: str | None = None
    nickname: str | None = None
    username: str | None = None
    email_local_part: str | None = None
    phone_suffix: str | None = None
    birthday: str | None = None
    birth_year: int | None = None
    interest_words: list[str] = Field(default_factory=list)
    authorized_keywords: list[str] = Field(default_factory=list)

    @field_validator(
        "name", "nickname", "username", "email_local_part", "phone_suffix",
        "birthday", "region", "organization", "description",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        prepared = value.strip()
        if not prepared:
            return None
        if len(prepared) > 256 or "\n" in prepared or "\r" in prepared:
            raise ValueError("个人信息字段必须为不超过 256 字符的单行文本")
        return prepared

    @field_validator("keywords", "interest_words", "authorized_keywords")
    @classmethod
    def validate_terms(cls, values: list[str]) -> list[str]:
        prepared: list[str] = []
        for value in values:
            item = value.strip()
            if not item or len(item) > 256 or "\n" in item or "\r" in item:
                raise ValueError("信息词必须为 1～256 个字符的单行文本")
            prepared.append(item)
        return list(dict.fromkeys(prepared))

    @field_validator("birth_year")
    @classmethod
    def validate_birth_year(cls, value: int | None) -> int | None:
        if value is not None and not 1000 <= value <= 9999:
            raise ValueError("birth_year 必须为四位年份")
        return value


class InformationStructureSummary(BaseModel):
    """Value-free structural information safe for planning and persistence."""

    personal_field_count: int = Field(ge=0)
    personal_value_count: int = Field(ge=0)
    historical_length_buckets: dict[str, int] = Field(default_factory=dict)
    historical_character_classes: dict[str, int] = Field(default_factory=dict)


class InformationProfile(BaseModel):
    """The sole information boundary exposed to a Planner."""

    scenario: InformationScenario
    has_personal_information: bool
    information_types: list[InformationType] = Field(default_factory=list)
    has_historical_passwords: bool
    historical_password_count: int = Field(ge=0)
    has_pattern_knowledge: bool
    structure_summary: InformationStructureSummary


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    target: TargetInput
    known_algorithm: str | None = Field(default=None, max_length=100)
    time_budget: int = Field(gt=0)
    candidate_budget: int = Field(gt=0)
    context: TaskContext = Field(default_factory=TaskContext)
    historical_passwords: list[str] = Field(default_factory=list, max_length=1000)
    # 上传的词表文件（真实字典）：执行时交给 hashcat 原生读取，不经过候选列表。
    wordlist_file_id: str | None = Field(default=None, max_length=32)

    @field_validator("historical_passwords")
    @classmethod
    def validate_historical_passwords(cls, values: list[str]) -> list[str]:
        prepared: list[str] = []
        for value in values:
            if not value or len(value) > 1024 or "\n" in value or "\r" in value:
                raise ValueError("历史口令必须为 1～1024 个字符的单行文本")
            prepared.append(value)
        return list(dict.fromkeys(prepared))

    @model_validator(mode="after")
    def historical_passwords_are_separate(self) -> "TaskCreate":
        ordinary_terms = {
            *self.context.keywords,
            *self.context.interest_words,
            *self.context.authorized_keywords,
        }
        if ordinary_terms.intersection(self.historical_passwords):
            raise ValueError("historical_passwords 不能混入普通关键词")
        return self


class TaskCreated(BaseModel):
    task_id: str
    status: TaskStatus
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskDetail(TaskCreated):
    name: str
    target: TargetInput
    known_algorithm: str | None
    wordlist_file_id: str | None = None
    time_budget: int
    candidate_budget: int
    context: TaskContext
    # Available only to internal execution code. Pydantic excludes the plaintext
    # from every TaskDetail API response and repr.
    historical_passwords: list[str] = Field(
        default_factory=list, exclude=True, repr=False
    )
    information_profile: InformationProfile
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
    information_profile: InformationProfile | None = None


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
    # 命中即停：真实破解语义（找到目标即结束），缺省时使用 SAGE_STOP_ON_HIT。
    stop_on_hit: bool | None = None

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


class SystemConfigResponse(BaseModel):
    """运行配置（前端展示规划模式与调度模式，二者语义分离）。"""

    planner_type: PlannerType
    scheduler_type: SchedulerType
    real_execution_configured: bool
    feedback_mock_enabled: bool


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail
