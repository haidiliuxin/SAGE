from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ..candidate_types import CandidateRecord
from ..enums import StrategyId
from ..transfer import TransferCandidateGenerator
from .base import GeneratorPrepareRequest, GeneratorState, ReplayableGenerator


@dataclass(frozen=True, slots=True)
class _PatternSnapshot:
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


class TransferGenerator(ReplayableGenerator):
    generator_id = "transfer"
    strategy_id = StrategyId.S5
    model_version = "transfer-v2"

    def __init__(
        self, transfer_generator: TransferCandidateGenerator | None = None
    ) -> None:
        self._transfer_generator = (
            transfer_generator or TransferCandidateGenerator()
        )

    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState:
        patterns = pattern_snapshots(request.transfer_patterns)
        return self._new_state(
            request,
            restore_data={
                "patterns": patterns,
                "seeds": list(request.transfer_seeds),
                "years": list(request.year_suffixes),
                "numbers": list(request.number_suffixes),
                "symbols": list(request.symbol_suffixes),
            },
        )

    def _iter_records(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        patterns = restore_patterns(state.restore_data.get("patterns", []))
        yield from self._transfer_generator.iter_records(
            patterns,
            seeds=tuple(str(item) for item in state.restore_data.get("seeds", [])),
            years=tuple(str(item) for item in state.restore_data.get("years", [])),
            numbers=tuple(str(item) for item in state.restore_data.get("numbers", [])),
            symbols=tuple(str(item) for item in state.restore_data.get("symbols", [])),
        )


class PatternKnowledgeGenerator(TransferGenerator):
    """S5 concrete generator; uses only abstract cross-task patterns."""

    generator_id = "pattern_knowledge"


def pattern_snapshots(patterns) -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "pattern_type": item.pattern_type,
            "pattern_signature": item.pattern_signature,
            "observation_count": item.observation_count,
            "task_count": item.task_count,
            "confidence": item.confidence,
        }
        for item in patterns
    ]


def restore_patterns(raw_patterns: object) -> list[_PatternSnapshot]:
    patterns: list[_PatternSnapshot] = []
    if not isinstance(raw_patterns, list):
        return patterns
    for raw in raw_patterns:
        if not isinstance(raw, dict):
            continue
        patterns.append(_PatternSnapshot(
            id=int(raw["id"]),
            scope="",
            target_type="",
            algorithm="",
            pattern_type=str(raw["pattern_type"]),
            pattern_signature=str(raw["pattern_signature"]),
            feature_data=dict(raw.get("feature_data", {})),
            observation_count=int(raw["observation_count"]),
            task_count=int(raw["task_count"]),
            confidence=float(raw["confidence"]),
            last_seen_at="",
        ))
    return patterns
