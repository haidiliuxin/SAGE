from __future__ import annotations

from itertools import islice

from sage_pass.enums import StrategyId
from sage_pass.pcfg_lite import PCFG_TEMPLATES, iter_pcfg_candidates, ranked_templates


def test_templates_are_finite_and_probability_ranked():
    ranked = ranked_templates()
    assert ranked == PCFG_TEMPLATES
    assert len(ranked) == 11
    assert [item.probability for item in ranked] == sorted(
        (item.probability for item in ranked), reverse=True
    )


def test_template_filters_and_expansion_are_deterministic():
    records = list(iter_pcfg_candidates(
        ("pass",),
        years=("2025",),
        numbers=("1",),
        symbols=("!",),
        max_templates=3,
        min_probability=0.15,
        max_structure_length=20,
    ))
    assert [item.value for item in records] == ["pass", "pass2025", "pass1"]
    assert [item.sources[0].template for item in records] == ["W", "WY", "WD"]
    assert all(item.strategy_id == StrategyId.S3 for item in records)


def test_structure_length_and_lazy_candidate_control():
    records = list(islice(iter_pcfg_candidates(
        ("longword", "ok"),
        years=("2025",),
        numbers=("1",),
        symbols=("!",),
        max_templates=2,
        max_structure_length=4,
    ), 10))
    assert [item.value for item in records] == ["ok"]

