from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from .candidate_types import CandidateRecord, CandidateSource
from .context import iter_context_candidates
from .enums import StrategyId
from .pcfg_lite import (
    DEFAULT_MAX_STRUCTURE_LENGTH,
    DEFAULT_MAX_TEMPLATES,
    DEFAULT_MIN_PROBABILITY,
    iter_pcfg_candidates,
)
from .schemas import StrategyPlan, TaskContext


MAX_CANDIDATE_LENGTH = 1024
MAX_EXECUTION_CANDIDATES = 100_000
DEFAULT_BATCH_SIZE = 1_000

DEFAULT_BASELINE_CANDIDATES = (
    "123456", "password", "123456789", "12345678", "12345", "qwerty",
    "abc123", "111111", "123123", "admin", "letmein", "welcome",
    "monkey", "dragon", "football", "iloveyou", "password1",
    "qwerty123", "000000", "1q2w3e4r",
)
DEFAULT_NUMBER_SUFFIXES = ("1", "12", "123", "1234", "520", "666", "888")
DEFAULT_YEAR_SUFFIXES = (
    "2026", "2025", "2024", "2023", "2022", "2021", "2020", "2000",
    "1999", "1998",
)
DEFAULT_SYMBOL_SUFFIXES = ("!", "@", "#")

_S2_PARAMETER_NAMES = frozenset({
    "capitalize_first", "all_upper", "all_lower", "common_number_suffix",
    "year_suffix", "common_substitution", "symbol_suffix",
})
_COMMON_SUBSTITUTIONS = str.maketrans({
    "a": "@", "A": "@", "e": "3", "E": "3", "i": "1", "I": "1",
    "o": "0", "O": "0", "s": "5", "S": "5",
})


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    strategy_id: StrategyId
    candidates: tuple[str, ...]
    records: tuple[CandidateRecord, ...] = ()

    def __post_init__(self) -> None:
        if self.records and len(self.records) != len(self.candidates):
            raise ValueError("records 与 candidates 数量必须一致")
        if self.records and tuple(item.value for item in self.records) != self.candidates:
            raise ValueError("records 与 candidates 的值和顺序必须一致")


