"""UCB1 index and a predicted-cost ratio variant.

Formula references: Auer et al. (2002), Figure 1; Tran-Thanh et al. (2012),
section 3.3 (fractional KUBE). SMPyBandits Policies.UCB was consulted for
index/cold-start conventions; no source was copied. See docs/decision/ucb-and-cost.md.
"""

import math
from copy import deepcopy
from dataclasses import asdict, dataclass, replace

from ..scheduler import ArmStatistics, ScoreBreakdown, _OrderedSchedulerBase, decision_diagnostics
from .costs import OnlineCostModel, UCBConfig
from .types import require_count, require_seconds


@dataclass(frozen=True, slots=True)
class UCBScore(ScoreBreakdown):
    mean_reward: float
    exploration_bonus: float
    upper_bound: float
    predicted_seconds: float
    cost_samples: int
    censored_samples: int
    untried: int
    cost_confidence: float
    recent_throughput: float | None
    last_batch_throughput: float | None


class UCBScheduler(_OrderedSchedulerBase):
    """Per-batch target-fraction reward; hard budgets use actual feedback."""

    def __init__(self, arms, *, total_candidate_budget, total_time_budget,
                 initial_targets, cost_aware=False, config=None):
        require_count(initial_targets, "initial_targets", positive=True)
        require_count(total_candidate_budget, "total_candidate_budget", positive=True)
        require_seconds(total_time_budget, "total_time_budget", positive=True)
        for arm in arms:
            require_count(arm.candidate_budget, "arm candidate budget")
            require_seconds(arm.time_budget, "arm time budget")
        super().__init__(arms, total_candidate_budget=total_candidate_budget,
                         total_time_budget=total_time_budget)
        self.initial_targets = initial_targets
        self.cost_aware = cost_aware
        self.config = config or UCBConfig()
        self.cost_models = {key: OnlineCostModel(self.config) for key in self._ordered_ids}

    def score(self, strategy_id, next_batch_size):
        require_count(next_batch_size, "next_batch_size", positive=True)
        stats = self._statistics[strategy_id]
        pulls = sum(item.pulls for item in self._statistics.values())
        mean = stats.recovered / self.initial_targets / stats.pulls if stats.pulls else 0.0
        bonus = self.config.exploration_coefficient * math.sqrt(math.log(max(1, pulls)) / stats.pulls) if stats.pulls else 0.0
        upper = mean + bonus
        model = self.cost_models[strategy_id]
        estimate = model.estimate(next_batch_size)
        predicted = estimate.estimated_batch_time
        index = upper / predicted if self.cost_aware else upper
        if not math.isfinite(index):
            raise ValueError("UCB score overflow")
        # Cold arms are selected explicitly, so logs never contain Infinity.
        return UCBScore(0.0, stats.recent_gain, 0.0, predicted, index,
                        mean, bonus, upper, predicted, model.samples,
                        model.censored_samples, int(stats.pulls == 0), estimate.confidence,
                        estimate.recent_throughput, estimate.last_batch_throughput)

    def select(self, next_batch_sizes):
        if set(next_batch_sizes) - set(self._ordered_ids):
            raise ValueError("unknown arm")
        for size in next_batch_sizes.values():
            require_count(size, "available size")
        info = decision_diagnostics(self, next_batch_sizes)
        eligible = [key for key in self._ordered_ids if info["available"][key] > 0]
        if not eligible:
            return None
        untried = [key for key in eligible if self._statistics[key].pulls == 0]
        selected = untried[0] if untried else max(eligible, key=lambda key: info["scores"][key].score)
        return replace(self._decision(selected, next_batch_sizes), exploration=bool(untried))

    def observe(self, strategy_id, *, candidate_count, tested, recovered, duration, complete=True, cost_tested_known=True):
        stats = self._statistics[strategy_id]
        for key, value in (("candidate_count", candidate_count), ("tested", tested), ("recovered", recovered)):
            require_count(value, key, positive=key == "candidate_count")
        require_seconds(duration, "duration")
        if tested > candidate_count or (recovered and not tested):
            raise ValueError("invalid candidate feedback")
        if sum(item.recovered for item in self._statistics.values()) + recovered > self.initial_targets:
            raise ValueError("recovered targets exceed initial target count")
        if (stats.allocated_candidates + candidate_count > self._arms[strategy_id].candidate_budget
                or self.total_allocated_candidates + candidate_count > self.total_candidate_budget):
            raise ValueError("feedback exceeds candidate budget")
        self.cost_models[strategy_id].observe(candidate_count, duration,
                                             complete=complete and tested == candidate_count,
                                             tested=tested if cost_tested_known else None)
        # This recovered field is TARGET count, not legacy Bernoulli successes.
        super().observe(strategy_id, candidate_count=candidate_count, tested=tested,
                        recovered=recovered, duration=duration)
        stats.recent_gain = recovered / self.initial_targets

    def observe_outcome(self, arm_id, outcome):
        if outcome.arm_id != arm_id:
            raise ValueError("feedback arm differs")
        self.observe(arm_id, candidate_count=outcome.candidate_count, tested=outcome.tested,
                     recovered=outcome.recovered, duration=outcome.duration,
                     complete=outcome.status == "completed")

    def snapshot_statistics(self):
        result = super().snapshot_statistics()
        for key, values in result.items():
            model = self.cost_models[key]
            estimate = model.estimate(model.recent[-1]["candidates"] if model.recent else 1)
            values.update(recovered_targets=values["recovered"],
                          learning_reward_sum=values["recovered"] / self.initial_targets,
                          cost_samples=self.cost_models[key].samples,
                          censored_samples=self.cost_models[key].censored_samples,
                          estimated_startup=estimate.startup_cost,
                          estimated_seconds_per_candidate=estimate.seconds_per_candidate,
                          cost_confidence=estimate.confidence,
                          recent_throughput=estimate.recent_throughput,
                          last_batch_throughput=estimate.last_batch_throughput)
        return result

    def configuration(self):
        return {"initial_targets": self.initial_targets, "cost_aware": self.cost_aware,
                "config": asdict(self.config), "total_candidate_budget": self.total_candidate_budget,
                "total_time_budget": self.total_time_budget,
                "arms": [asdict(self._arms[key]) for key in self._ordered_ids]}

    def snapshot(self):
        return {"version": 1, "configuration": self.configuration(),
                "statistics": {key: asdict(value) for key, value in self._statistics.items()},
                "cost_models": {key: model.snapshot() for key, model in self.cost_models.items()}}

    def restore_statistics(self, snapshot):
        raise ValueError("UCB requires the complete scheduler snapshot, including cost models")

    def restore(self, snapshot):
        if snapshot.get("version") != 1 or snapshot.get("configuration") != self.configuration():
            raise ValueError("incompatible UCB snapshot")
        raw = deepcopy(snapshot)
        if set(raw["statistics"]) != set(self._ordered_ids) or set(raw["cost_models"]) != set(self._ordered_ids):
            raise ValueError("UCB snapshot arm set differs")
        statistics, costs = {}, {}
        for key, values in raw["statistics"].items():
            stats = ArmStatistics(**values)
            for name in ("tested", "recovered", "pulls", "allocated_candidates"):
                require_count(getattr(stats, name), name)
            require_seconds(stats.time_cost, "time_cost")
            require_seconds(stats.recent_gain, "recent_gain")
            if not stats.pulls <= stats.allocated_candidates <= self._arms[key].candidate_budget or stats.tested > stats.allocated_candidates:
                raise ValueError("invalid UCB candidate statistics")
            if stats.recent_gain > 1 or (not stats.tested and stats.recovered):
                raise ValueError("invalid UCB reward statistics")
            if not stats.pulls and any((stats.tested, stats.recovered, stats.allocated_candidates, stats.time_cost, stats.recent_gain)):
                raise ValueError("untried UCB arm has feedback")
            model = OnlineCostModel.from_snapshot(self.config, raw["cost_models"][key])
            if model.samples + model.censored_samples != stats.pulls:
                raise ValueError("cost sample count differs from pull count")
            if model.sum_x > stats.tested or model.sum_y > stats.time_cost + 1e-10:
                raise ValueError("cost observations exceed actual resources")
            statistics[key], costs[key] = stats, model
        if sum(s.recovered for s in statistics.values()) > self.initial_targets or sum(s.allocated_candidates for s in statistics.values()) > self.total_candidate_budget:
            raise ValueError("UCB snapshot exceeds global counts")
        self._statistics, self.cost_models = statistics, costs
