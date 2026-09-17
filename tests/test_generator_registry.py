from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest

from sage_pass.candidate_types import CandidateBatch, CandidateRecord
from sage_pass.enums import StrategyId
from sage_pass.generators import (
    BaselineGenerator,
    ContextGenerator,
    DuplicateGeneratorError,
    FUTURE_GENERATOR_IDS,
    GeneratorPrepareRequest,
    GeneratorProtocol,
    GeneratorRegistry,
    HistoryGenerator,
    HybridGenerator,
    PCFGLiteGenerator,
    RuleGenerator,
    TransferGenerator,
    UnknownGeneratorError,
    build_default_registry,
)
from sage_pass.schemas import StrategyItem, StrategyPlan, TaskContext


@dataclass
class Pattern:
    id: int = 1
    scope: str = "hash:md5"
    target_type: str = "hash"
    algorithm: str = "md5"
    pattern_type: str = "suffix_pattern"
    pattern_signature: str = "digits_1_4"
    feature_data: dict[str, object] = None
    observation_count: int = 4
    task_count: int = 2
    confidence: float = 0.8
    last_seen_at: str = "2026-01-01T00:00:00+00:00"

    def __post_init__(self):
        if self.feature_data is None:
            self.feature_data = {"pattern": self.pattern_signature}


def _requests():
    return [
        (
            BaselineGenerator(),
            GeneratorPrepareRequest(
                strategy_id=StrategyId.S1,
                parameters={},
                supplied_candidates=("supplied",),
                baseline_candidates=("base", "supplied"),
            ),
            ["supplied", "base"],
        ),
        (
            RuleGenerator(),
            GeneratorPrepareRequest(
                strategy_id=StrategyId.S2,
                parameters={
                    "capitalize_first": True,
                    "common_number_suffix": True,
                },
                rule_seeds=("pass",),
                number_suffixes=("1", "2"),
                rule_year_suffixes=("2025",),
            ),
            ["Pass", "pass1", "pass2", "Pass1", "Pass2"],
        ),
        (
            PCFGLiteGenerator(),
            GeneratorPrepareRequest(
                strategy_id=StrategyId.S3,
                parameters={
                    "max_templates": 3,
                    "min_probability": 0.15,
                    "max_structure_length": 20,
                },
                pcfg_seeds=("pass",),
                year_suffixes=("2025",),
                number_suffixes=("1",),
                symbol_suffixes=("!",),
            ),
            ["pass", "pass2025", "pass1"],
        ),
        (
            ContextGenerator(),
            GeneratorPrepareRequest(
                strategy_id=StrategyId.S4,
                parameters={
                    "use_keywords": True,
                    "use_pinyin": False,
                    "use_abbreviations": False,
                    "use_years": True,
                    "use_region": False,
                    "use_organization": False,
                    "max_combinations": 3,
                },
                task_context=TaskContext(keywords=["Alpha"], years=[2025]),
            ),
            ["alpha", "2025", "alpha2025"],
        ),
        (
            TransferGenerator(),
            GeneratorPrepareRequest(
                strategy_id=StrategyId.S5,
                parameters={},
                transfer_patterns=(Pattern(),),
                transfer_seeds=("word",),
                number_suffixes=("1", "2"),
                year_suffixes=("2025",),
                symbol_suffixes=("!",),
            ),
            ["word1", "word2"],
        ),
    ]


def _drain(generator, state, limit=2):
    values = []
    while not generator.exhausted(state):
        batch = generator.next_batch(state, limit)
        values.extend(batch.candidates)
    return values


def test_default_registry_registers_only_implemented_generators():
    registry = build_default_registry()
    assert registry.registered_ids() == (
        "baseline", "rule", "pcfg_lite", "pcfg_full", "markov", "context",
        "history", "hybrid", "pattern_knowledge", "transfer",
        # 原生攻击单元（S6 掩码/暴力、S7 混合）：候选由 hashcat 自己枚举，
        # 这里注册的是不产出候选的占位生成器，供调度器记账使用。
        "mask", "hybrid_mask",
    )
    assert all(generator_id not in registry for generator_id in FUTURE_GENERATOR_IDS)
    assert isinstance(registry.get("baseline"), GeneratorProtocol)


def test_registry_rejects_duplicate_and_unknown_generators():
    registry = GeneratorRegistry()
    registry.register(BaselineGenerator())
    with pytest.raises(DuplicateGeneratorError, match="已注册"):
        registry.register(BaselineGenerator())
    with pytest.raises(UnknownGeneratorError, match="未注册"):
        registry.get("markov")


def test_pipeline_reports_missing_registered_generator():
    from sage_pass.candidate_generator import CandidateGenerator

    registry = GeneratorRegistry()
    registry.register(BaselineGenerator())
    plan = StrategyPlan(
        task_id="T-MISSING",
        planner_type="rule",
        total_time_budget=1,
        strategies=[StrategyItem(
            strategy_id="S2",
            strategy_name="Rule",
            priority=1,
            time_budget=1,
            candidate_budget=1,
            reason="missing generator",
        )],
    )
    with pytest.raises(UnknownGeneratorError, match="'rule' 未注册"):
        list(CandidateGenerator(registry=registry).iter_plan_batches(plan))


