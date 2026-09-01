from enum import StrEnum


class TaskStatus(StrEnum):
    CREATED = "created"
    ANALYZED = "analyzed"
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TargetType(StrEnum):
    HASH = "hash"
    ZIP = "zip"
    PDF = "pdf"
    OFFICE = "office"
    UNKNOWN = "unknown"


class VerificationCost(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class StrategyId(StrEnum):
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"
    S5 = "S5"


class PlannerType(StrEnum):
    MOCK = "mock"
    RULE = "rule"
    LLM = "llm"
    ADAPTIVE = "adaptive"


class ExecutionMode(StrEnum):
    MOCK = "mock"
    REAL = "real"
