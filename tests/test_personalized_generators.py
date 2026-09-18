from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sage_pass.candidate_generator import CandidateGenerator
from sage_pass.enums import PlannerType, StrategyId
from sage_pass.generators import GeneratorPrepareRequest, HistoryGenerator, HybridGenerator
from sage_pass.schemas import StrategyItem, StrategyPlan, TaskContext


@dataclass
class Pattern:
    id: int = 1
    pattern_type: str = "suffix_pattern"
    pattern_signature: str = "digits_1_4"
    observation_count: int = 4
    task_count: int = 2
    confidence: float = 0.8


def request(**overrides):
    values = {
        "strategy_id": StrategyId.S4,
        "parameters": {},
        "historical_passwords": ("Admin2020!",),
        "personalized_years": ("2026",),
        "number_suffixes": ("1",),
        "symbol_suffixes": ("!",),
    }
    values.update(overrides)
    return GeneratorPrepareRequest(**values)


def drain(generator, state, limit=3):
    records = []
    while not generator.exhausted(state):
        records.extend(generator.next_batch(state, limit).records)
    return records


def test_history_generator_golden_transform_order_and_safe_sources():
    generator = HistoryGenerator()
    records = drain(generator, generator.prepare(request()))
    assert [item.value for item in records] == [
        "Admin2020!", "admin2020!", "ADMIN2020!", "aDMIN2020!",
        "Admin2026!", "admin2026!", "ADMIN2026!", "aDMIN2026!",
        "admin", "admin1", "1admin", "admin!", "@dmin", "adm1n",
    ]
    serialized_sources = json.dumps(
        [source.public_dict() for item in records for source in item.sources],
        ensure_ascii=False,
    )
    assert "Admin2020!" not in serialized_sources
    assert "historical-password length=10" in serialized_sources


def test_history_snapshot_restore_continues_without_repeating_batch():
    generator = HistoryGenerator()
    uninterrupted = generator.prepare(request())
    assert generator.next_batch(uninterrupted, 4).candidates == (
        "Admin2020!", "admin2020!", "ADMIN2020!", "aDMIN2020!"
    )
    snapshot = generator.snapshot(uninterrupted)
    expected = generator.next_batch(uninterrupted, 3)
    restored = generator.restore(snapshot)
    actual = generator.next_batch(restored, 3)
    assert actual.candidates == expected.candidates
    assert actual.records == expected.records


def test_hybrid_combines_personal_history_current_year_and_pattern_knowledge():
    generator = HybridGenerator()
    state = generator.prepare(request(
        task_context=TaskContext(
            username="alice", region="Tianjin", organization="Nankai Lab"
        ),
        transfer_patterns=(Pattern(),),
    ))
    records = drain(generator, state, limit=5)
    values = [item.value for item in records]
    assert values[:4] == ["tianjin", "nankailab", "alice", "nl"]
    assert "Alice2026!" in values
    assert "alice1" in values
    hybrid = next(item for item in records if item.value == "Alice2026!")
    assert [source.kind for source in hybrid.sources] == [
        "keyword", "historical_structure", "personalized_combination"
    ]
    pattern = next(item for item in records if item.value == "alice1")
    assert pattern.strategy_id == StrategyId.S4
    assert pattern.sources[0].kind == "transfer_pattern"


def test_s4_auto_routes_information_scenarios_and_global_dedupes():
    plan = StrategyPlan(
        task_id="T-PERSONALIZED",
        planner_type=PlannerType.RULE,
        total_time_budget=10,
        strategies=[StrategyItem(
            strategy_id=StrategyId.S4,
            strategy_name="Personalized",
            priority=1,
            time_budget=10,
            candidate_budget=20,
            reason="test",
            parameters={},
        )],
    )
    pipeline = CandidateGenerator(
        baseline_candidates=(), number_suffixes=("1",),
        year_suffixes=("2026",), symbol_suffixes=("!",),
    )
    history_batches = list(pipeline.iter_plan_batches(
        plan, historical_passwords=("Admin2020!",), batch_size=4
    ))
    assert {batch.generator_id for batch in history_batches} == {"history"}
    hybrid_batches = list(pipeline.iter_plan_batches(
        plan,
        task_context=TaskContext(username="admin"),
        historical_passwords=("Admin2020!",),
        batch_size=4,
    ))
    assert {batch.generator_id for batch in hybrid_batches} == {"hybrid"}
    values = [value for batch in hybrid_batches for value in batch.candidates]
    assert len(values) == len(set(values))


