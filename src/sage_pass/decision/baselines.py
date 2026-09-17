"""Adapt legacy baselines and target-reward UCB to DecisionPolicy methods."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from ..enums import SchedulerType
from ..interfaces import BatchOutcome
from ..scheduler import build_scheduler, decision_diagnostics
from .types import DecisionArm, DecisionFeedback, PolicyDecision, require_count, require_seconds
from .costs import UCBConfig


class BaselinePolicy:
    """One outstanding decision at a time; checkpoints are batch boundaries.

    The inherited heuristic remains a candidate-hit baseline. The adapter
    preserves true recovered-target counts separately instead of pretending
    that a multi-target recovery count is a Bernoulli success count.
    """

    def __init__(
        self, scheduler_type: SchedulerType | str, arms: Sequence[DecisionArm], *,
        total_candidate_budget: int, total_time_budget: float,
        initial_targets: int | None = None, ucb_config: UCBConfig | None = None,
    ) -> None:
        self.scheduler_type = SchedulerType(scheduler_type)
        if self.scheduler_type not in {
            SchedulerType.FIXED, SchedulerType.ROUND_ROBIN, SchedulerType.HEURISTIC_BANDIT,
            SchedulerType.UCB, SchedulerType.COST_AWARE_UCB,
        }:
            raise ValueError("this milestone does not support the requested policy")
        self.is_ucb = self.scheduler_type in {SchedulerType.UCB, SchedulerType.COST_AWARE_UCB}
        self.initial_targets = initial_targets
        self.ucb_config = ucb_config or UCBConfig()
        require_count(total_candidate_budget, "total_candidate_budget", positive=True)
        require_seconds(total_time_budget, "total_time_budget", positive=True)
        if not arms or len({arm.arm_id for arm in arms}) != len(arms):
            raise ValueError("arms must be non-empty with unique arm_id values")
        self.arms = tuple(sorted(arms, key=lambda arm: (arm.priority, arm.arm_id)))
        self._by_id = {arm.arm_id: arm for arm in self.arms}
        self.total_candidate_budget = total_candidate_budget
        self.total_time_budget = total_time_budget
        self._engine = build_scheduler(
            self.scheduler_type, [arm.legacy_spec() for arm in self.arms],
            total_candidate_budget=total_candidate_budget,
            total_time_budget=total_time_budget,
            initial_targets=initial_targets, ucb_config=self.ucb_config,
        )
        self._target_counts = {arm.arm_id: 0 for arm in self.arms}
        self._recovered_ids: set[str] = set()
        self._pending: PolicyDecision | None = None

    def _validate_available(self, sizes: Mapping[str, int]) -> None:
        if set(sizes) - self._by_id.keys():
            raise ValueError("unknown arm_id in available batches")
        for value in sizes.values():
            require_count(value, "next_batch_size")

    def select(self, next_batch_sizes: Mapping[str, int]) -> PolicyDecision | None:
        if self._pending is not None:
            raise ValueError("observe the pending decision before selecting again")
        self._validate_available(next_batch_sizes)
        selected = self._engine.select(next_batch_sizes)
        if selected is None:
            return None
        arm = self._by_id[selected.strategy_id]
        self._pending = PolicyDecision(
            arm.arm_id, arm.strategy_id, selected.candidate_limit,
            selected.time_limit, selected.score, selected.exploration,
        )
        return self._pending

    def observe_outcome(self, arm_id: str, outcome: BatchOutcome) -> None:
        decision = self._pending
        if decision is None or arm_id != decision.arm_id or outcome.arm_id != arm_id:
            raise ValueError("outcome does not match the pending arm")
        if outcome.strategy_id != decision.strategy_id:
            raise ValueError("outcome business strategy does not match the arm")
        require_count(outcome.candidate_count, "candidate_count", positive=True)
        require_count(outcome.tested, "tested")
        require_count(outcome.recovered, "recovered")
        require_seconds(outcome.duration, "duration")
        if outcome.candidate_count > decision.candidate_limit or outcome.tested > outcome.candidate_count:
            raise ValueError("outcome exceeds the submitted candidate limit")
        if outcome.duration > decision.time_limit:
            raise ValueError("outcome exceeds the simulated time limit")
        if isinstance(outcome, DecisionFeedback):
            hits = outcome.successful_candidates
            if self._recovered_ids.intersection(outcome.recovered_target_ids):
                raise ValueError("target recovery must be globally unique within the run")
        else:
            # Compatibility for the current single-target public outcome.
            hits = outcome.recovered
            if hits > outcome.tested and not self.is_ucb:
                raise ValueError("multi-target feedback requires successful_candidates")
        if self.is_ucb:
            self._engine.observe_outcome(arm_id, outcome)
        else:
            self._engine.observe(
                arm_id, candidate_count=outcome.candidate_count, tested=outcome.tested,
                recovered=hits, duration=outcome.duration,
            )
        self._target_counts[arm_id] += outcome.recovered
        if isinstance(outcome, DecisionFeedback):
            self._recovered_ids.update(outcome.recovered_target_ids)
        self._pending = None

    def stop_reason(self, next_batch_sizes: Mapping[str, int]) -> str | None:
        self._validate_available(next_batch_sizes)
        reason = self._engine.stop_reason(next_batch_sizes)
        return reason.value if reason is not None else None

    def diagnostics(self, next_batch_sizes: Mapping[str, int]) -> dict:
        if self._pending is not None:
            raise ValueError("diagnostics must be captured before select")
        self._validate_available(next_batch_sizes)
        return decision_diagnostics(self._engine, next_batch_sizes)

    def statistics(self) -> dict[str, dict[str, Any]]:
        if self.is_ucb:
            return self._engine.snapshot_statistics()
        result = {}
        for arm in self.arms:
            values = asdict(self._engine.statistics(arm.arm_id))
            values["successful_candidates"] = values.pop("recovered")
            values["recovered_targets"] = self._target_counts[arm.arm_id]
            result[arm.arm_id] = values
        return result

    def _configuration(self) -> dict[str, Any]:
        result = {
            "scheduler_type": self.scheduler_type.value,
            "arms": [asdict(arm) for arm in self.arms],
            "total_candidate_budget": self.total_candidate_budget,
            "total_time_budget": self.total_time_budget,
        }

        if self.is_ucb:
            result.update(initial_targets=self.initial_targets, ucb_config=asdict(self.ucb_config))
        return result

    def snapshot(self) -> dict[str, Any]:
        if self._pending is not None:
            raise ValueError("snapshot requires a completed batch boundary")
        return deepcopy({
            "version": 1, "configuration": self._configuration(),
            "engine": self._engine.snapshot(), "target_counts": self._target_counts,
            "recovered_target_ids": sorted(self._recovered_ids),
        })

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        if self._pending is not None:
            raise ValueError("cannot restore over a pending decision")
        if snapshot.get("version") != 1 or snapshot.get("configuration") != self._configuration():
            raise ValueError("incompatible decision policy snapshot")
        # Build separately so malformed input cannot partially modify this policy.
        fresh = BaselinePolicy(
            self.scheduler_type, self.arms,
            total_candidate_budget=self.total_candidate_budget,
            total_time_budget=self.total_time_budget,
            initial_targets=self.initial_targets, ucb_config=self.ucb_config,
        )
        engine = deepcopy(snapshot["engine"])
        if self.is_ucb:
            fresh._engine.restore(engine)
            counts = dict(snapshot["target_counts"])
            if counts != {key: values["recovered"] for key, values in fresh.statistics().items()}:
                raise ValueError("UCB target counts differ from engine")
            for value in counts.values():
                require_count(value, "recovered_targets")
            ids = snapshot["recovered_target_ids"]
            if (not isinstance(ids, list) or any(not isinstance(item, str) for item in ids)
                    or len(set(ids)) != len(ids) or len(ids) > sum(counts.values())):
                raise ValueError("invalid recovered target IDs")
            self._engine, self._target_counts, self._recovered_ids = fresh._engine, counts, set(ids)
            return
        expected_keys = set(self._by_id)
        if self.scheduler_type == SchedulerType.ROUND_ROBIN:
            expected_keys.add("__round_robin__")
        if set(engine) != expected_keys:
            raise ValueError("snapshot arm set differs")
        counts = dict(snapshot["target_counts"])
        if set(counts) != set(self._by_id):
            raise ValueError("snapshot target count arm set differs")
        for arm in self.arms:
            values = engine[arm.arm_id]
            for key in ("tested", "recovered", "pulls", "allocated_candidates"):
                require_count(values[key], key)
            require_count(counts[arm.arm_id], "recovered_targets")
            require_seconds(values["time_cost"], "time_cost")
            require_seconds(values["recent_gain"], "recent_gain")
            if not values["recovered"] <= values["tested"] <= values["allocated_candidates"] <= arm.candidate_budget:
                raise ValueError("invalid snapshot candidate statistics")
            if counts[arm.arm_id] < values["recovered"]:
                raise ValueError("snapshot target count is below successful candidate count")
            if _exceeds_time(values["time_cost"], arm.time_budget) or values["recent_gain"] > 1:
                raise ValueError("invalid snapshot cost/gain statistics")
        if sum(engine[a.arm_id]["allocated_candidates"] for a in self.arms) > self.total_candidate_budget:
            raise ValueError("snapshot exceeds global candidate budget")
        if _exceeds_time(sum(engine[a.arm_id]["time_cost"] for a in self.arms), self.total_time_budget):
            raise ValueError("snapshot exceeds global time budget")
        ids = snapshot["recovered_target_ids"]
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
            raise ValueError("invalid recovered target IDs")
        if len(ids) > sum(counts.values()):
            raise ValueError("snapshot recovered IDs exceed target counts")
        fresh._engine.restore(engine)
        self._engine = fresh._engine
        self._target_counts = counts
        self._recovered_ids = set(ids)


def _exceeds_time(value: float, limit: float) -> bool:
    return value > limit and not math.isclose(value, limit, rel_tol=1e-12, abs_tol=0.0)
