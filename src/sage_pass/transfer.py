from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Iterator, Protocol, Sequence

from .candidate_types import CandidateRecord, CandidateSource
from .context import normalize_keyword
from .enums import StrategyId
from .feedback import FeedbackConfig
from .schemas import TaskContext


TRANSFERABLE_PATTERN_TYPES = frozenset({
    "structure_signature",
    "suffix_pattern",
    "prefix_pattern",
    "case_pattern",
    "year_pattern",
    "common_substitution",
})


def current_task_transfer_seeds(
    baseline_seeds: Iterable[str],
    task_context: TaskContext | dict[str, object] | None,
) -> tuple[str, ...]:
    """Return only S1/legal seeds and explicitly authorized current context."""
    values = list(baseline_seeds)
    if task_context is not None:
        context = (
            task_context
            if isinstance(task_context, TaskContext)
            else TaskContext.model_validate(task_context)
        )
        raw = [*context.keywords]
        if context.region:
            raw.append(context.region)
        if context.organization:
            raw.append(context.organization)
        for item in raw:
            normalized = normalize_keyword(item)
            if normalized:
                values.append(normalized)
    return tuple(dict.fromkeys(values))


class PatternLike(Protocol):
    id: int
    scope: str
    target_type: str
    algorithm: str
    pattern_type: str
    pattern_signature: str
    feature_data: dict[str, object]
    observation_count: int
    task_count: int
    confidence: float
    last_seen_at: str


@dataclass(frozen=True, slots=True)
class KnowledgeSummary:
    available: bool = False
    pattern_count: int = 0
    top_pattern_types: tuple[str, ...] = ()
    highest_confidence: float = 0.0
    suggested_s5_max_budget: int = 0
    transfer_score: float = 0.0

    def public_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "pattern_count": self.pattern_count,
            "top_pattern_types": list(self.top_pattern_types),
            "highest_confidence": self.highest_confidence,
            "suggested_s5_max_budget": self.suggested_s5_max_budget,
        }


class TransferScorer:
    def __init__(self, *, recency_half_life_days: float = 90.0) -> None:
        if recency_half_life_days <= 0:
            raise ValueError("recency_half_life_days must be positive")
        self.recency_half_life_days = recency_half_life_days

    def pattern_score(self, pattern: PatternLike, *, now: datetime | None = None) -> float:
        current = now or datetime.now(timezone.utc)
        seen = datetime.fromisoformat(pattern.last_seen_at)
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (current - seen).total_seconds() / 86_400)
        recency = math.pow(0.5, age_days / self.recency_half_life_days)
        frequency = min(1.0, math.log1p(pattern.observation_count) / math.log(21.0))
        diversity = min(1.0, pattern.task_count / 5.0)
        value = (
            0.45 * min(1.0, max(0.0, pattern.confidence))
            + 0.25 * frequency
            + 0.20 * diversity
            + 0.10 * recency
        )
        return round(min(1.0, max(0.0, value)), 6)

    def aggregate(self, patterns: Sequence[PatternLike]) -> float:
        if not patterns:
            return 0.0
        scores = sorted((self.pattern_score(item) for item in patterns), reverse=True)
        top = scores[:5]
        return round(min(1.0, 0.7 * top[0] + 0.3 * sum(top) / len(top)), 6)


