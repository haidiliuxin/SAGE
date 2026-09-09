from __future__ import annotations

from datetime import datetime, timezone

from sage_pass.candidate_generator import CandidateGenerator
from sage_pass.enums import PlannerType, StrategyId
from sage_pass.models import PatternKnowledgeModel
from sage_pass.schemas import StrategyItem, StrategyPlan, TaskContext
from sage_pass.transfer import TransferCandidateGenerator, TransferScorer


def _pattern(
    pattern_id: int,
    pattern_type: str,
    signature: str,
    *,
    confidence: float = 0.8,
) -> PatternKnowledgeModel:
    now = datetime.now(timezone.utc).isoformat()
    return PatternKnowledgeModel(
        id=pattern_id,
        scope="hash:md5",
        target_type="hash",
        algorithm="md5",
        pattern_type=pattern_type,
        pattern_signature=signature,
        feature_data={"pattern": signature},
        observation_count=5,
        task_count=3,
        confidence=confidence,
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )


def _strategy(strategy_id: StrategyId, priority: int, budget: int):
    return StrategyItem(
        strategy_id=strategy_id,
        strategy_name=strategy_id.value,
        priority=priority,
        time_budget=10,
        candidate_budget=budget,
        reason="test",
        parameters={},
    )


def _plan(*items):
    return StrategyPlan(
        task_id="T-S5",
        planner_type=PlannerType.RULE,
        total_time_budget=sum(item.time_budget for item in items),
        strategies=list(items),
    )


def test_s5_without_history_produces_no_batch():
    generator = CandidateGenerator(baseline_candidates=("current",))
    batches = list(generator.iter_plan_batches(
        _plan(_strategy(StrategyId.S5, 1, 10)), transfer_patterns=[]
    ))
    assert batches == []


def test_s5_uses_only_current_seeds_and_keeps_abstract_provenance():
    patterns = [
        _pattern(1, "suffix_pattern", "year4"),
        _pattern(2, "suffix_pattern", "digits_1_4_plus_symbol"),
    ]
    generator = CandidateGenerator(
        baseline_candidates=("baseline",),
        number_suffixes=("7",),
        year_suffixes=("2025",),
        symbol_suffixes=("!",),
    )
    records = generator.build_candidate_records(
        _plan(_strategy(StrategyId.S5, 1, 20)),
        supplied_candidates=("supplied",),
        task_context=TaskContext(keywords=["Current Word"], years=[2026]),
        transfer_patterns=patterns,
    )
    values = [record.value for record in records]
    assert values == [
        "supplied7!",
        "baseline7!",
        "currentword7!",
        "supplied2026",
        "baseline2026",
        "currentword2026",
    ]
    assert all(record.strategy_id == StrategyId.S5 for record in records)
    assert all(record.sources[0].kind == "transfer_pattern" for record in records)
    assert {record.sources[0].pattern_id for record in records} == {1, 2}
    assert all(record.sources[0].pattern_signature for record in records)
    assert all(record.sources[0].pattern_confidence == 0.8 for record in records)
    assert "HistoricalRecoveredPlaintext" not in "\n".join(values)
    assert {record.sources[0].original for record in records} <= {
        "supplied", "baseline", "currentword"
    }


def test_s5_order_is_stable_and_obeys_strategy_and_global_budgets():
    patterns = [
        _pattern(2, "suffix_pattern", "digits_1_4", confidence=0.7),
        _pattern(1, "suffix_pattern", "year4", confidence=0.9),
    ]
    generator = CandidateGenerator(
        baseline_candidates=("word", "word2025"),
        number_suffixes=("1", "2"),
        year_suffixes=("2025",),
    )
    plan = _plan(
        _strategy(StrategyId.S1, 1, 2),
        _strategy(StrategyId.S5, 2, 3),
    )
    first = generator.build_candidate_records(
        plan, transfer_patterns=patterns, max_candidates=4
    )
    second = generator.build_candidate_records(
        plan, transfer_patterns=patterns, max_candidates=4
    )
    assert [item.value for item in first] == [item.value for item in second]
    assert len(first) == 4
    assert len({item.value for item in first}) == 4
    assert sum(item.strategy_id == StrategyId.S5 for item in first) <= 3
    assert [item.value for item in first].count("word2025") == 1


def test_s5_filters_overlength_and_never_emits_multiline_candidates():
    pattern = _pattern(1, "suffix_pattern", "digits_1_4")
    generator = CandidateGenerator(
        baseline_candidates=("x" * 1024, "safe"), number_suffixes=("1",)
    )
    records = generator.build_candidate_records(
        _plan(_strategy(StrategyId.S5, 1, 10)), transfer_patterns=[pattern]
    )
    assert [item.value for item in records] == ["safe1"]
    assert all(1 <= len(item.value) <= 1024 for item in records)
    assert all("\n" not in item.value and "\r" not in item.value for item in records)


def test_transfer_generator_supports_explainable_bounded_shapes():
    patterns = [
        _pattern(1, "case_pattern", "capitalized"),
        _pattern(2, "structure_signature", "U1L4D3"),
        _pattern(3, "structure_signature", "U3D2"),
        _pattern(4, "common_substitution", "a_to_at"),
    ]
    records = list(TransferCandidateGenerator().iter_records(
        patterns,
        seeds=("admin", "nku"),
        years=("2025",),
        numbers=("12",),
        symbols=("!",),
    ))
    values = [record.value for record in records]
    assert "Admin" in values
    assert "Admin12" in values
    assert "NKU12" in values
    assert "@dmin" in values
    assert len(values) < 100


def test_transfer_score_is_bounded_deterministic_and_recency_sensitive():
    scorer = TransferScorer(recency_half_life_days=30)
    recent = _pattern(1, "suffix_pattern", "year4")
    stale = _pattern(2, "suffix_pattern", "digits_1_4")
    stale.last_seen_at = "2000-01-01T00:00:00+00:00"
    first = scorer.pattern_score(recent)
    second = scorer.pattern_score(recent)
    assert first == second
    assert 0.0 <= scorer.aggregate([recent, stale]) <= 1.0
    assert first > scorer.pattern_score(stale)
