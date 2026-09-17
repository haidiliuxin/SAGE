from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from sage_pass.candidate_generator import CandidateGenerator
from sage_pass.enums import StrategyId
from sage_pass.generators import (
    GeneratorPrepareRequest,
    GeneratorStateError,
    PCFGFullGenerator,
    PCFGModelNotConfiguredError,
)
from sage_pass.generators.pcfg_full import _is_usable_candidate
from sage_pass.schemas import StrategyItem, StrategyPlan


def _write_ruleset(root: Path) -> Path:
    rules = root / "synthetic"
    for directory in (
        "Alpha", "Capitalization", "Digits", "Other", "Keyboard",
        "Years", "Context", "Grammar", "Omen", "Emails", "Websites",
    ):
        (rules / directory).mkdir(parents=True, exist_ok=True)
    (rules / "config.ini").write_text(
        """[TRAINING_PROGRAM_DETAILS]
version = 4.7

[TRAINING_DATASET_DETAILS]
encoding = utf-8
uuid = synthetic-model-v1

[BASE_A]
name = A
directory = Alpha
filenames = ["2.txt"]

[CAPITALIZATION]
name = C
directory = Capitalization
filenames = ["2.txt"]

[BASE_D]
name = D
directory = Digits
filenames = ["1.txt"]

[BASE_O]
name = O
directory = Other
filenames = ["1.txt"]

[BASE_K]
name = K
directory = Keyboard
filenames = ["1.txt"]

[BASE_Y]
name = Y
directory = Years
filenames = ["1.txt"]

[BASE_X]
name = X
directory = Context
filenames = ["1.txt"]
""",
        encoding="utf-8",
    )
    (rules / "Grammar" / "grammar.txt").write_text(
        "A2\t0.45\nA2D1\t0.27\nD1\t0.18\nM\t0.1\n", encoding="utf-8"
    )
    (rules / "Alpha" / "2.txt").write_text(
        "aa\t0.6\nbb\t0.4\n", encoding="utf-8"
    )
    (rules / "Capitalization" / "2.txt").write_text(
        "LL\t0.75\nUL\t0.25\n", encoding="utf-8"
    )
    (rules / "Digits" / "1.txt").write_text(
        "1\t0.7\n2\t0.3\n", encoding="utf-8"
    )
    for relative in (
        "Other/1.txt", "Keyboard/1.txt", "Years/1.txt", "Context/1.txt",
        "Omen/pcfg_omen_prob.txt", "Emails/email_providers.txt",
        "Websites/website_hosts.txt",
    ):
        (rules / relative).write_text("", encoding="utf-8")
    return rules


def _request(**parameters: object) -> GeneratorPrepareRequest:
    return GeneratorPrepareRequest(
        strategy_id=StrategyId.S3,
        parameters=parameters,
    )


def _drain(generator: PCFGFullGenerator, state, limit: int = 2):
    records = []
    while not generator.exhausted(state):
        records.extend(generator.next_batch(state, limit).records)
    return records


def test_pcfg_full_requires_an_explicit_ruleset():
    with pytest.raises(PCFGModelNotConfiguredError, match="未配置"):
        PCFGFullGenerator().prepare(_request())


def test_pcfg_full_loads_model_streams_probability_order_and_log_probability(
    tmp_path: Path,
):
    generator = PCFGFullGenerator(_write_ruleset(tmp_path))
    state = generator.prepare(_request(max_structure_length=10))
    records = _drain(generator, state, limit=2)

    assert [record.value for record in records[:4]] == ["aa", "bb", "1", "aa1"]
    probabilities = [record.sources[0].probability for record in records]
    assert probabilities == sorted(probabilities, reverse=True)
    assert all(
        source.log_probability == pytest.approx(math.log(source.probability))
        for record in records
        for source in record.sources
    )
    assert all(record.sources[0].kind == "pcfg_full" for record in records)


def test_pcfg_full_snapshot_restore_continues_at_exact_cursor(tmp_path: Path):
    generator = PCFGFullGenerator(_write_ruleset(tmp_path))
    uninterrupted = generator.prepare(_request())
    first = generator.next_batch(uninterrupted, 3)
    snapshot = generator.snapshot(uninterrupted)
    json.dumps(snapshot)
    expected = generator.next_batch(uninterrupted, 3)

    restored = generator.restore(snapshot)
    actual = generator.next_batch(restored, 3)

    assert len(first.candidates) == 3
    assert actual.candidates == expected.candidates
    assert actual.records == expected.records


def test_pcfg_full_states_are_run_local_and_independent(tmp_path: Path):
    generator = PCFGFullGenerator(_write_ruleset(tmp_path))
    first = generator.prepare(_request())
    second = generator.prepare(_request())

    assert generator.next_batch(first, 2).candidates == ("aa", "bb")
    assert generator.next_batch(second, 1).candidates == ("aa",)
    assert first.cursor == 2
    assert second.cursor == 1


def test_pcfg_full_restore_rejects_changed_model_uuid(tmp_path: Path):
    ruleset = _write_ruleset(tmp_path)
    original = PCFGFullGenerator(ruleset)
    state = original.prepare(_request())
    original.next_batch(state, 1)
    snapshot = original.snapshot(state)

    config_path = ruleset / "config.ini"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "synthetic-model-v1", "synthetic-model-v2"
        ),
        encoding="utf-8",
    )
    changed = PCFGFullGenerator(ruleset)

    with pytest.raises(GeneratorStateError, match="模型版本不兼容"):
        changed.restore(snapshot)


def test_pcfg_full_filters_length_newline_nul_and_encoding(tmp_path: Path):
    generator = PCFGFullGenerator(_write_ruleset(tmp_path))
    records = _drain(
        generator,
        generator.prepare(_request(max_structure_length=1)),
    )
    assert records
    assert all(len(record.value) == 1 for record in records)
    assert not _is_usable_candidate("a\nb", encoding="utf-8", max_length=10)
    assert not _is_usable_candidate("a\x00b", encoding="utf-8", max_length=10)
    assert not _is_usable_candidate("\udcff", encoding="utf-8", max_length=10)


def test_pipeline_can_select_full_pcfg_and_keeps_global_dedup(tmp_path: Path):
    plan = StrategyPlan(
        task_id="T-PCFG-FULL",
        planner_type="rule",
        total_time_budget=2,
        strategies=[
            StrategyItem(
                strategy_id=StrategyId.S1,
                strategy_name="baseline",
                priority=1,
                time_budget=1,
                candidate_budget=1,
                reason="dedupe seed",
            ),
            StrategyItem(
                strategy_id=StrategyId.S3,
                strategy_name="pcfg_full",
                priority=2,
                time_budget=1,
                candidate_budget=3,
                reason="full model",
                parameters={"max_structure_length": 10},
            ),
        ],
    )
    batches = list(CandidateGenerator(
        baseline_candidates=("aa",),
        pcfg_variant="pcfg_full",
        pcfg_ruleset_path=str(_write_ruleset(tmp_path)),
    ).iter_plan_batches(plan, batch_size=2))
    values = [value for batch in batches for value in batch.candidates]

    assert values == ["aa", "bb", "1", "aa1"]
    assert [batch.generator_id for batch in batches] == [
        "baseline", "pcfg_full", "pcfg_full",
    ]
    assert len(values) == len(set(values))
