"""契约漂移防护：机器可读契约与版本号必须与实现一致。

背景：`docs/openapi.json` 曾多次在新增字段后未同步（第 2 周 C 交付、第 3 周 C 交付），
评审阶段才被发现。此处的测试把它变成可自动发现的问题。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage_pass.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPO_ROOT / "docs" / "openapi.json"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def test_docs_openapi_matches_runtime_contract() -> None:
    assert OPENAPI_PATH.exists(), "缺少 docs/openapi.json"
    current = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert current == app.openapi(), (
        "docs/openapi.json 与运行时契约不一致；"
        "请运行 python scripts/regen_openapi.py --write"
    )


def test_docs_openapi_documents_information_profile() -> None:
    schema = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    components = schema["components"]["schemas"]
    assert "InformationProfile" in components
    assert components["InformationScenario"]["enum"] == ["I0", "I1", "I2", "I3"]
    assert "$ref" in components["TaskDetail"]["properties"]["information_profile"]


def test_package_version_matches_pyproject_and_openapi() -> None:
    tomllib = pytest.importorskip("tomllib")
    import sage_pass

    declared = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]["version"]
    schema = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert sage_pass.__version__ == declared
    assert schema["info"]["version"] == declared
