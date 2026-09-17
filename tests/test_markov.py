from __future__ import annotations

import json
from pathlib import Path

import pytest

from sage_pass.candidate_generator import CandidateGenerator
from sage_pass.enums import StrategyId
from sage_pass.generators import (
    GeneratorPrepareRequest,
    GeneratorStateError,
    MarkovGenerator,
    MarkovModelError,
    MarkovModelNotConfiguredError,
)
from sage_pass.generators.markov import _is_usable_candidate
from sage_pass.schemas import StrategyItem, StrategyPlan


def _write_omen_model(root: Path) -> Path:
    model = root / "omen-order-3"
    model.mkdir(parents=True, exist_ok=True)
    (model / "config.txt").write_text(
        "[training_settings]\nngram = 4\nencoding = utf-8\n",
        encoding="utf-8",
    )
    (model / "alphabet.txt").write_text(
        "a\nb\nc\nd\ne\nx\ny\n", encoding="utf-8"
    )
    (model / "IP.level").write_text(
        "0\tabc\n1\tabd\n", encoding="utf-8"
    )
    (model / "EP.level").write_text("", encoding="utf-8")
    (model / "CP.level").write_text(
        "0\tabcd\n0\tbcde\n1\tabcx\n0\tabdy\n",
        encoding="utf-8",
    )
    # One level per final password length. With ngram=4, lengths 4 and 5
    # become one- and two-transition candidates respectively.
    (model / "LN.level").write_text(
        "0\n0\n0\n0\n0\n", encoding="ascii"
    )
    return model


def _request(**parameters: object) -> GeneratorPrepareRequest:
    return GeneratorPrepareRequest(
        strategy_id=StrategyId.S3,
        parameters=parameters,
    )


def _drain(generator: MarkovGenerator, state, limit: int = 2):
    records = []
    while not generator.exhausted(state):
        records.extend(generator.next_batch(state, limit).records)
    return records


def test_markov_requires_configured_model():
    with pytest.raises(MarkovModelNotConfiguredError, match="未配置"):
        MarkovGenerator().prepare(_request(order=3))


def test_markov_loads_third_order_model_and_streams_by_level(tmp_path: Path):
    generator = MarkovGenerator(_write_omen_model(tmp_path))
    state = generator.prepare(_request(order=3, max_level=1))
    records = _drain(generator, state, limit=2)

    assert [record.value for record in records] == [
        "abcd", "abcde", "abcx", "abdy",
    ]
    assert [record.sources[0].score for record in records] == [0.0, 0.0, 1.0, 1.0]
    assert all(record.sources[0].kind == "markov" for record in records)
    assert all(record.sources[0].template == "order:3" for record in records)
    assert records[0].sources[0].public_dict()["score"] == 0.0


def test_markov_order_must_match_trained_ngram(tmp_path: Path):
    generator = MarkovGenerator(_write_omen_model(tmp_path))
    with pytest.raises(MarkovModelError, match="阶数必须在训练时确定"):
        generator.prepare(_request(order=2))


def test_markov_snapshot_restore_continues_at_exact_cursor(tmp_path: Path):
    generator = MarkovGenerator(_write_omen_model(tmp_path))
    uninterrupted = generator.prepare(_request(order=3, max_level=1))
    first = generator.next_batch(uninterrupted, 2)
    snapshot = generator.snapshot(uninterrupted)
    json.dumps(snapshot)
    expected = generator.next_batch(uninterrupted, 1)

    restored = generator.restore(snapshot)
    actual = generator.next_batch(restored, 1)

    assert first.candidates == ("abcd", "abcde")
    assert actual.candidates == expected.candidates
    assert actual.records == expected.records


def test_markov_states_are_run_local_and_independent(tmp_path: Path):
    generator = MarkovGenerator(_write_omen_model(tmp_path))
    first = generator.prepare(_request(order=3, max_level=1))
    second = generator.prepare(_request(order=3, max_level=1))

    assert generator.next_batch(first, 2).candidates == ("abcd", "abcde")
    assert generator.next_batch(second, 1).candidates == ("abcd",)
    assert first.cursor == 2
    assert second.cursor == 1


def test_markov_restore_rejects_changed_model(tmp_path: Path):
    model = _write_omen_model(tmp_path)
    original = MarkovGenerator(model)
    state = original.prepare(_request(order=3, max_level=1))
    original.next_batch(state, 1)
    snapshot = original.snapshot(state)

    with (model / "CP.level").open("a", encoding="utf-8") as handle:
        handle.write("2\tabcz\n")
    changed = MarkovGenerator(model)

    with pytest.raises(GeneratorStateError, match="模型版本不兼容"):
        changed.restore(snapshot)


def test_markov_filters_length_newline_nul_and_encoding(tmp_path: Path):
    generator = MarkovGenerator(_write_omen_model(tmp_path))
    records = _drain(
        generator,
        generator.prepare(_request(order=3, max_level=1, max_length=4)),
    )
    assert [record.value for record in records] == ["abcd", "abcx", "abdy"]
    assert not _is_usable_candidate(
        "a\nb", encoding="utf-8", minimum_length=1, maximum_length=10
    )
    assert not _is_usable_candidate(
        "a\x00b", encoding="utf-8", minimum_length=1, maximum_length=10
    )
    assert not _is_usable_candidate(
        "\udcff", encoding="utf-8", minimum_length=1, maximum_length=10
    )


def test_pipeline_can_select_markov_and_keeps_global_dedup(tmp_path: Path):
    plan = StrategyPlan(
        task_id="T-MARKOV",
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
                strategy_name="markov",
                priority=2,
                time_budget=1,
                candidate_budget=3,
                reason="third-order OMEN",
                parameters={"order": 3, "max_level": 1},
            ),
        ],
    )
    batches = list(CandidateGenerator(
        baseline_candidates=("abcd",),
        s3_generator_id="markov",
        markov_ruleset_path=str(_write_omen_model(tmp_path)),
        markov_order=3,
    ).iter_plan_batches(plan, batch_size=2))
    values = [value for batch in batches for value in batch.candidates]

    assert values == ["abcd", "abcde", "abcx", "abdy"]
    assert [batch.generator_id for batch in batches] == [
        "baseline", "markov", "markov",
    ]
    assert len(values) == len(set(values))
