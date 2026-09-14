from enum import StrEnum


class TaskStatus(StrEnum):
    CREATED = "created"
    ANALYZED = "analyzed"
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
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


class SchedulerType(StrEnum):
    """调度模式（与 Planner 类型分离，修正 adaptive 语义）。"""

    FIXED = "fixed"
    ROUND_ROBIN = "round_robin"
    HEURISTIC_BANDIT = "heuristic_bandit"
    UCB = "ucb"
    COST_AWARE_UCB = "cost_aware_ucb"
    THOMPSON = "thompson"


class LLMApiStyle(StrEnum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"


class ExecutionMode(StrEnum):
    MOCK = "mock"
    REAL = "real"
