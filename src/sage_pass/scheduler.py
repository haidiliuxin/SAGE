from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class BanditWeights:
    success_probability: float = 0.45
    recent_gain: float = 0.30
    transfer: float = 0.15
    cost: float = 0.10

    def __post_init__(self) -> None:
        values = (
            self.success_probability,
            self.recent_gain,
            self.transfer,
            self.cost,
        )
        if any(value < 0 for value in values) or sum(values) <= 0:
            raise ValueError("bandit weights must be non-negative and not all zero")


@dataclass(frozen=True, slots=True)
class ArmSpec:
    """Static limits and planner prior for one target-group/strategy arm."""

    strategy_id: str
    priority: int
    candidate_budget: int
    time_budget: float
    transfer_score: float | None = None

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id must not be empty")
        if self.priority <= 0:
            raise ValueError("priority must be positive")
        if self.candidate_budget < 0 or self.time_budget < 0:
            raise ValueError("arm budgets must be non-negative")
        if self.transfer_score is not None and not 0 <= self.transfer_score <= 1:
            raise ValueError("transfer_score must be between 0 and 1")

    @property
    def transfer(self) -> float:
        if self.transfer_score is not None:
            return self.transfer_score
        return 1.0 / self.priority


@dataclass(slots=True)
class ArmStatistics:
    tested: int = 0
    recovered: int = 0
    time_cost: float = 0.0
    recent_gain: float = 0.0
    pulls: int = 0
    allocated_candidates: int = 0

    @property
    def success_rate(self) -> float:
        return self.recovered / self.tested if self.tested > 0 else 0.0


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    success_probability: float
    recent_gain: float
    transfer: float
    cost: float
    score: float


@dataclass(frozen=True, slots=True)
class SchedulingDecision:
    strategy_id: str
    candidate_limit: int
    time_limit: float
    score: ScoreBreakdown
    exploration: bool


class SchedulerStopReason(StrEnum):
    CANDIDATE_BUDGET = "candidate_budget"
    TIME_BUDGET = "time_budget"
    CANDIDATES_EXHAUSTED = "candidates_exhausted"
    STRATEGY_BUDGETS = "strategy_budgets"


