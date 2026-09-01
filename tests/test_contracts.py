import pytest
from pydantic import ValidationError

from sage_pass.schemas import StrategyPlan


def test_strategy_plan_rejects_time_over_budget():
    with pytest.raises(ValidationError):
        StrategyPlan.model_validate(
            {
                "task_id": "T001",
                "planner_type": "mock",
                "total_time_budget": 10,
                "strategies": [
                    {
                        "strategy_id": "S1",
                        "strategy_name": "Baseline",
                        "priority": 1,
                        "time_budget": 11,
                        "candidate_budget": 100,
                        "reason": "测试预算约束",
                        "parameters": {},
                    }
                ],
                "status": "planned",
                "warnings": [],
            }
        )