@pytest.mark.parametrize(("generator", "prepare_request", "expected"), _requests())
def test_generators_preserve_values_order_sources_and_batch_contract(
    generator, prepare_request, expected
):
    state = generator.prepare(prepare_request)
    batches = []
    while not generator.exhausted(state):
        batches.append(generator.next_batch(state, 2))
    records = [record for batch in batches for record in batch.records]

    assert [record.value for record in records] == expected
    assert all(record.strategy_id == prepare_request.strategy_id for record in records)
    assert all(batch.generator_id == generator.generator_id for batch in batches)
    assert all(batch.candidates == tuple(item.value for item in batch.records) for batch in batches)
    assert batches[-1].exhausted is True


@pytest.mark.parametrize(("generator", "prepare_request", "expected"), _requests())
def test_generator_snapshot_restore_continues_at_exact_next_candidate(
    generator, prepare_request, expected
):
    uninterrupted = generator.prepare(prepare_request)
    first = generator.next_batch(uninterrupted, 1)
    snapshot = generator.snapshot(uninterrupted)
    json.dumps(snapshot)
    expected_next = generator.next_batch(uninterrupted, 1)

    restored = generator.restore(snapshot)
    actual_next = generator.next_batch(restored, 1)

    assert first.candidates == (expected[0],)
    assert actual_next.candidates == expected_next.candidates
    assert actual_next.records == expected_next.records
    assert restored.cursor == 2


def test_two_generator_states_are_independent():
    generator = BaselineGenerator()
    request = GeneratorPrepareRequest(
        strategy_id=StrategyId.S1,
        parameters={},
        baseline_candidates=("a", "b", "c"),
    )
    first = generator.prepare(request)
    second = generator.prepare(request)

    assert generator.next_batch(first, 2).candidates == ("a", "b")
    assert generator.next_batch(second, 1).candidates == ("a",)
    assert first.cursor == 2
    assert second.cursor == 1


def test_shared_generator_instance_keeps_concurrent_run_states_independent():
    generator = BaselineGenerator()

    def run(prefix):
        state = generator.prepare(GeneratorPrepareRequest(
            strategy_id=StrategyId.S1,
            parameters={},
            baseline_candidates=(f"{prefix}-1", f"{prefix}-2"),
        ))
        return _drain(generator, state, limit=1), state.cursor

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, "first")
        second = pool.submit(run, "second")

    assert first.result() == (["first-1", "first-2"], 2)
    assert second.result() == (["second-1", "second-2"], 2)


def test_context_snapshot_excludes_description_and_keeps_supported_personal_fields():
    generator = ContextGenerator()
    state = generator.prepare(GeneratorPrepareRequest(
        strategy_id=StrategyId.S4,
        parameters={},
        task_context=TaskContext(
            keywords=["allowed"],
            description="must-not-be-snapshotted",
            username="not-used-by-current-context-generator",
        ),
    ))
    serialized = json.dumps(generator.snapshot(state), ensure_ascii=False)
    assert "must-not-be-snapshotted" not in serialized
    assert "not-used-by-current-context-generator" in serialized


def test_candidate_batch_rejects_record_mismatch():
    record = CandidateRecord("a", StrategyId.S1, ())
    with pytest.raises(ValueError, match="值和顺序"):
        CandidateBatch(StrategyId.S1, ("b",), (record,))


def test_registry_pipeline_golden_output_preserves_cross_generator_semantics():
    from sage_pass.candidate_generator import CandidateGenerator

    plan = StrategyPlan(
        task_id="T-GOLDEN",
        planner_type="rule",
        total_time_budget=50,
        strategies=[
            StrategyItem(
                strategy_id=strategy_id,
                strategy_name=strategy_id.value,
                priority=index,
                time_budget=10,
                candidate_budget=budget,
                reason="golden",
                parameters=parameters,
            )
            for index, (strategy_id, budget, parameters) in enumerate((
                (StrategyId.S1, 1, {}),
                (StrategyId.S2, 3, {
                    "capitalize_first": True,
                    "common_number_suffix": True,
                }),
                (StrategyId.S3, 3, {
                    "max_templates": 3,
                    "min_probability": 0.15,
                    "max_structure_length": 20,
                }),
                (StrategyId.S4, 3, {
                    "use_keywords": True,
                    "use_pinyin": True,
                    "use_abbreviations": True,
                    "use_years": True,
                    "use_region": False,
                    "use_organization": False,
                    "max_combinations": 3,
                }),
                (StrategyId.S5, 3, {}),
            ), start=1)
        ],
    )
    batches = list(CandidateGenerator(
        baseline_candidates=("pass",),
        number_suffixes=("1",),
        year_suffixes=("2025",),
        symbol_suffixes=("!",),
    ).iter_plan_batches(
        plan,
        task_context=TaskContext(keywords=["Alpha"], years=[2025]),
        transfer_patterns=(Pattern(),),
        batch_size=2,
    ))
    records = [record for batch in batches for record in batch.records]

    assert [record.value for record in records] == [
        "pass", "Pass", "pass1", "Pass1", "pass2025",
        "alpha", "2025", "alpha2025", "alpha1",
    ]
    assert [batch.generator_id for batch in batches] == [
        "baseline", "rule", "rule", "pcfg_lite",
        "context", "context", "pattern_knowledge",
    ]
    assert [[source.kind for source in record.sources] for record in records] == [
        ["baseline"],
        ["rule"], ["rule"], ["rule"],
        ["pcfg_template"],
        ["keyword"], ["year"], ["keyword", "year", "combination"],
        ["transfer_pattern"],
    ]
    assert len({record.value for record in records}) == len(records)
