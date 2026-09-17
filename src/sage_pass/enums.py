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
    ADAPTIVE = "adaptive"


class LLMApiStyle(StrEnum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"


class ExecutionMode(StrEnum):
    MOCK = "mock"
    REAL = "real"


class InformationScenario(StrEnum):
    """Authorized information available to the current task."""

    I0 = "I0"  # no auxiliary information
    I1 = "I1"  # personal information only
    I2 = "I2"  # historical passwords only
    I3 = "I3"  # personal information and historical passwords


class InformationType(StrEnum):
    NAME = "name"
    NICKNAME = "nickname"
    USERNAME = "username"
    EMAIL_LOCAL_PART = "email_local_part"
    PHONE_SUFFIX = "phone_suffix"
    BIRTHDAY_OR_YEAR = "birthday_or_year"
    REGION = "region"
    ORGANIZATION = "organization"
    INTEREST_WORD = "interest_word"
    AUTHORIZED_KEYWORD = "authorized_keyword"