class BanditScheduler:
    """Batch-level bandit scheduler with deterministic initial exploration."""

    def __init__(
        self,
        arms: Sequence[ArmSpec],
        *,
        total_candidate_budget: int,
        total_time_budget: float,
        exploration_rounds: int = 1,
        weights: BanditWeights | None = None,
    ) -> None:
        if not arms:
            raise ValueError("at least one bandit arm is required")
        if total_candidate_budget <= 0 or total_time_budget <= 0:
            raise ValueError("total budgets must be positive")
        if exploration_rounds <= 0:
            raise ValueError("exploration_rounds must be positive")
        by_id = {arm.strategy_id: arm for arm in arms}
        if len(by_id) != len(arms):
            raise ValueError("strategy_id must be unique")

        self._arms = by_id
        self._ordered_ids = tuple(
            arm.strategy_id
            for arm in sorted(arms, key=lambda item: (item.priority, item.strategy_id))
        )
        self._statistics = {
            strategy_id: ArmStatistics() for strategy_id in self._ordered_ids
        }
        self.total_candidate_budget = total_candidate_budget
        self.total_time_budget = float(total_time_budget)
        self.exploration_rounds = exploration_rounds
        self.weights = weights or BanditWeights()

    @property
    def total_allocated_candidates(self) -> int:
        return sum(
            stats.allocated_candidates for stats in self._statistics.values()
        )

    @property
    def total_time_cost(self) -> float:
        return sum(stats.time_cost for stats in self._statistics.values())

    def statistics(self, strategy_id: str) -> ArmStatistics:
        return replace(self._require_statistics(strategy_id))

    def score(self, strategy_id: str, next_batch_size: int) -> ScoreBreakdown:
        if next_batch_size <= 0:
            raise ValueError("next_batch_size must be positive")
        spec = self._require_arm(strategy_id)
        stats = self._require_statistics(strategy_id)

        # Jeffreys' prior prevents an early zero-yield batch from assigning an
        # arm a permanent probability of zero. The candidate probability is
        # projected over the size of the next batch.
        candidate_probability = (stats.recovered + 0.5) / (stats.tested + 1.0)
        success_probability = 1.0 - math.pow(
            1.0 - candidate_probability, next_batch_size
        )
        cost = min(
            1.0,
            stats.time_cost / spec.time_budget if spec.time_budget > 0 else 1.0,
        )
        total = (
            self.weights.success_probability * success_probability
            + self.weights.recent_gain * stats.recent_gain
            + self.weights.transfer * spec.transfer
            - self.weights.cost * cost
        )
        return ScoreBreakdown(
            success_probability=success_probability,
            recent_gain=stats.recent_gain,
            transfer=spec.transfer,
            cost=cost,
            score=total,
        )

    def select(self, next_batch_sizes: Mapping[str, int]) -> SchedulingDecision | None:
        remaining_total = (
            self.total_candidate_budget - self.total_allocated_candidates
        )
        effective_sizes = {
            strategy_id: min(
                next_batch_sizes.get(strategy_id, 0),
                self._arms[strategy_id].candidate_budget
                - self._statistics[strategy_id].allocated_candidates,
                remaining_total,
            )
            for strategy_id in self._ordered_ids
        }
        eligible = [
            strategy_id
            for strategy_id in self._ordered_ids
            if self._eligible(strategy_id, effective_sizes[strategy_id])
        ]
        if not eligible:
            return None

        exploring = [
            strategy_id
            for strategy_id in eligible
            if self._statistics[strategy_id].pulls < self.exploration_rounds
        ]
        is_exploration = bool(exploring)
        if exploring:
            selected = exploring[0]
            breakdown = self.score(selected, effective_sizes[selected])
        else:
            ranked = [
                (
                    self.score(strategy_id, effective_sizes[strategy_id]),
                    self._arms[strategy_id],
                )
                for strategy_id in eligible
            ]
            breakdown, spec = min(
                ranked,
                key=lambda item: (
                    -item[0].score,
                    item[1].priority,
                    item[1].strategy_id,
                ),
            )
            selected = spec.strategy_id

        spec = self._arms[selected]
        stats = self._statistics[selected]
        candidate_limit = effective_sizes[selected]
        time_limit = min(
            spec.time_budget - stats.time_cost,
            self.total_time_budget - self.total_time_cost,
        )
        return SchedulingDecision(
            strategy_id=selected,
            candidate_limit=candidate_limit,
            time_limit=time_limit,
            score=breakdown,
            exploration=is_exploration,
        )

    def observe(
        self,
        strategy_id: str,
        *,
        candidate_count: int,
        tested: int,
        recovered: int,
        duration: float,
    ) -> None:
        spec = self._require_arm(strategy_id)
        stats = self._require_statistics(strategy_id)
        if candidate_count <= 0:
            raise ValueError("candidate_count must be positive")
        if tested < 0 or tested > candidate_count:
            raise ValueError("tested must be between zero and candidate_count")
        if recovered < 0 or recovered > tested:
            raise ValueError("recovered must be between zero and tested")
        if duration < 0:
            raise ValueError("duration must be non-negative")
        if stats.allocated_candidates + candidate_count > spec.candidate_budget:
            raise ValueError("observation exceeds arm candidate budget")
        if self.total_allocated_candidates + candidate_count > self.total_candidate_budget:
            raise ValueError("observation exceeds total candidate budget")

        # A gain of 1.0 means at least one recovery per 1,000 tested candidates.
        # EWMA keeps the score responsive without discarding earlier feedback.
        batch_gain = min(1.0, (recovered * 1_000) / max(tested, 1))
        stats.recent_gain = (
            batch_gain
            if stats.pulls == 0
            else 0.5 * batch_gain + 0.5 * stats.recent_gain
        )
        stats.pulls += 1
        stats.allocated_candidates += candidate_count
        stats.tested += tested
        stats.recovered += recovered
        stats.time_cost += duration

    def stop_reason(
        self, next_batch_sizes: Mapping[str, int]
    ) -> SchedulerStopReason | None:
        if self.total_allocated_candidates >= self.total_candidate_budget:
            return SchedulerStopReason.CANDIDATE_BUDGET
        if self.total_time_cost >= self.total_time_budget:
            return SchedulerStopReason.TIME_BUDGET
        if not any(size > 0 for size in next_batch_sizes.values()):
            return SchedulerStopReason.CANDIDATES_EXHAUSTED
        if not any(
            self._eligible(strategy_id, next_batch_sizes.get(strategy_id, 0))
            for strategy_id in self._ordered_ids
        ):
            return SchedulerStopReason.STRATEGY_BUDGETS
        return None

    def _eligible(self, strategy_id: str, next_batch_size: int) -> bool:
        if next_batch_size <= 0:
            return False
        spec = self._arms[strategy_id]
        stats = self._statistics[strategy_id]
        return (
            self.total_allocated_candidates < self.total_candidate_budget
            and self.total_time_cost < self.total_time_budget
            and stats.allocated_candidates < spec.candidate_budget
            and stats.time_cost < spec.time_budget
        )

    def _require_arm(self, strategy_id: str) -> ArmSpec:
        try:
            return self._arms[strategy_id]
        except KeyError as exc:
            raise KeyError(f"unknown strategy_id: {strategy_id}") from exc

    def _require_statistics(self, strategy_id: str) -> ArmStatistics:
        self._require_arm(strategy_id)
        return self._statistics[strategy_id]
