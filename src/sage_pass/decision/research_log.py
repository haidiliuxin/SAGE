"""Durable research events with explicit projection of non-sensitive fields.

SQLite is the authoritative append-only store; JSONL is a streaming export.
Each attempt records a start, selected batches, feedback, and termination.
An interrupted attempt is retained when a service restarts.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..interfaces import BatchOutcome
from .rewards import REWARD_VERSION, RewardContext, RewardWeights, calculate_reward
from .types import require_count, require_seconds
from .calibration import VerificationProfile

LOG_VERSION = 1
POLICY_VERSION = "sage-baselines-v1"
_STATS = frozenset({
    "tested", "recovered", "successful_candidates", "recovered_targets",
    "time_cost", "recent_gain", "pulls", "allocated_candidates",
    "learning_reward_sum", "cost_samples", "censored_samples",
    "estimated_startup", "estimated_seconds_per_candidate",
    "cost_confidence", "recent_throughput", "last_batch_throughput",
})
_SCORES = frozenset({"score", "success_probability", "recent_gain", "transfer", "cost",
                     "mean_reward", "exploration_bonus", "upper_bound", "predicted_seconds",
                     "cost_samples", "censored_samples", "untried", "cost_confidence",
                     "recent_throughput", "last_batch_throughput"})
_REASONS = frozenset({
    "candidate_budget", "time_budget", "candidates_exhausted", "strategy_budgets",
    "all_targets_recovered", "completed", "failed", "cancelled", "shutdown",
    "launch_failed", "execution_failed", "internal_error", "logging_failed",
    "checkpoint", "initial_selection", "exploration", "policy_selection",
    "same_arm", "arm_unavailable", "outcome_unknown",
})


def _reason(value: str | None) -> str | None:
    if value is not None and value not in _REASONS:
        raise ValueError("research reason must be a fixed reason code")
    return value


def _numbers(values: Mapping[str, Any], allowed: frozenset[str]) -> dict[str, int | float | None]:
    result = {}
    for key in sorted(allowed.intersection(values)):
        value = values[key]
        if key in {"recent_throughput", "last_batch_throughput"} and value is None:
            result[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("research statistics must contain finite numeric values")
        result[key] = value
    return result


def _state(values: Mapping[str, Mapping[str, Any]], context: RewardContext) -> dict:
    arms = {key: _numbers(stats, _STATS) for key, stats in values.items()}
    used_candidates = sum(stats.get("allocated_candidates", 0) for stats in arms.values())
    used_time = sum(stats.get("time_cost", 0) for stats in arms.values())
    return {
        "arms": arms,
        "remaining_candidates": context.candidate_budget - used_candidates,
        "remaining_time": context.time_budget - used_time,
    }


class SqliteResearchLog:
    """One short transaction per event, safe across threads and store instances.

    Callers should write events via ResearchRecorder, which performs the privacy
    projection. Never pass raw Task, CandidateRecord or executor snapshots here.
    """

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path).resolve()
        self.read_only = read_only
        if read_only:
            with closing(self._connect()) as connection:
                connection.execute("SELECT sequence, payload FROM decision_research_events LIMIT 0")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS decision_research_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    round_index INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE(run_id, attempt_id, round_index, event_type)
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS decision_research_by_run
                ON decision_research_events(run_id, sequence)
            """)

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            return sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _append(self, event: Mapping[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False)
        identity = (event["run_id"], event["attempt_id"], event["round_index"], event["event_type"])
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO decision_research_events(run_id, attempt_id, round_index, event_type, payload) VALUES (?,?,?,?,?)",
                (*identity, payload),
            )
            existing = connection.execute(
                "SELECT payload FROM decision_research_events WHERE run_id=? AND attempt_id=? AND round_index=? AND event_type=?",
                identity,
            ).fetchone()
            if existing[0] != payload:
                raise ValueError("conflicting research event; refusing to overwrite history")

    def events(self, run_id: str | None = None, *, after_sequence: int = 0) -> Iterator[dict]:
        require_count(after_sequence, "after_sequence")
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "SELECT sequence, payload FROM decision_research_events "
                "WHERE sequence > ? AND (? IS NULL OR run_id=?) ORDER BY sequence",
                (after_sequence, run_id, run_id),
            )
            for sequence, payload in cursor:
                event = json.loads(payload)
                if event.get("schema_version") != LOG_VERSION:
                    raise ValueError("unsupported research log version")
                yield {"sequence": sequence, **event}

    def export_jsonl(self, path: str | Path, *, run_id: str | None = None) -> int:
        destination = Path(path).resolve()
        if destination == self.path:
            raise ValueError("export destination must differ from the research database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        # Exclusive creation avoids silently replacing another experiment.
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            for event in self.events(run_id):
                stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
                count += 1
        return count


class ResearchRecorder:
    """One active batch at a time; bounded memory independent of run length."""

    def __init__(
        self, *, run_id: str, policy_type: str, context: RewardContext,
        weights: RewardWeights | None = None, store: SqliteResearchLog | None = None,
        mode: str = "replay", attempt_id: str | None = None,
        seed: int | None = None, resume_from_round: int = 0,
        previous_arm_id: str | None = None, policy_parameters: Mapping[str, float] | None = None,
        verification_profile: VerificationProfile | None = None,
    ) -> None:
        if mode not in {"replay", "real"}:
            raise ValueError("unsupported research mode")
        if policy_type not in {"fixed", "round_robin", "heuristic_bandit", "ucb", "cost_aware_ucb", "thompson"}:
            raise ValueError("unsupported policy type")
        require_count(resume_from_round, "resume_from_round")
        if seed is not None:
            require_count(seed, "seed")
        self.run_id = run_id
        self.policy_type = policy_type
        self.context = context
        self.weights = weights or RewardWeights()
        self.store = store
        self.attempt_id = attempt_id or uuid4().hex
        self.mode = mode
        self.round_index = resume_from_round
        self.previous_arm_id = previous_arm_id
        self._pending: dict | None = None
        self._finished = False
        self._write("run_started", {
            "mode": mode, "policy_type": policy_type,
            "policy_version": "sage-ucb-v2" if policy_type in {"ucb", "cost_aware_ucb"} else POLICY_VERSION,
            "policy_parameters": _numbers(policy_parameters or {}, frozenset({
                "exploration_rounds", "success_probability", "recent_gain", "transfer", "cost",
                "exploration_coefficient", "startup_prior", "seconds_per_candidate_prior", "minimum_cost",
            })),
            "reward_version": REWARD_VERSION,
            "learning_reward": "new_targets_over_initial_targets" if policy_type in {"ucb", "cost_aware_ucb"} else "legacy",
            "reward_weights": asdict(self.weights), "reward_context": asdict(context),
            "seed": seed, "resume_from_round": resume_from_round,
            "verification_profile": VerificationProfile.from_dict(verification_profile.as_dict()).as_dict() if verification_profile else None,
            "previous_arm_id": previous_arm_id,
            "decision_probability": "deterministic" if policy_type != "thompson" else "not_recorded",
        })

    def _write(self, kind: str, payload: dict) -> None:
        if self.store is not None:
            self.store._append({
                "schema_version": LOG_VERSION, "run_id": self.run_id,
                "attempt_id": self.attempt_id, "round_index": self.round_index,
                "event_type": kind, "payload": payload,
            })

    @property
    def pending(self) -> bool:
        return self._pending is not None

    def begin(
        self, *, decision: Any, available: Mapping[str, int],
        scores: Mapping[str, Any], prior_state: Mapping[str, Mapping[str, Any]],
    ) -> dict:
        if self._pending is not None or self._finished:
            raise ValueError("recorder requires an unfinished run with no pending batch")
        available = dict(available)
        for count in available.values():
            require_count(count, "available batch size")
        arm_id = getattr(decision, "arm_id", decision.strategy_id)
        require_count(decision.candidate_limit, "candidate_limit", positive=True)
        require_seconds(decision.time_limit, "time_limit", positive=True)
        if available.get(arm_id, 0) < decision.candidate_limit:
            raise ValueError("selected batch exceeds available candidates")
        score_values = {}
        for key, value in scores.items():
            raw = asdict(value) if is_dataclass(value) else value
            if isinstance(raw, (int, float)):
                raw = {"score": raw}
            score_values[key] = _numbers(raw, _SCORES)
        if any(key not in score_values for key, count in available.items() if count > 0):
            raise ValueError("every available arm requires a score breakdown")
        reason = "same_arm"
        if self.previous_arm_id is None:
            reason = "initial_selection"
        elif self.previous_arm_id != arm_id:
            reason = "arm_unavailable" if not available.get(self.previous_arm_id, 0) else (
                "exploration" if decision.exploration else "policy_selection"
            )
        pending = {
            "available_arms": sorted(key for key, count in available.items() if count > 0),
            "available_batches": available,
            "prior_state": _state(prior_state, self.context), "scores": score_values,
            "decision": {
                "arm_id": arm_id, "strategy_id": decision.strategy_id,
                "candidate_limit": decision.candidate_limit, "time_limit": decision.time_limit,
                "exploration": bool(decision.exploration),
            },
            "selection_probability": 1.0 if self.policy_type != "thompson" else None,
            "selection_rule": {
                "fixed": "priority_order", "round_robin": "round_robin_cursor",
                "heuristic_bandit": "exploration_then_score",
            }.get(self.policy_type, self.policy_type),
            "scores_used_for_selection": self.policy_type not in {"fixed", "round_robin"},
            "previous_arm_id": self.previous_arm_id, "switch_reason": reason,
        }
        self.round_index += 1
        try:
            self._write("decision_started", pending)
        except Exception:
            self.round_index -= 1
            raise
        self._pending = pending
        return deepcopy(pending)

    def complete(
        self, outcome: BatchOutcome, *, updated_state: Mapping[str, Mapping[str, Any]],
        stop_reason: str | None = None,
    ) -> dict:
        if self._pending is None:
            raise ValueError("no decision is awaiting feedback")
        pending = self._pending
        if outcome.run_id != self.run_id:
            raise ValueError("feedback run differs from recorder run")
        if outcome.arm_id != pending["decision"]["arm_id"] or outcome.strategy_id != pending["decision"]["strategy_id"]:
            raise ValueError("feedback does not match the pending decision")
        if outcome.candidate_count > pending["decision"]["candidate_limit"]:
            raise ValueError("feedback exceeds selected candidate count")
        breakdown = calculate_reward(outcome, self.context, self.weights)
        status = getattr(outcome.status, "value", outcome.status)
        if status not in {"completed", "failed", "cancelled", "running", "paused"}:
            raise ValueError("unsupported feedback status")
        # No message, targets, recovered_target_ids, plaintext, candidate records,
        # stdout, stderr, free-form context, or raw policy snapshot is serialized.
        feedback = {
            "candidate_count": outcome.candidate_count, "tested": outcome.tested,
            "recovered": outcome.recovered, "duration": outcome.duration,
            "status": status, "batch_index": outcome.batch_index,
            "successful_candidates": getattr(outcome, "successful_candidates", None),
            "duplicate_count": getattr(outcome, "duplicate_count", None),
            "offered_count": getattr(outcome, "offered_count", None),
        }
        require_count(feedback["batch_index"], "batch_index", positive=True)
        for name in ("successful_candidates", "duplicate_count", "offered_count"):
            if feedback[name] is not None:
                require_count(feedback[name], name)
        if feedback["successful_candidates"] is not None and feedback["successful_candidates"] > outcome.tested:
            raise ValueError("successful candidates exceed tested candidates")
        event = {
            **pending, "feedback": feedback, "reward": breakdown.total,
            "reward_breakdown": breakdown.as_dict(),
            "reward_version": REWARD_VERSION, "reward_weights": asdict(self.weights),
            "updated_state": _state(updated_state, self.context),
            "stop_reason": _reason(stop_reason),
        }
        if self.policy_type in {"ucb", "cost_aware_ucb"}:
            event["learning_reward"] = breakdown.recovery_fraction
        self._write("decision_completed", event)
        self.previous_arm_id = outcome.arm_id
        self._pending = None
        return deepcopy(event)

    def finish(self, reason: str, *, state: Mapping[str, Mapping[str, Any]]) -> None:
        if self._finished:
            return
        _reason(reason)
        if self._pending is not None:
            self._write("decision_interrupted", {
                **self._pending, "stop_reason": reason,
                "feedback": None, "reward": None, "outcome_status": "outcome_unknown",
                "updated_state": _state(state, self.context),
            })
            self._pending = None
        self._write("run_finished", {"stop_reason": reason, "state": _state(state, self.context)})
        self._finished = True

    def checkpoint(self) -> None:
        if self._pending is not None:
            raise ValueError("cannot mark a checkpoint with a pending batch")
        self._write("checkpoint", {"last_committed_round": self.round_index})