def test_two_history_states_are_independent():
    generator = HistoryGenerator()
    first = generator.prepare(request(historical_passwords=("Alpha2020!",)))
    second = generator.prepare(request(historical_passwords=("Beta2021!",)))
    assert generator.next_batch(first, 1).candidates == ("Alpha2020!",)
    assert generator.next_batch(second, 1).candidates == ("Beta2021!",)


def test_history_prioritizes_exact_password_and_full_password_mutations():
    generator = HistoryGenerator()
    state = generator.prepare(request(
        historical_passwords=("Lisi@2000",),
        personalized_years=("2000", "2018", "2024", "2026"),
        symbol_suffixes=("!", "@", "#"),
    ))
    values = [item.value for item in drain(generator, state, limit=10)]

    assert values[0] == "Lisi@2000"
    assert "Lisi@2000!" in values
    assert values.index("Lisi@2000!") < values.index("Lisi@2018")


def test_personalized_year_order_prefers_birth_related_and_history_years():
    plan = StrategyPlan(
        task_id="T-YEAR-ORDER",
        planner_type=PlannerType.RULE,
        total_time_budget=10,
        strategies=[StrategyItem(
            strategy_id=StrategyId.S4,
            strategy_name="Personalized",
            priority=1,
            time_budget=10,
            candidate_budget=100,
            reason="test",
            parameters={},
        )],
    )
    pipeline = CandidateGenerator(year_suffixes=("2026", "2025", "1999"))
    stream = pipeline.open_plan_stream(
        plan,
        task_context=TaskContext(
            birth_year=2000,
            years=[2018, 2024],
        ),
        historical_passwords=("Legacy1998",),
    )
    snapshot = stream.snapshot()
    years = snapshot["arms"]["S4"]["generator_state"]["restore_data"]["years"]

    current_year = str(datetime.now(timezone.utc).year)
    expected = list(dict.fromkeys((
        "2000", "2018", "2024", "1998", current_year,
        "2026", "2025", "1999",
    )))
    assert years == expected


def test_i3_stream_generates_failed_case_target_in_s4_window():
    """回归：此前失败案例（旧口令 Lisi@2000 → 目标 Lisi@2000!）必须落在 S4 候选空间内。

    注意：S4（hybrid）先输出通用个人信息组合，再输出旧口令迁移候选；
    因此这里在 S4 的候选窗口（默认取前 4000 条）内断言，而不绑定"首批"，
    以免个人信息组合规模变化让断言失效。
    """
    plan = StrategyPlan(
        task_id="T-I3-REGRESSION",
        planner_type=PlannerType.RULE,
        total_time_budget=180,
        strategies=[StrategyItem(
            strategy_id=StrategyId.S4,
            strategy_name="Personalized",
            priority=1,
            time_budget=60,
            candidate_budget=10_000,
            reason="failed-case regression",
            parameters={},
        )],
    )
    pipeline = CandidateGenerator()
    stream = pipeline.open_plan_stream(
        plan,
        task_context=TaskContext(
            name="lisi",
            nickname="lisir",
            username="lisi_2000",
            email_local_part="lisi_2000",
            phone_suffix="6789",
            birthday="08-15",
            birth_year=2000,
            keywords=["cat", "travel"],
            years=[2000, 2018, 2024],
            region="shanghai",
            organization="test-company",
            interest_words=["photography", "reading"],
        ),
        historical_passwords=("Lisi@2000", "lisi200008", "2000lisi!"),
    )

    candidates = stream.pull(StrategyId.S4, 4_000).candidates
    target = "188253ebe8124b5652421683b2ed29f1"

    assert "Lisi@2000!" in candidates
    assert hashlib.md5(b"Lisi@2000!").hexdigest() == target
