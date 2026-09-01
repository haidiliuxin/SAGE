from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    upload_dir: Path
    max_upload_bytes: int
    cors_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        origins = os.getenv(
            "SAGE_CORS_ORIGINS",
            "http://localhost:8501,http://127.0.0.1:8501",
        )
        return cls(
            database_url=os.getenv(
                "SAGE_DATABASE_URL", "sqlite:///./data/sage_pass.db"
            ),
            upload_dir=_as_path(os.getenv("SAGE_UPLOAD_DIR", "./data/uploads")),
            max_upload_bytes=int(os.getenv("SAGE_MAX_UPLOAD_BYTES", "10485760")),
            cors_origins=tuple(item.strip() for item in origins.split(",") if item.strip()),
        )
