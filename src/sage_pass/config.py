from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .enums import LLMApiStyle, PlannerType, SchedulerType


def _as_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _parse_planner_type(raw: str) -> PlannerType:
    value = raw.strip().lower()
    if value == "adaptive":
        raise RuntimeError(
            "SAGE_PLANNER_TYPE=adaptive 已弃用：自适应能力位于调度层。"
            "请改用 SAGE_SCHEDULER_TYPE（fixed / round_robin / "
            "heuristic_bandit / ucb / cost_aware_ucb / thompson），"
            "并把 SAGE_PLANNER_TYPE 设为 mock、rule 或 llm。"
        )
    try:
        return PlannerType(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in PlannerType)
        raise RuntimeError(
            f"SAGE_PLANNER_TYPE={raw!r} 不受支持，可选值：{allowed}"
        ) from exc


def _parse_scheduler_type(raw: str) -> SchedulerType:
    try:
        return SchedulerType(raw.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SchedulerType)
        raise RuntimeError(
            f"SAGE_SCHEDULER_TYPE={raw!r} 不受支持，可选值：{allowed}"
        ) from exc


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    upload_dir: Path
    max_upload_bytes: int
    cors_origins: tuple[str, ...]
    hashcat_path: str = "hashcat"
    zip2john_path: str = "zip2john"
    planner_type: PlannerType = PlannerType.MOCK
    scheduler_type: SchedulerType = SchedulerType.HEURISTIC_BANDIT
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    llm_api_style: LLMApiStyle = LLMApiStyle.RESPONSES
    llm_model: str = "gpt-4.1-mini"
    llm_temperature: float = 0.1
    llm_timeout_seconds: float = 15.0
    llm_max_output_tokens: int = 700
    llm_cache_ttl_seconds: int = 900
    llm_cache_max_entries: int = 256
    feedback_minimum_observations: int = 2
    feedback_minimum_tasks: int = 2
    feedback_maximum_patterns_per_scope: int = 500
    feedback_recency_half_life_days: float = 90.0
    feedback_enable_mock: bool = False
    pdf2john_path: str = "pdf2john"
    office2john_path: str = "office2john"
    extraction_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "Settings":
        origins = os.getenv(
            "SAGE_CORS_ORIGINS",
            (
                "http://localhost:5173,http://127.0.0.1:5173,"
                "http://localhost:8501,http://127.0.0.1:8501"
            ),
        )
        return cls(
            database_url=os.getenv(
                "SAGE_DATABASE_URL", "sqlite:///./data/sage_pass.db"
            ),
            upload_dir=_as_path(os.getenv("SAGE_UPLOAD_DIR", "./data/uploads")),
            max_upload_bytes=int(os.getenv("SAGE_MAX_UPLOAD_BYTES", "10485760")),
            cors_origins=tuple(item.strip() for item in origins.split(",") if item.strip()),
            hashcat_path=os.getenv("SAGE_HASHCAT_PATH", "hashcat"),
            zip2john_path=os.getenv("SAGE_ZIP2JOHN_PATH", "zip2john"),
            planner_type=_parse_planner_type(
                os.getenv("SAGE_PLANNER_TYPE", "mock")
            ),
            scheduler_type=_parse_scheduler_type(
                os.getenv("SAGE_SCHEDULER_TYPE", "heuristic_bandit")
            ),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_base_url=os.getenv("OPENAI_BASE_URL") or None,
            llm_api_style=LLMApiStyle(
                os.getenv("SAGE_LLM_API_STYLE", "responses").lower()
            ),
            llm_model=os.getenv("SAGE_LLM_MODEL", "gpt-4.1-mini"),
            llm_temperature=float(os.getenv("SAGE_LLM_TEMPERATURE", "0.1")),
            llm_timeout_seconds=float(os.getenv("SAGE_LLM_TIMEOUT_SECONDS", "15")),
            llm_max_output_tokens=int(os.getenv("SAGE_LLM_MAX_OUTPUT_TOKENS", "700")),
            llm_cache_ttl_seconds=int(os.getenv("SAGE_LLM_CACHE_TTL_SECONDS", "900")),
            llm_cache_max_entries=int(os.getenv("SAGE_LLM_CACHE_MAX_ENTRIES", "256")),
            feedback_minimum_observations=int(
                os.getenv("SAGE_FEEDBACK_MINIMUM_OBSERVATIONS", "2")
            ),
            feedback_minimum_tasks=int(
                os.getenv("SAGE_FEEDBACK_MINIMUM_TASKS", "2")
            ),
            feedback_maximum_patterns_per_scope=int(
                os.getenv("SAGE_FEEDBACK_MAXIMUM_PATTERNS_PER_SCOPE", "500")
            ),
            feedback_recency_half_life_days=float(
                os.getenv("SAGE_FEEDBACK_RECENCY_HALF_LIFE_DAYS", "90")
            ),
            feedback_enable_mock=os.getenv(
                "SAGE_FEEDBACK_ENABLE_MOCK", "false"
            ).strip().lower() in {"1", "true", "yes", "on"},
            pdf2john_path=os.getenv("SAGE_PDF2JOHN_PATH", "pdf2john"),
            office2john_path=os.getenv(
                "SAGE_OFFICE2JOHN_PATH", "office2john"
            ),
            extraction_timeout_seconds=float(
                os.getenv("SAGE_EXTRACTION_TIMEOUT_SECONDS", "30")
            ),
        )
