"""B-side integration types; public HTTP/database schemas remain owned by A."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..interfaces import BatchOutcome
from ..scheduler import ArmSpec, ScoreBreakdown


def require_count(value: int, name: str, *, positive: bool = False) -> None:
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(f"{name} must be a {'positive' if positive else 'non-negative'} integer")


def require_seconds(value: float, name: str, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'non-negative'}")


@dataclass(frozen=True, slots=True)
class DecisionArm:
    arm_id: str
    strategy_id: str
    generator_id: str
    priority: int
    candidate_budget: int
    time_budget: float
    target_group_id: str = "default"
    parameter_profile: str = "default"
    transfer_score: float | None = None

    def __post_init__(self) -> None:
        for name in ("arm_id", "strategy_id", "generator_id", "target_group_id", "parameter_profile"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.arm_id == "__round_robin__":
            raise ValueError("reserved arm_id")
        require_count(self.priority, "priority", positive=True)
        require_count(self.candidate_budget, "candidate_budget")
        require_seconds(self.time_budget, "time_budget")
        if self.transfer_score is not None:
            require_seconds(self.transfer_score, "transfer_score")
            if self.transfer_score > 1:
                raise ValueError("transfer_score must be at most one")

    def legacy_spec(self) -> ArmSpec:
        # The legacy engine's key is named strategy_id. Internally it receives
        # the unique arm_id; business strategy labels stay in this adapter.
        return ArmSpec(
            self.arm_id, self.priority, self.candidate_budget,
            self.time_budget, self.transfer_score,
        )


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    arm_id: str
    strategy_id: str
    candidate_limit: int
    time_limit: float
    score: ScoreBreakdown
    exploration: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionFeedback(BatchOutcome):
    """`recovered` counts targets; `successful_candidates` counts hit candidates.

    IDs are anonymous experiment identifiers, never recovered plaintext.
    A single candidate can recover several targets.
    """

    successful_candidates: int
    recovered_target_ids: tuple[str, ...]
    duplicate_count: int = 0
    offered_count: int = 0

    def __post_init__(self) -> None:
        for name in ("candidate_count", "tested", "recovered", "successful_candidates", "duplicate_count", "offered_count"):
            require_count(getattr(self, name), name)
        require_seconds(self.duration, "duration")
        if not 0 <= self.successful_candidates <= self.tested <= self.candidate_count:
            raise ValueError("require successful_candidates <= tested <= candidate_count")
        if self.recovered < self.successful_candidates:
            raise ValueError("each successful candidate must recover at least one new target")
        if bool(self.recovered) != bool(self.successful_candidates):
            raise ValueError("new targets and successful candidates must both be zero or both positive")
        if any(not isinstance(item, str) or not item for item in self.recovered_target_ids):
            raise ValueError("recovered target IDs must be non-empty strings")
        if len(set(self.recovered_target_ids)) != self.recovered:
            raise ValueError("recovered_target_ids must contain exactly recovered distinct IDs")
        if len(self.recovered_target_ids) != self.recovered:
            raise ValueError("duplicate recovered target IDs")
        if self.offered_count != self.candidate_count + self.duplicate_count:
            raise ValueError("offered_count must equal candidate_count + duplicate_count")

    def as_dict(self) -> dict:
        result = BatchOutcome.as_dict(self)
        result.update(
            successful_candidates=self.successful_candidates,
            recovered_target_ids=list(self.recovered_target_ids),
            duplicate_count=self.duplicate_count,
            offered_count=self.offered_count,
        )
        return result
