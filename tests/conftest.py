from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage_pass.config import Settings
from sage_pass.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        upload_dir=tmp_path / "uploads",
        max_upload_bytes=1024,
        cors_origins=("http://localhost:5173",),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def hash_task_payload() -> dict:
    return {
        "name": "bcrypt测试任务",
        "target": {
            "type": "hash",
            "content": "$2b$12$example",
            "file_id": None,
        },
        "known_algorithm": "bcrypt",
        "time_budget": 300,
        "candidate_budget": 100000,
        "context": {
            "keywords": ["学校名称", "张三"],
            "years": [2024, 2025],
            "region": "北京",
            "organization": "示例大学",
            "description": "其他补充信息",
        },
    }