class CandidateGenerator:
    """Generate ordered S1-S4 candidates under shared deduplication limits."""

    def __init__(
        self,
        *,
        baseline_candidates: Sequence[str] = DEFAULT_BASELINE_CANDIDATES,
        number_suffixes: Sequence[str] = DEFAULT_NUMBER_SUFFIXES,
        year_suffixes: Sequence[str] = DEFAULT_YEAR_SUFFIXES,
        symbol_suffixes: Sequence[str] = DEFAULT_SYMBOL_SUFFIXES,
    ) -> None:
        self.baseline_candidates = tuple(baseline_candidates)
        self.number_suffixes = tuple(number_suffixes)
        self.year_suffixes = tuple(year_suffixes)
        self.symbol_suffixes = tuple(symbol_suffixes)
        _validate_source(self.baseline_candidates, "baseline_candidates")
        _validate_suffixes(self.number_suffixes, "number_suffixes")
        _validate_suffixes(self.year_suffixes, "year_suffixes")
        _validate_suffixes(self.symbol_suffixes, "symbol_suffixes")

    def iter_plan_batches(
        self,
        plan: StrategyPlan,
        *,
        supplied_candidates: Iterable[str] = (),
        rule_seeds: Iterable[str] | None = None,
        pcfg_seeds: Iterable[str] | None = None,
        task_context: TaskContext | dict[str, object] | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_candidates: int = MAX_EXECUTION_CANDIDATES,
    ) -> Iterator[CandidateBatch]:
        """Yield stable, globally deduplicated candidate batches by priority."""
        if batch_size <= 0:
            raise ValueError("batch_size 必须大于 0")
        if max_candidates <= 0 or max_candidates > MAX_EXECUTION_CANDIDATES:
            raise ValueError(f"max_candidates 必须在 1～{MAX_EXECUTION_CANDIDATES} 之间")

        supplied = tuple(supplied_candidates)
        _validate_source(supplied, "supplied_candidates")
        baseline_values = _stable_unique((*supplied, *self.baseline_candidates))
        supplied_set = set(supplied)
        baseline_records = tuple(
            CandidateRecord(value, StrategyId.S1, (CandidateSource(
                kind="supplied" if value in supplied_set else "baseline",
                original=value,
                normalized=value,
            ),))
            for value in baseline_values
        )
        prepared_rule_seeds = baseline_values if rule_seeds is None else tuple(rule_seeds)
        prepared_pcfg_seeds = baseline_values if pcfg_seeds is None else tuple(pcfg_seeds)
        _validate_source(prepared_rule_seeds, "rule_seeds")
        _validate_source(prepared_pcfg_seeds, "pcfg_seeds")

        seen: set[str] = set()
        total = 0
        for strategy in sorted(plan.strategies, key=lambda item: item.priority):
            if strategy.candidate_budget <= 0:
                continue
            records = self._records_for_strategy(
                strategy.strategy_id,
                strategy.parameters,
                baseline_records=baseline_records,
                rule_seeds=prepared_rule_seeds,
                pcfg_seeds=prepared_pcfg_seeds,
                task_context=task_context,
            )
            accepted = 0
            batch: list[CandidateRecord] = []
            for record in records:
                if record.value in seen or not _is_valid_candidate(record.value):
                    continue
                seen.add(record.value)
                batch.append(record)
                accepted += 1
                total += 1
                if len(batch) == batch_size:
                    yield _to_batch(strategy.strategy_id, batch)
                    batch.clear()
                if accepted >= strategy.candidate_budget or total >= max_candidates:
                    break
            if batch:
                yield _to_batch(strategy.strategy_id, batch)
            if total >= max_candidates:
                return

    def build_execute_candidates(self, plan: StrategyPlan, **kwargs: object) -> list[str]:
        """Build the flat string list accepted by ExecutionRequest."""
        return [
            value
            for batch in self.iter_plan_batches(plan, **kwargs)
            for value in batch.candidates
        ]

    def build_candidate_records(self, plan: StrategyPlan, **kwargs: object) -> list[CandidateRecord]:
        """Build the provenance-preserving form for internal auditing."""
        return [
            record
            for batch in self.iter_plan_batches(plan, **kwargs)
            for record in batch.records
        ]

    def _records_for_strategy(
        self,
        strategy_id: StrategyId,
        parameters: dict[str, object],
        *,
        baseline_records: tuple[CandidateRecord, ...],
        rule_seeds: tuple[str, ...],
        pcfg_seeds: tuple[str, ...],
        task_context: TaskContext | dict[str, object] | None,
    ) -> Iterator[CandidateRecord]:
        if strategy_id == StrategyId.S1:
            yield from baseline_records
        elif strategy_id == StrategyId.S2:
            yield from self._iter_rule_records(rule_seeds, parameters)
        elif strategy_id == StrategyId.S3:
            years = _context_years(task_context) or self.year_suffixes
            yield from iter_pcfg_candidates(
                pcfg_seeds,
                years=years,
                numbers=self.number_suffixes,
                symbols=self.symbol_suffixes,
                max_templates=int(parameters.get("max_templates", DEFAULT_MAX_TEMPLATES)),
                min_probability=float(parameters.get("min_probability", DEFAULT_MIN_PROBABILITY)),
                max_structure_length=int(parameters.get(
                    "max_structure_length", DEFAULT_MAX_STRUCTURE_LENGTH
                )),
            )
        elif strategy_id == StrategyId.S4 and task_context is not None:
            yield from iter_context_candidates(task_context, parameters=parameters)

    def _iter_rule_records(
        self,
        seeds: Iterable[str],
        parameters: dict[str, object],
    ) -> Iterator[CandidateRecord]:
        enabled = {name for name in _S2_PARAMETER_NAMES if parameters.get(name) is True}
        for seed in seeds:
            direct: list[tuple[str, str]] = []
            if "capitalize_first" in enabled:
                direct.append((seed[:1].upper() + seed[1:], "capitalize_first"))
            if "all_upper" in enabled:
                direct.append((seed.upper(), "all_upper"))
            if "all_lower" in enabled:
                direct.append((seed.lower(), "all_lower"))
            if "common_substitution" in enabled:
                direct.append((seed.translate(_COMMON_SUBSTITUTIONS), "common_substitution"))

            stems = _stable_unique((seed, *(value for value, _ in direct)))
            for value, rule in direct:
                if value != seed:
                    yield _rule_record(value, seed, rule)
            for rule, suffixes in (
                ("common_number_suffix", self.number_suffixes),
                ("year_suffix", self.year_suffixes),
                ("symbol_suffix", self.symbol_suffixes),
            ):
                if rule not in enabled:
                    continue
                for stem in stems:
                    for suffix in suffixes:
                        yield _rule_record(f"{stem}{suffix}", seed, rule)


def _rule_record(value: str, seed: str, rule: str) -> CandidateRecord:
    return CandidateRecord(value, StrategyId.S2, (CandidateSource(
        kind="rule",
        original=seed,
        normalized=value,
        components=(rule,),
    ),))


def _to_batch(strategy_id: StrategyId, records: list[CandidateRecord]) -> CandidateBatch:
    prepared = tuple(records)
    return CandidateBatch(strategy_id, tuple(item.value for item in prepared), prepared)


def _context_years(value: TaskContext | dict[str, object] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    context = value if isinstance(value, TaskContext) else TaskContext.model_validate(value)
    return tuple(str(year) for year in context.years)


def _stable_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _validate_source(values: Iterable[str], field: str) -> None:
    for index, value in enumerate(values):
        if not _is_valid_candidate(value):
            raise ValueError(
                f"{field}[{index}] 必须为 1～{MAX_CANDIDATE_LENGTH} 个字符的单行文本"
            )


def _validate_suffixes(values: Iterable[str], field: str) -> None:
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
            raise ValueError(f"{field}[{index}] 必须为非空单行文本")


def _is_valid_candidate(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_CANDIDATE_LENGTH
        and "\n" not in value
        and "\r" not in value
    )
