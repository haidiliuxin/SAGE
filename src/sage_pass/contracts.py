from __future__ import annotations

from typing import Protocol

from .schemas import (
    ExecutionStarted,
    PRIR,
    RunResult,
    RunStatus,
    StrategyPlan,
    TaskDetail,
)


class Analyzer(Protocol):
    """乙负责实现：原始任务 -> PRIR。""" 

    def analyze(self, task: TaskDetail) -> PRIR: ...


class Planner(Protocol):
    """乙/后续规划模块实现：PRIR -> StrategyPlan。"""

    def plan(self, prir: PRIR) -> StrategyPlan: ...


class Executor(Protocol):
    """乙负责第一周 Mock 实现；后续可替换为真实适配器。"""

    def start(self, task: TaskDetail, plan: StrategyPlan) -> ExecutionStarted: ...

    def status(self, run_id: str) -> RunStatus: ...

    def result(self, run_id: str) -> RunResult: ...
