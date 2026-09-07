from __future__ import annotations

import pytest

from sage_pass.analyzer import _has_context
from sage_pass.context import abbreviate, iter_context_candidates, normalize_keyword, to_pinyin
from sage_pass.enums import StrategyId
from sage_pass.schemas import TaskContext


def test_context_normalization_pinyin_and_abbreviations():
    assert normalize_keyword("  ＮＫＵ Lab  ") == "nkulab"
    assert to_pinyin("南开大学") == "nankaidaxue"
    assert abbreviate("南开大学") == "nkdx"
    assert abbreviate("Nan Kai University") == "nku"
    with pytest.raises(ValueError, match="单行"):
        normalize_keyword("bad\nterm")


def test_context_uses_real_values_and_preserves_sources():
    records = list(iter_context_candidates(TaskContext(
        keywords=["张三"],
        years=[2025],
        region="天津",
        organization="南开大学",
    )))
    values = [item.value for item in records]
    assert values[:5] == ["张三", "天津", "南开大学", "南开", "zhangsan"]
    assert "zs2025" in values
    assert "nankaidaxue2025" in values
    combined = next(item for item in records if item.value == "zhangsan2025")
    assert combined.strategy_id == StrategyId.S4
    assert [source.kind for source in combined.sources] == [
        "pinyin", "year", "combination"
    ]
    assert combined.sources[-1].components == (
        "pinyin:zhangsan", "year:2025"
    )


def test_context_limit_and_source_switches():
    records = list(iter_context_candidates(
        TaskContext(keywords=["Alpha"], years=[2024], region="Beijing"),
        parameters={
            "use_keywords": True,
            "use_pinyin": False,
            "use_abbreviations": False,
            "use_years": True,
            "use_region": False,
            "use_organization": False,
            "max_combinations": 2,
        },
    ))
    assert [item.value for item in records] == ["alpha", "2024"]


def test_description_alone_does_not_enable_s4_context():
    assert not _has_context(TaskContext(description="仅用于任务备注"))
    assert _has_context(TaskContext(keywords=["南开"]))
