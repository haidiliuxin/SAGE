from __future__ import annotations

from .enums import PlannerType, StrategyId, TaskStatus
from .schemas import PRIR, StrategyItem, StrategyPlan


class MockPlanner:
    def plan(self, prir: PRIR) -> StrategyPlan:
        baseline = _baseline_strategy(prir)
        strategies = [baseline]
        warnings: list[str] = []
        if prir.context_available:
            remaining_time = prir.time_budget - baseline.time_budget
            remaining_candidates = prir.candidate_budget - baseline.candidate_budget
            if remaining_time >= 1 and remaining_candidates >= 1:
                strategies.append(
                    _context_strategy(
                        prir,
                        max_time_budget=remaining_time,
                        max_candidate_budget=remaining_candidates,
                    )
                )
            else:
                warnings.append("上下文策略因任务预算不足未加入计划")
        return StrategyPlan(
            task_id=prir.task_id,
            planner_type=PlannerType.MOCK,
            total_time_budget=prir.time_budget,
            strategies=strategies,
            status=TaskStatus.PLANNED,
            warnings=warnings,
        )


def _baseline_strategy(prir: PRIR) -> StrategyItem:
    return StrategyItem(
        strategy_id=StrategyId.S1,
        strategy_name="Baseline",
        priority=1,
        time_budget=max(1, prir.time_budget // 5),
        candidate_budget=max(1, prir.candidate_budget // 5),
        reason="优先测试高频口令",
        parameters={},
    )


def _context_strategy(
    prir: PRIR, *, max_time_budget: int, max_candidate_budget: int
) -> StrategyItem:
    return StrategyItem(
        strategy_id=StrategyId.S4,
        strategy_name="Context",
        priority=2,
        time_budget=min(max_time_budget, max(1, (prir.time_budget * 2) // 5)),
        candidate_budget=min(
            max_candidate_budget, max(1, (prir.candidate_budget * 2) // 5)
        ),
        reason="任务提供了上下文信息",
        parameters={"use_years": True, "use_keywords": True},
    )
