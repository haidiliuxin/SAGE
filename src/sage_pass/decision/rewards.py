"""Versioned, pure reward calculation; resource accounting stays independent."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math

from ..interfaces import BatchOutcome
from .types import require_count, require_seconds

REWARD_VERSION = "target-budget-v1"


@dataclass(frozen=True, slots=True)
class RewardWeights:
    """Initial experimental defaults, not fitted or claimed optimal."""

    recovery: float = 1.0
    time: float = 0.1
    candidates: float = 0.1
    duplicates: float = 0.1

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            require_seconds(value, name, positive=name == "recovery")


@dataclass(frozen=True, slots=True)
class RewardContext:
    initial_targets: int
    candidate_budget: int
    time_budget: float

    def __post_init__(self) -> None:
        require_count(self.initial_targets, "initial_targets", positive=True)
        require_count(self.candidate_budget, "candidate_budget", positive=True)
        require_seconds(self.time_budget, "time_budget", positive=True)


@dataclass(frozen=True, slots=True)
class RewardBreakdown:
    version: str
    recovered_targets: int
    submitted_candidates: int
    tested_candidates: int
    duration: float
    recovery_fraction: float
    time_fraction: float
    candidate_fraction: float
    duplicate_fraction: float | None
    recovery_gain: float
    time_penalty: float
    candidate_penalty: float
    duplicate_penalty: float | None
    total: float
    gain_per_thousand_candidates: float
    gain_per_second: float | None
    duplicate_measurement_available: bool

    def as_dict(self) -> dict:
        return asdict(self)


def calculate_reward(
    outcome: BatchOutcome, context: RewardContext,
    weights: RewardWeights | None = None, *,
    duplicate_count: int | None = None, offered_count: int | None = None,
) -> RewardBreakdown:
    """Use original budgets as denominators; do not clip negative rewards.

    `recovered` must already be deduplicated by target over the whole run.
    Missing duplicate measurements stay null, rather than pretending they were
    measured as zero. Their contribution to the scalar is explicitly omitted.
    Real execution overruns remain visible as fractions greater than one.
    """
    weights = weights or RewardWeights()
    for name in ("candidate_count", "tested", "recovered"):
        require_count(getattr(outcome, name), name)
    require_seconds(outcome.duration, "duration")
    if outcome.tested > outcome.candidate_count:
        raise ValueError("tested must not exceed submitted candidates")
    if outcome.recovered > context.initial_targets:
        raise ValueError("new recovered targets exceed initial target count")
    if outcome.recovered and not outcome.tested:
        raise ValueError("new recoveries require tested candidates")
    if duplicate_count is None and offered_count is None:
        duplicate_count = getattr(outcome, "duplicate_count", None)
        offered_count = getattr(outcome, "offered_count", None)
    if (duplicate_count is None) != (offered_count is None):
        raise ValueError("duplicate_count and offered_count must be supplied together")
    duplicate_fraction = None
    if duplicate_count is not None:
        require_count(duplicate_count, "duplicate_count")
        require_count(offered_count, "offered_count")
        if offered_count != outcome.candidate_count + duplicate_count:
            raise ValueError("offered_count must equal submitted plus duplicates")
        duplicate_fraction = duplicate_count / max(1, offered_count)
    recovery_fraction = outcome.recovered / context.initial_targets
    time_fraction = outcome.duration / context.time_budget
    candidate_fraction = outcome.candidate_count / context.candidate_budget
    recovery_gain = weights.recovery * recovery_fraction
    time_penalty = weights.time * time_fraction
    candidate_penalty = weights.candidates * candidate_fraction
    duplicate_penalty = (
        weights.duplicates * duplicate_fraction if duplicate_fraction is not None else None
    )
    breakdown = RewardBreakdown(
        REWARD_VERSION, outcome.recovered, outcome.candidate_count, outcome.tested,
        outcome.duration, recovery_fraction, time_fraction, candidate_fraction,
        duplicate_fraction, recovery_gain, time_penalty, candidate_penalty,
        duplicate_penalty,
        recovery_gain - time_penalty - candidate_penalty - (duplicate_penalty or 0.0),
        1000 * outcome.recovered / max(1, outcome.candidate_count),
        outcome.recovered / outcome.duration if outcome.duration > 0 else None,
        duplicate_fraction is not None,
    )
    if any(isinstance(value, float) and not math.isfinite(value) for value in asdict(breakdown).values()):
        raise ValueError("reward arithmetic produced a non-finite value")
    return breakdown
