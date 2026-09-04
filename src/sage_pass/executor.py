from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .enums import TaskStatus
from .errors import AppError
from .models import StrategyRunModel
from .repository import StrategyRunRepository
from .schemas import (
    ExecutionStarted,
    RunResult,
    RunStatus,
    StrategyPlan,
    StrategyResult,
    TaskDetail,
)
from .service import now_iso, public_id


# Mock 执行按计划候选量推进，不再让所有任务使用同一个固定时长。
# 最小交互窗口确保很小的任务启动后仍有机会取消。
MOCK_CANDIDATES_PER_SECOND = 5_000.0
MOCK_MIN_INTERACTION_SECONDS = 5.0


class MockExecutor:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(self, task: TaskDetail, plan: StrategyPlan) -> ExecutionStarted:
        if not plan.strategies:
            raise AppError(
                "EXECUTION_FAILED",
                "策略计划为空",
                status_code=422,
                details={"task_id": task.task_id},
            )
        run_id = public_id("R")
        started_at = now_iso()
        items = [
            StrategyRunModel(
                run_id=run_id,
                task_id=task.task_id,
                strategy_id=strategy.strategy_id.value,
                strategy_name=strategy.strategy_name,
                priority=strategy.priority,
                time_budget=strategy.time_budget,
                candidate_budget=strategy.candidate_budget,
                parameters=strategy.parameters,
                status=TaskStatus.RUNNING.value,
                tested=0,
                recovered=0,
                started_at=started_at,
            )
            for strategy in plan.strategies
        ]
        StrategyRunRepository(self.session).add_many(items)
        return ExecutionStarted(
            task_id=task.task_id,
            run_id=run_id,
            status=TaskStatus.RUNNING,
            started_at=started_at,
        )

    def status(self, run_id: str) -> RunStatus:
        items = _require_run(self.session, run_id)
        _refresh_run_progress(self.session, items)
        progress = _run_progress(items)
        current = _current_strategy(items, progress)
        return RunStatus(
            task_id=items[0].task_id,
            run_id=run_id,
            status=TaskStatus(items[0].status),
            progress=progress,
            current_strategy=current,
            elapsed_time=_elapsed_seconds(items[0].started_at),
            tested=sum(item.tested for item in items),
            recovered=sum(item.recovered for item in items),
            message=_status_message(items, current),
        )

    def result(self, run_id: str) -> RunResult:
        items = _require_run(self.session, run_id)
        _complete_run(self.session, items)
        total_time = max(_elapsed_seconds(items[0].started_at), _run_duration(items))
        return RunResult(
            task_id=items[0].task_id,
            run_id=run_id,
            status=TaskStatus.COMPLETED,
            total_time=total_time,
            total_tested=sum(item.tested for item in items),
            total_recovered=sum(item.recovered for item in items),
            strategy_results=[
                StrategyResult(
                    strategy_id=item.strategy_id,
                    time=_strategy_time(total_time, item, items),
                    tested=item.tested,
                    recovered=item.recovered,
                    success_rate=(
                        item.recovered / item.tested if item.tested > 0 else 0.0
                    ),
                )
                for item in items
            ],
            finished_at=items[0].finished_at or now_iso(),
        )


def _require_run(session: Session, run_id: str) -> list[StrategyRunModel]:
    items = StrategyRunRepository(session).list_by_run_id(run_id)
    if not items:
        raise AppError(
            "EXECUTION_FAILED",
            "执行不存在",
            status_code=404,
            details={"run_id": run_id},
        )
    return items


def _refresh_run_progress(session: Session, items: list[StrategyRunModel]) -> None:
    if TaskStatus(items[0].status) == TaskStatus.COMPLETED:
        return
    progress = _run_progress(items)
    for item in items:
        item.tested = int(item.candidate_budget * progress)
        item.recovered = _mock_recovered(item, progress)
    if progress >= 1.0:
        _complete_run(session, items)
    else:
        StrategyRunRepository(session).save_all(items)


def _complete_run(session: Session, items: list[StrategyRunModel]) -> None:
    finished_at = now_iso()
    for item in items:
        item.status = TaskStatus.COMPLETED.value
        item.tested = item.candidate_budget
        item.recovered = _mock_recovered(item, 1.0)
        item.finished_at = item.finished_at or finished_at
    StrategyRunRepository(session).save_all(items)


def _run_progress(items: list[StrategyRunModel]) -> float:
    if TaskStatus(items[0].status) == TaskStatus.COMPLETED:
        return 1.0
    return min(1.0, _elapsed_seconds(items[0].started_at) / _run_duration(items))


def _run_duration(items: list[StrategyRunModel]) -> float:
    candidate_count = sum(item.candidate_budget for item in items)
    return max(
        MOCK_MIN_INTERACTION_SECONDS,
        candidate_count / MOCK_CANDIDATES_PER_SECOND,
    )


def _elapsed_seconds(started_at: str | None) -> float:
    if started_at is None:
        return 0.0
    started = datetime.fromisoformat(started_at)
    return max(0.0, (datetime.fromisoformat(now_iso()) - started).total_seconds())


def _current_strategy(items: list[StrategyRunModel], progress: float) -> str | None:
    if not items or progress >= 1.0:
        return None
    index = min(int(progress * len(items)), len(items) - 1)
    return items[index].strategy_id


def _status_message(items: list[StrategyRunModel], current: str | None) -> str:
    if TaskStatus(items[0].status) == TaskStatus.COMPLETED:
        return "模拟执行已完成"
    if current == "S4":
        return "正在执行上下文策略"
    return "正在执行模拟策略"


def _mock_recovered(item: StrategyRunModel, progress: float) -> int:
    if progress < 1.0:
        return 0
    return 2 if item.strategy_id == "S4" else 1


def _strategy_time(
    total_time: float, item: StrategyRunModel, items: list[StrategyRunModel]
) -> float:
    total_budget = sum(entry.time_budget for entry in items)
    if total_budget <= 0:
        return total_time / len(items)
    return total_time * (item.time_budget / total_budget)
