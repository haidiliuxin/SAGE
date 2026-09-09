from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .enums import LLMApiStyle, PlannerType


def _as_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    upload_dir: Path
    max_upload_bytes: int
    cors_origins: tuple[str, ...]
    hashcat_path: str = "hashcat"
    zip2john_path: str = "zip2john"
    planner_type: PlannerType = PlannerType.MOCK
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
            planner_type=PlannerType(os.getenv("SAGE_PLANNER_TYPE", "mock").lower()),
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
        )
