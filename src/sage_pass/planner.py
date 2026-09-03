from __future__ import annotations

from .enums import PlannerType, StrategyId, TaskStatus
from .schemas import PRIR, StrategyItem, StrategyPlan


class MockPlanner:
    def plan(self, prir: PRIR) -> StrategyPlan:
        strategies = [_baseline_strategy(prir)]
        if prir.context_available:
            strategies.append(_context_strategy(prir))
        return StrategyPlan(
            task_id=prir.task_id,
            planner_type=PlannerType.MOCK,
            total_time_budget=prir.time_budget,
            strategies=strategies,
            status=TaskStatus.PLANNED,
            warnings=[],
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


def _context_strategy(prir: PRIR) -> StrategyItem:
    return StrategyItem(
        strategy_id=StrategyId.S4,
        strategy_name="Context",
        priority=2,
        time_budget=max(1, (prir.time_budget * 2) // 5),
        candidate_budget=max(1, (prir.candidate_budget * 2) // 5),
        reason="任务提供了上下文信息",
        parameters={"use_years": True, "use_keywords": True},
    )
