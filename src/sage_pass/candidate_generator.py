from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime, timezone

from .candidate_types import CandidateBatch, CandidateRecord
from .enums import StrategyId
from .generators import (
    STRATEGY_GENERATOR_IDS,
    GeneratorPrepareRequest,
    GeneratorProtocol,
    GeneratorRegistry,
    GeneratorSnapshot,
    GeneratorState,
    build_default_registry,
)
from .information import build_information_profile
from .schemas import StrategyPlan, TaskContext
from .transfer import (
    PatternLike,
    TransferCandidateGenerator,
    current_task_transfer_seeds,
)


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

class CandidateGenerator:
    """Registry-backed pipeline with shared validation, budgets, and dedupe."""

    def __init__(
        self,
        *,
        baseline_candidates: Sequence[str] = DEFAULT_BASELINE_CANDIDATES,
        number_suffixes: Sequence[str] = DEFAULT_NUMBER_SUFFIXES,
        year_suffixes: Sequence[str] = DEFAULT_YEAR_SUFFIXES,
        symbol_suffixes: Sequence[str] = DEFAULT_SYMBOL_SUFFIXES,
        transfer_generator: TransferCandidateGenerator | None = None,
        registry: GeneratorRegistry | None = None,
        pcfg_variant: str = "pcfg_lite",
        pcfg_ruleset_path: str | None = None,
        markov_ruleset_path: str | None = None,
        markov_order: int = 3,
        s3_generator_id: str | None = None,
        s4_generator_id: str | None = None,
    ) -> None:
        self.baseline_candidates = tuple(baseline_candidates)
        self.number_suffixes = tuple(number_suffixes)
        self.year_suffixes = tuple(year_suffixes)
        self.symbol_suffixes = tuple(symbol_suffixes)
        self.registry = registry or build_default_registry(
            transfer_generator=transfer_generator,
            pcfg_ruleset_path=pcfg_ruleset_path,
            markov_ruleset_path=markov_ruleset_path,
            markov_order=markov_order,
        )
        selected_s3_generator = s3_generator_id or pcfg_variant
        if selected_s3_generator not in {"pcfg_lite", "pcfg_full", "markov"}:
            raise ValueError(
                "S3 generator 必须为 'pcfg_lite'、'pcfg_full' 或 'markov'"
            )
        self.strategy_generator_ids = dict(STRATEGY_GENERATOR_IDS)
        self.strategy_generator_ids[StrategyId.S3] = selected_s3_generator
        if s4_generator_id not in {None, "auto", "context", "history", "hybrid"}:
            raise ValueError(
                "S4 generator 必须为 'auto'、'context'、'history' 或 'hybrid'"
            )
        self.s4_generator_id = (
            None if s4_generator_id in {None, "auto"} else s4_generator_id
        )
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
        historical_passwords: Iterable[str] = (),
        transfer_patterns: Sequence[PatternLike] = (),
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
        prepared_rule_seeds = baseline_values if rule_seeds is None else tuple(rule_seeds)
        prepared_pcfg_seeds = baseline_values if pcfg_seeds is None else tuple(pcfg_seeds)
        prepared_transfer_seeds = current_task_transfer_seeds(
            baseline_values, task_context
        )
        _validate_source(prepared_rule_seeds, "rule_seeds")
        _validate_source(prepared_pcfg_seeds, "pcfg_seeds")
        prepared_context = (
            task_context
            if isinstance(task_context, TaskContext)
            else TaskContext.model_validate(task_context)
            if task_context is not None
            else None
        )
        prepared_history = tuple(dict.fromkeys(historical_passwords))
        _validate_source(prepared_history, "historical_passwords")
        information_profile = build_information_profile(
            prepared_context, prepared_history
        )
        context_years = (
            tuple(str(year) for year in prepared_context.years)
            if prepared_context is not None
            else ()
        )
        effective_years = context_years or self.year_suffixes
        personalized_years = _stable_unique((
            str(datetime.now(timezone.utc).year),
            *context_years,
            *self.year_suffixes,
        ))

        seen: set[str] = set()
        total = 0
        for strategy in sorted(plan.strategies, key=lambda item: item.priority):
            if strategy.candidate_budget <= 0:
                continue
            generator_id = self._generator_id_for_strategy(
                strategy.strategy_id,
                has_personal_information=(
                    information_profile.has_personal_information
                ),
                has_historical_passwords=(
                    information_profile.has_historical_passwords
                ),
            )
            generator = self.registry.get(generator_id)
            state = generator.prepare(GeneratorPrepareRequest(
                strategy_id=strategy.strategy_id,
                parameters=strategy.parameters,
                supplied_candidates=supplied,
                baseline_candidates=self.baseline_candidates,
                rule_seeds=prepared_rule_seeds,
                pcfg_seeds=prepared_pcfg_seeds,
                task_context=prepared_context,
                historical_passwords=prepared_history,
                transfer_patterns=tuple(transfer_patterns),
                transfer_seeds=prepared_transfer_seeds,
                number_suffixes=self.number_suffixes,
                rule_year_suffixes=self.year_suffixes,
                year_suffixes=effective_years,
                personalized_years=personalized_years,
                symbol_suffixes=self.symbol_suffixes,
            ))
            yield from self._iter_strategy_batches(
                generator_id=generator_id,
                generator=generator,
                state=state,
                strategy_budget=strategy.candidate_budget,
                batch_size=batch_size,
                max_candidates=max_candidates,
                seen=seen,
                total_so_far=total,
            )
            total = len(seen)
            if total >= max_candidates:
                return

    def _generator_id_for_strategy(
        self,
        strategy_id: StrategyId,
        *,
        has_personal_information: bool,
        has_historical_passwords: bool,
    ) -> str:
        if strategy_id != StrategyId.S4:
            return self.strategy_generator_ids[strategy_id]
        if self.s4_generator_id is not None:
            return self.s4_generator_id
        if has_personal_information and has_historical_passwords:
            return "hybrid"
        if has_historical_passwords:
            return "history"
        return "context"

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

    def _iter_strategy_batches(
        self,
        *,
        generator_id: str,
        generator: GeneratorProtocol,
        state: GeneratorState,
        strategy_budget: int,
        batch_size: int,
        max_candidates: int,
        seen: set[str],
        total_so_far: int,
    ) -> Iterator[CandidateBatch]:
        accepted = 0
        total = total_so_far
        output: list[CandidateRecord] = []
        while (
            accepted < strategy_budget
            and total < max_candidates
            and not generator.exhausted(state)
        ):
            capacity = min(
                batch_size - len(output),
                strategy_budget - accepted,
                max_candidates - total,
            )
            raw_batch = generator.next_batch(state, capacity)
            if not raw_batch.records and not raw_batch.exhausted:
                raise RuntimeError(
                    f"生成器 {generator_id!r} 未耗尽但返回了空批次"
                )
            for record in raw_batch.records:
                if record.value in seen or not _is_valid_candidate(record.value):
                    continue
                seen.add(record.value)
                output.append(record)
                accepted += 1
                total += 1
            if len(output) == batch_size:
                yield _pipeline_batch(
                    generator_id,
                    state.strategy_id,
                    output,
                    exhausted=(
                        generator.exhausted(state)
                        or accepted >= strategy_budget
                        or total >= max_candidates
                    ),
                    snapshot=generator.snapshot(state),
                )
                output.clear()
        if output:
            yield _pipeline_batch(
                generator_id,
                state.strategy_id,
                output,
                exhausted=(
                    generator.exhausted(state)
                    or accepted >= strategy_budget
                    or total >= max_candidates
                ),
                snapshot=generator.snapshot(state),
            )


def _pipeline_batch(
    generator_id: str,
    strategy_id: StrategyId,
    records: list[CandidateRecord],
    *,
    exhausted: bool,
    snapshot: GeneratorSnapshot,
) -> CandidateBatch:
    prepared = tuple(records)
    return CandidateBatch(
        strategy_id,
        tuple(item.value for item in prepared),
        prepared,
        generator_id=generator_id,
        exhausted=exhausted,
        snapshot=snapshot,
    )


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
    if not (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_CANDIDATE_LENGTH
        and "\n" not in value
        and "\r" not in value
        and "\x00" not in value
    ):
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return True
