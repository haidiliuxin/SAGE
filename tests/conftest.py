import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage_pass.config import Settings
from sage_pass.main import create_app


def pytest_configure(config: pytest.Config) -> None:
    """Give each pytest process an isolated, caller-owned temp directory.

    A fixed directory is unsafe when tests alternate between a sandboxed agent
    and an interactive Windows account: the creator's ACL can deny the other
    process. A PID-specific sibling of the repository avoids both that collision
    and pytest's inaccessible per-user temp root.
    """
    if config.option.basetemp is None:
        config.option.basetemp = str(
            config.rootpath / f".pytest-tmp-{os.getpid()}"
        )


def pytest_sessionfinish(session: pytest.Session) -> None:
    base_temp = session.config.option.basetemp
    if base_temp:
        shutil.rmtree(Path(base_temp), ignore_errors=True)


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
