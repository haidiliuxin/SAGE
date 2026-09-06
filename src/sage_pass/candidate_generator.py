from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from .enums import StrategyId
from .schemas import StrategyItem, StrategyPlan


MAX_CANDIDATE_LENGTH = 1024
MAX_EXECUTION_CANDIDATES = 100_000
DEFAULT_BATCH_SIZE = 1_000

# A compact built-in baseline keeps S1 usable without an external wordlist. Callers
# can inject a larger ordered source while retaining the same generation contract.
DEFAULT_BASELINE_CANDIDATES = (
    "123456",
    "password",
    "123456789",
    "12345678",
    "12345",
    "qwerty",
    "abc123",
    "111111",
    "123123",
    "admin",
    "letmein",
    "welcome",
    "monkey",
    "dragon",
    "football",
    "iloveyou",
    "password1",
    "qwerty123",
    "000000",
    "1q2w3e4r",
)

DEFAULT_NUMBER_SUFFIXES = ("1", "12", "123", "1234", "520", "666", "888")
DEFAULT_YEAR_SUFFIXES = (
    "2026",
    "2025",
    "2024",
    "2023",
    "2022",
    "2021",
    "2020",
    "2000",
    "1999",
    "1998",
)
DEFAULT_SYMBOL_SUFFIXES = ("!", "@", "#")

_S2_PARAMETER_NAMES = frozenset(
    {
        "capitalize_first",
        "all_upper",
        "all_lower",
        "common_number_suffix",
        "year_suffix",
        "common_substitution",
        "symbol_suffix",
    }
)
_COMMON_SUBSTITUTIONS = str.maketrans(
    {
        "a": "@",
        "A": "@",
        "e": "3",
        "E": "3",
        "i": "1",
        "I": "1",
        "o": "0",
        "O": "0",
        "s": "5",
        "S": "5",
    }
)


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    strategy_id: StrategyId
    candidates: tuple[str, ...]


class CandidateGenerator:
    """Generate ordered S1/S2 candidates without changing the HTTP API."""

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
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_candidates: int = MAX_EXECUTION_CANDIDATES,
    ) -> Iterator[CandidateBatch]:
        """Yield stable, globally deduplicated S1/S2 batches in priority order."""
        if batch_size <= 0:
            raise ValueError("batch_size 必须大于 0")
        if max_candidates <= 0 or max_candidates > MAX_EXECUTION_CANDIDATES:
            raise ValueError(
                f"max_candidates 必须在 1～{MAX_EXECUTION_CANDIDATES} 之间"
            )

        supplied = tuple(supplied_candidates)
        _validate_source(supplied, "supplied_candidates")
        baseline_source = _stable_unique((*supplied, *self.baseline_candidates))
        prepared_rule_seeds = (
            baseline_source if rule_seeds is None else tuple(rule_seeds)
        )
        _validate_source(prepared_rule_seeds, "rule_seeds")

        seen: set[str] = set()
        total = 0
        for strategy in sorted(plan.strategies, key=lambda item: item.priority):
            if strategy.candidate_budget <= 0:
                continue
            if strategy.strategy_id == StrategyId.S1:
                source = iter(baseline_source)
            elif strategy.strategy_id == StrategyId.S2:
                source = self._iter_rule_candidates(
                    prepared_rule_seeds, strategy.parameters
                )
            else:
                continue

            accepted = 0
            batch: list[str] = []
            for candidate in source:
                if candidate in seen:
                    continue
                if not _is_valid_candidate(candidate):
                    continue
                seen.add(candidate)
                batch.append(candidate)
                accepted += 1
                total += 1

                if len(batch) == batch_size:
                    yield CandidateBatch(strategy.strategy_id, tuple(batch))
                    batch.clear()
                if accepted >= strategy.candidate_budget or total >= max_candidates:
                    break

            if batch:
                yield CandidateBatch(strategy.strategy_id, tuple(batch))
            if total >= max_candidates:
                return

    def build_execute_candidates(
        self,
        plan: StrategyPlan,
        *,
        supplied_candidates: Iterable[str] = (),
        rule_seeds: Iterable[str] | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_candidates: int = MAX_EXECUTION_CANDIDATES,
    ) -> list[str]:
        """Build the flat list accepted by ExecutionRequest.candidates."""
        return [
            candidate
            for batch in self.iter_plan_batches(
                plan,
                supplied_candidates=supplied_candidates,
                rule_seeds=rule_seeds,
                batch_size=batch_size,
                max_candidates=max_candidates,
            )
            for candidate in batch.candidates
        ]

    def _iter_rule_candidates(
        self,
        seeds: Iterable[str],
        parameters: dict[str, object],
    ) -> Iterator[str]:
        enabled = {
            name for name in _S2_PARAMETER_NAMES if parameters.get(name) is True
        }
        for seed in seeds:
            direct_variants: list[str] = []
            if "capitalize_first" in enabled:
                direct_variants.append(seed[:1].upper() + seed[1:])
            if "all_upper" in enabled:
                direct_variants.append(seed.upper())
            if "all_lower" in enabled:
                direct_variants.append(seed.lower())
            if "common_substitution" in enabled:
                direct_variants.append(seed.translate(_COMMON_SUBSTITUTIONS))

            stems = _stable_unique((seed, *direct_variants))
            for candidate in direct_variants:
                if candidate != seed:
                    yield candidate
            if "common_number_suffix" in enabled:
                yield from _append_suffixes(stems, self.number_suffixes)
            if "year_suffix" in enabled:
                yield from _append_suffixes(stems, self.year_suffixes)
            if "symbol_suffix" in enabled:
                yield from _append_suffixes(stems, self.symbol_suffixes)


def _append_suffixes(
    stems: Iterable[str], suffixes: Iterable[str]
) -> Iterator[str]:
    for stem in stems:
        for suffix in suffixes:
            yield f"{stem}{suffix}"


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
        if (
            not isinstance(value, str)
            or not value
            or "\n" in value
            or "\r" in value
        ):
            raise ValueError(f"{field}[{index}] 必须为非空单行文本")


def _is_valid_candidate(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_CANDIDATE_LENGTH
        and "\n" not in value
        and "\r" not in value
    )
