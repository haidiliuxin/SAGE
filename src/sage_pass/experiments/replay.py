"""Finite, ordered candidate replay with target-level recovery accounting.

Only counts and execution feedback reach the policy. Match tables remain in
the environment. Candidate tokens should be synthetic opaque IDs.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from ..decision import BaselinePolicy, DecisionArm, DecisionFeedback
from ..decision.types import require_count, require_seconds
from ..enums import SchedulerType, TaskStatus


@dataclass(frozen=True, slots=True)
class ReplayCost:
    """Supplied simulation parameters, not measured calibration results."""

    startup_cost: float = 0.0
    seconds_per_candidate: float = 0.01

    def __post_init__(self) -> None:
        require_seconds(self.startup_cost, "startup_cost")
        require_seconds(self.seconds_per_candidate, "seconds_per_candidate", positive=True)


@dataclass(frozen=True, slots=True)
class ReplayScenario:
    arms: tuple[DecisionArm, ...]
    candidate_streams: Mapping[str, tuple[str, ...]]
    target_ids: tuple[str, ...]
    matches: Mapping[str, tuple[str, ...]]
    total_candidate_budget: int
    total_time_budget: float
    batch_size: int = 100
    seed: int = 0
    cost: ReplayCost = field(default_factory=ReplayCost)
    arm_costs: Mapping[str, ReplayCost] = field(default_factory=dict)
    time_jitter: float = 0.0

    def __post_init__(self) -> None:
        require_count(self.total_candidate_budget, "total_candidate_budget", positive=True)
        require_seconds(self.total_time_budget, "total_time_budget", positive=True)
        require_count(self.batch_size, "batch_size", positive=True)
        require_count(self.seed, "seed")
        require_seconds(self.time_jitter, "time_jitter")
        if self.time_jitter >= 1:
            raise ValueError("time_jitter must be less than one")
        arm_ids = {arm.arm_id for arm in self.arms}
        if not arm_ids or len(arm_ids) != len(self.arms):
            raise ValueError("non-empty unique arms are required")
        if len({arm.target_group_id for arm in self.arms}) != 1:
            raise ValueError("MVP supports one target group per replay")
        if set(self.candidate_streams) != arm_ids or set(self.arm_costs) - arm_ids:
            raise ValueError("candidate streams/costs must reference the configured arms")
        if not self.target_ids or len(set(self.target_ids)) != len(self.target_ids):
            raise ValueError("non-empty distinct target IDs are required")
        if any(not isinstance(item, str) or not item for item in self.target_ids):
            raise ValueError("target IDs must be non-empty strings")
        universe = set()
        for stream in self.candidate_streams.values():
            if isinstance(stream, str):
                raise ValueError("a candidate stream must be a sequence of IDs")
            for token in stream:
                if not isinstance(token, str) or not token or '\n' in token or '\r' in token:
                    raise ValueError("candidate tokens must be non-empty single-line strings")
                universe.add(token)
        for token, targets in self.matches.items():
            if token not in universe or isinstance(targets, str):
                raise ValueError("match table must reference candidate tokens and target lists")
            if len(set(targets)) != len(targets) or set(targets) - set(self.target_ids):
                raise ValueError("match table contains duplicate or unknown target IDs")
        # Own the mappings so changes to caller-owned dictionaries cannot alter
        # an active experiment or invalidate its fingerprint.
        object.__setattr__(self, "arms", tuple(self.arms))
        object.__setattr__(self, "target_ids", tuple(self.target_ids))
        object.__setattr__(self, "candidate_streams", {key: tuple(value) for key, value in self.candidate_streams.items()})
        object.__setattr__(self, "matches", {key: tuple(value) for key, value in self.matches.items()})
        object.__setattr__(self, "arm_costs", dict(self.arm_costs))

    def as_dict(self) -> dict[str, Any]:
        return {
            "arms": [asdict(arm) for arm in self.arms],
            "candidate_streams": {key: list(value) for key, value in self.candidate_streams.items()},
            "target_ids": list(self.target_ids),
            "matches": {key: list(value) for key, value in self.matches.items()},
            "total_candidate_budget": self.total_candidate_budget,
            "total_time_budget": self.total_time_budget,
            "batch_size": self.batch_size, "seed": self.seed,
            "cost": asdict(self.cost),
            "arm_costs": {key: asdict(value) for key, value in self.arm_costs.items()},
            "time_jitter": self.time_jitter,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ReplayScenario:
        data = deepcopy(dict(value))
        data["arms"] = tuple(DecisionArm(**item) for item in data["arms"])
        data["target_ids"] = tuple(data["target_ids"])
        data["cost"] = ReplayCost(**data.get("cost", {}))
        data["arm_costs"] = {key: ReplayCost(**item) for key, item in data.get("arm_costs", {}).items()}
        return cls(**data)


class ReplayEnvironment:
    def __init__(self, scenario: ReplayScenario, scheduler_type: SchedulerType | str) -> None:
        # Copy once more to isolate the run from mutation of a scenario mapping.
        self.scenario = ReplayScenario.from_dict(scenario.as_dict())
        self.scheduler_type = SchedulerType(scheduler_type)
        self.policy = BaselinePolicy(
            self.scheduler_type, self.scenario.arms,
            total_candidate_budget=self.scenario.total_candidate_budget,
            total_time_budget=self.scenario.total_time_budget,
        )
        self._rng = random.Random(self.scenario.seed)
        self._cursors = {arm.arm_id: 0 for arm in self.scenario.arms}
        self._submitted: set[str] = set()
        self._recovered: set[str] = set()
        self._batch_counts = {arm.arm_id: 0 for arm in self.scenario.arms}
        self._trace: list[dict[str, Any]] = []
        self._fingerprint = hashlib.sha256(json.dumps(
            self.scenario.as_dict(), sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()

    def _peek(self, arm_id: str, limit: int) -> tuple[list[str], int, int]:
        """Return tokens, end cursor and duplicate count without consuming.

        Only the selected prefix advances its cursor. A shortened batch never
        discards the remainder of an input stream.
        """
        stream = self.scenario.candidate_streams[arm_id]
        cursor = self._cursors[arm_id]
        result: list[str] = []
        local_seen: set[str] = set()
        duplicates = 0
        while cursor < len(stream) and len(result) < limit:
            token = stream[cursor]
            cursor += 1
            if token in self._submitted or token in local_seen:
                duplicates += 1
                continue
            local_seen.add(token)
            result.append(token)
        return result, cursor, duplicates

    def available_batches(self) -> dict[str, int]:
        return {
            arm.arm_id: len(self._peek(arm.arm_id, self.scenario.batch_size)[0])
            for arm in self.scenario.arms
        }

    def stop_reason(self) -> str | None:
        if len(self._recovered) == len(self.scenario.target_ids):
            return "all_targets_recovered"
        return self.policy.stop_reason(self.available_batches())

    @property
    def trace(self) -> list[dict[str, Any]]:
        return deepcopy(self._trace)

    def step(self) -> dict[str, Any] | None:
        if self.stop_reason() is not None:
            return None
        available = self.available_batches()
        prior = self.policy.statistics()
        decision = self.policy.select(available)
        if decision is None:
            raise RuntimeError("policy returned no decision without a stop reason")
        tokens, cursor, duplicates = self._peek(decision.arm_id, decision.candidate_limit)
        cost = self.scenario.arm_costs.get(decision.arm_id, self.scenario.cost)
        jitter = self.scenario.time_jitter
        multiplier = self._rng.uniform(1 - jitter, 1 + jitter) if jitter else 1.0
        startup = Decimal(str(cost.startup_cost))
        per_candidate = Decimal(str(cost.seconds_per_candidate)) * Decimal(str(multiplier))
        allowed_time = Decimal(str(decision.time_limit))
        full_duration = startup + len(tokens) * per_candidate
        if allowed_time >= full_duration:
            tested = len(tokens)
        elif allowed_time <= startup:
            tested = 0
        else:
            tested = int((allowed_time - startup) // per_candidate)
        # All submitted candidates consume budget, including a timed-out suffix.
        # This matches the current production submission-budget convention.
        duration = min(allowed_time, full_duration)
        newly_recovered: set[str] = set()
        hits = 0
        for token in tokens[:tested]:
            new = set(self.scenario.matches.get(token, ())) - self._recovered - newly_recovered
            if new:
                hits += 1
                newly_recovered.update(new)
        batch_index = self._batch_counts[decision.arm_id] + 1
        outcome = DecisionFeedback(
            run_id=f"replay-{self._fingerprint[:12]}-{self.scheduler_type.value}",
            arm_id=decision.arm_id, strategy_id=decision.strategy_id,
            batch_index=batch_index, candidate_count=len(tokens), tested=tested,
            recovered=len(newly_recovered), duration=float(duration),
            status=TaskStatus.COMPLETED,
            message="simulated timeout" if tested < len(tokens) else "",
            successful_candidates=hits,
            recovered_target_ids=tuple(sorted(newly_recovered)),
            duplicate_count=duplicates, offered_count=len(tokens) + duplicates,
        )
        self.policy.observe_outcome(decision.arm_id, outcome)
        self._cursors[decision.arm_id] = cursor
        self._batch_counts[decision.arm_id] = batch_index
        self._submitted.update(tokens)
        self._recovered.update(newly_recovered)
        event = {
            "round_index": len(self._trace) + 1,
            "available_batches": available,
            "prior_state": prior,
            "decision": asdict(decision),
            "outcome": outcome.as_dict(),
            "updated_state": self.policy.statistics(),
            "remaining_candidates": self.scenario.total_candidate_budget - len(self._submitted),
            "remaining_time": max(0.0, self.scenario.total_time_budget - self._time_cost()),
            "stop_reason": self.stop_reason(),
        }
        self._trace.append(event)
        return deepcopy(event)

    def _time_cost(self) -> float:
        return sum(values["time_cost"] for values in self.policy.statistics().values())

    def run(self, max_steps: int | None = None) -> dict[str, Any]:
        if max_steps is not None:
            require_count(max_steps, "max_steps")
        steps = 0
        while max_steps is None or steps < max_steps:
            if self.step() is None:
                break
            steps += 1
        return self.report()

    def report(self) -> dict[str, Any]:
        stats = self.policy.statistics()
        return {
            "scenario_fingerprint": self._fingerprint,
            "scheduler_type": self.scheduler_type.value,
            "seed": self.scenario.seed,
            "rounds": len(self._trace),
            "submitted_candidates": len(self._submitted),
            "tested_candidates": sum(item["tested"] for item in stats.values()),
            "recovered_targets": len(self._recovered),
            "target_count": len(self.scenario.target_ids),
            "recovery_rate": len(self._recovered) / len(self.scenario.target_ids),
            "simulated_seconds": self._time_cost(),
            "stop_reason": self.stop_reason(),
            "arm_statistics": stats,
            "trace": self.trace,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1, "scenario_fingerprint": self._fingerprint,
            "scheduler_type": self.scheduler_type.value,
            "rounds": len(self._trace), "policy": self.policy.snapshot(),
            "cursors": dict(self._cursors),
            "submitted_ids": sorted(self._submitted),
            "recovered_target_ids": sorted(self._recovered),
            "batch_counts": dict(self._batch_counts), "trace": self.trace,
        }

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        """Validate and restore a JSON checkpoint by deterministic prefix replay.

        This deliberately trades O(prefix length) restore cost for a simple,
        validated MVP: RNG state, cursors, policy state and trace must all match.
        No real verifier is run. Direct constant-time restoration is future work.
        """
        if snapshot.get("version") != 1 or snapshot.get("scenario_fingerprint") != self._fingerprint:
            raise ValueError("incompatible replay snapshot/scenario")
        if snapshot.get("scheduler_type") != self.scheduler_type.value:
            raise ValueError("snapshot policy differs")
        rounds = snapshot.get("rounds")
        require_count(rounds, "rounds")
        if rounds > self.scenario.total_candidate_budget:
            raise ValueError("snapshot rounds exceed finite candidate budget")
        fresh = ReplayEnvironment(self.scenario, self.scheduler_type)
        fresh.run(max_steps=rounds)
        if fresh.snapshot() != dict(snapshot):
            raise ValueError("snapshot contents do not match deterministic replay")
        # Exercise the normative policy restore, including the RR cursor.
        fresh.policy.restore(snapshot["policy"])
        self.__dict__.update(fresh.__dict__)


def compare_baselines(scenario: ReplayScenario) -> dict[str, Any]:
    """Identical inputs, budgets and seed; each policy owns an independent run."""
    return {
        name: ReplayEnvironment(scenario, name).run()
        for name in ("fixed", "round_robin", "heuristic_bandit")
    }