def load_transfer_knowledge(
    repository,
    *,
    target_type: str,
    algorithm: str,
    candidate_budget: int,
    config: FeedbackConfig,
) -> tuple[list[PatternLike], KnowledgeSummary]:
    rows = repository.list(
        target_type=target_type,
        algorithm=(algorithm or "unknown").strip().casefold(),
        minimum_observations=config.minimum_observations,
        minimum_tasks=config.minimum_tasks,
        limit=config.maximum_patterns_per_scope,
    )
    transferable = [row for row in rows if row.pattern_type in TRANSFERABLE_PATTERN_TYPES]
    scorer = TransferScorer(recency_half_life_days=config.recency_half_life_days)
    score = scorer.aggregate(transferable)
    type_counts: dict[str, int] = {}
    for row in transferable:
        type_counts[row.pattern_type] = type_counts.get(row.pattern_type, 0) + 1
    top_types = tuple(
        name for name, _ in sorted(type_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    )
    highest = max((row.confidence for row in transferable), default=0.0)
    suggested = min(candidate_budget, max(0, len(transferable) * 100))
    return transferable, KnowledgeSummary(
        available=bool(transferable),
        pattern_count=len(transferable),
        top_pattern_types=top_types,
        highest_confidence=round(min(1.0, max(0.0, highest)), 6),
        suggested_s5_max_budget=suggested,
        transfer_score=score,
    )


class TransferCandidateGenerator:
    """Lazily apply bounded historical abstractions to current-task seeds."""

    _SUBSTITUTION_TABLES = {
        "a_to_at": str.maketrans({"a": "@", "A": "@"}),
        "e_to_3": str.maketrans({"e": "3", "E": "3"}),
        "i_or_l_to_1": str.maketrans({"i": "1", "I": "1", "l": "1", "L": "1"}),
        "o_to_0": str.maketrans({"o": "0", "O": "0"}),
        "s_to_5": str.maketrans({"s": "5", "S": "5"}),
        "s_to_dollar": str.maketrans({"s": "$", "S": "$"}),
    }

    def iter_records(
        self,
        patterns: Sequence[PatternLike],
        *,
        seeds: Iterable[str],
        years: Sequence[str],
        numbers: Sequence[str],
        symbols: Sequence[str],
    ) -> Iterator[CandidateRecord]:
        prepared_seeds = tuple(dict.fromkeys(seeds))
        ordered_patterns = sorted(
            patterns,
            key=lambda item: (
                -item.confidence,
                -item.task_count,
                -item.observation_count,
                item.pattern_type,
                item.pattern_signature,
                item.id,
            ),
        )
        for pattern in ordered_patterns:
            for seed in prepared_seeds:
                for value, operation in self._apply(
                    pattern, seed, years=years, numbers=numbers, symbols=symbols
                ):
                    if value == seed:
                        continue
                    yield CandidateRecord(value, StrategyId.S5, (CandidateSource(
                        kind="transfer_pattern",
                        original=seed,
                        normalized=value,
                        components=(operation, f"current_seed:{seed}"),
                        pattern_id=pattern.id,
                        pattern_signature=pattern.pattern_signature,
                        pattern_confidence=pattern.confidence,
                    ),))

    def _apply(
        self,
        pattern: PatternLike,
        seed: str,
        *,
        years: Sequence[str],
        numbers: Sequence[str],
        symbols: Sequence[str],
    ) -> Iterator[tuple[str, str]]:
        kind, signature = pattern.pattern_type, pattern.pattern_signature
        if kind == "case_pattern" and signature == "capitalized":
            yield seed[:1].upper() + seed[1:].lower(), "capitalized_word"
        if kind in {"year_pattern", "suffix_pattern"} and signature == "year4":
            for year in years:
                yield f"{seed}{year}", "word_plus_year"
        if kind == "suffix_pattern" and signature == "digits_1_4":
            for number in numbers:
                yield f"{seed}{number}", "word_plus_digits"
        if kind == "suffix_pattern" and signature == "digits_1_4_plus_symbol":
            for number in numbers:
                for symbol in symbols:
                    yield f"{seed}{number}{symbol}", "word_plus_digits_plus_symbol"
        if kind == "prefix_pattern" and signature in {"digits_1_4", "year4"}:
            values = years if signature == "year4" else numbers
            for value in values:
                yield f"{value}{seed}", "digits_plus_word"
        if kind == "structure_signature":
            if re.fullmatch(r"U\d+D[1-4]", signature):
                for number in numbers:
                    yield f"{seed.upper()}{number}", "acronym_plus_digits"
            elif re.fullmatch(r"U1L\d+D[1-4]", signature):
                for number in numbers:
                    yield f"{seed[:1].upper()}{seed[1:].lower()}{number}", "capitalized_word_plus_digits"
            elif re.fullmatch(r"(?:L|U1L|U)\d+D[1-4]S1", signature):
                for number in numbers:
                    for symbol in symbols:
                        yield f"{seed}{number}{symbol}", "word_plus_digits_plus_symbol"
        if kind == "common_substitution":
            table = self._SUBSTITUTION_TABLES.get(signature)
            if table is not None:
                transformed = seed.translate(table)
                if transformed != seed:
                    yield transformed, "controlled_substitution"
                    for value in (*years, *numbers):
                        yield f"{transformed}{value}", "controlled_substitution_plus_suffix"


def current_task_transfer_seeds(
    baseline_seeds: Sequence[str],
    task_context: TaskContext | dict[str, object] | None,
) -> tuple[str, ...]:
    values = list(baseline_seeds)
    if task_context is not None:
        context = (
            task_context
            if isinstance(task_context, TaskContext)
            else TaskContext.model_validate(task_context)
        )
        raw = [*context.keywords]
        if context.region:
            raw.append(context.region)
        if context.organization:
            raw.append(context.organization)
        for item in raw:
            normalized = normalize_keyword(item)
            if normalized:
                values.append(normalized)
    return tuple(dict.fromkeys(values))
