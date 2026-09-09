from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    FeedbackRunModel,
    FileModel,
    PatternKnowledgeModel,
    PatternTaskObservationModel,
    PRIRModel,
    RunRecordModel,
    StrategyRunModel,
    TaskModel,
)


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, task: TaskModel) -> TaskModel:
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def get(self, task_id: str) -> TaskModel | None:
        return self.session.scalar(select(TaskModel).where(TaskModel.task_id == task_id))

    def list(self, *, limit: int, offset: int) -> tuple[list[TaskModel], int]:
        items = list(
            self.session.scalars(
                select(TaskModel)
                .order_by(TaskModel.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        total = self.session.scalar(select(func.count()).select_from(TaskModel)) or 0
        return items, total

    def save(self, task: TaskModel) -> TaskModel:
        self.session.commit()
        self.session.refresh(task)
        return task


class FileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, item: FileModel) -> FileModel:
        self.session.add(item)
        self.session.commit()
        self.session.refresh(item)
        return item

    def get(self, file_id: str) -> FileModel | None:
        return self.session.scalar(select(FileModel).where(FileModel.file_id == file_id))


class PRIRRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, task_id: str) -> PRIRModel | None:
        return self.session.scalar(select(PRIRModel).where(PRIRModel.task_id == task_id))

    def add(self, item: PRIRModel) -> PRIRModel:
        self.session.add(item)
        self.session.commit()
        self.session.refresh(item)
        return item

    def save(self, item: PRIRModel) -> PRIRModel:
        self.session.commit()
        self.session.refresh(item)
        return item


class StrategyRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_many(self, items: list[StrategyRunModel]) -> list[StrategyRunModel]:
        self.session.add_all(items)
        self.session.commit()
        for item in items:
            self.session.refresh(item)
        return items

    def list_by_run_id(self, run_id: str) -> list[StrategyRunModel]:
        return list(
            self.session.scalars(
                select(StrategyRunModel)
                .where(StrategyRunModel.run_id == run_id)
                .order_by(StrategyRunModel.priority.asc(), StrategyRunModel.id.asc())
            )
        )

    def active_run_ids(self, task_id: str) -> list[str]:
        """返回任务当前处于 running/paused 的 run_id（可能有历史多条）。"""
        return list(
            self.session.scalars(
                select(StrategyRunModel.run_id)
                .where(
                    StrategyRunModel.task_id == task_id,
                    StrategyRunModel.status.in_(
                        ("running", "paused")
                    ),
                )
                .distinct()
            )
        )

    def save_all(self, items: list[StrategyRunModel]) -> list[StrategyRunModel]:
        self.session.commit()
        for item in items:
            self.session.refresh(item)
        return items


class RunRecordRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, run_id: str) -> RunRecordModel | None:
        return self.session.scalar(
            select(RunRecordModel).where(RunRecordModel.run_id == run_id)
        )

    def list_stale(self) -> list[RunRecordModel]:
        """返回上次中断、需要重启恢复的记录（running/paused）。"""
        return list(
            self.session.scalars(
                select(RunRecordModel)
                .where(RunRecordModel.status.in_(("running", "paused")))
                .order_by(RunRecordModel.id.asc())
            )
        )

    def add(self, item: RunRecordModel) -> RunRecordModel:
        self.session.add(item)
        self.session.commit()
        self.session.refresh(item)
        return item

    def save(self, item: RunRecordModel) -> RunRecordModel:
        self.session.commit()
        self.session.refresh(item)
        return item


class PatternKnowledgeRepository:
    """Read and aggregate abstract pattern knowledge in the caller's transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_identity(
        self, scope: str, pattern_type: str, pattern_signature: str
    ) -> PatternKnowledgeModel | None:
        return self.session.scalar(
            select(PatternKnowledgeModel).where(
                PatternKnowledgeModel.scope == scope,
                PatternKnowledgeModel.pattern_type == pattern_type,
                PatternKnowledgeModel.pattern_signature == pattern_signature,
            )
        )

    def count_scope(self, scope: str) -> int:
        return int(self.session.scalar(
            select(func.count()).select_from(PatternKnowledgeModel).where(
                PatternKnowledgeModel.scope == scope
            )
        ) or 0)

    def task_observed(self, pattern_id: int, task_id: str) -> bool:
        return self.session.scalar(
            select(PatternTaskObservationModel.id).where(
                PatternTaskObservationModel.pattern_id == pattern_id,
                PatternTaskObservationModel.task_id == task_id,
            )
        ) is not None

    def list(
        self,
        *,
        target_type: str | None = None,
        algorithm: str | None = None,
        pattern_type: str | None = None,
        minimum_confidence: float = 0.0,
        limit: int = 100,
        minimum_observations: int = 0,
        minimum_tasks: int = 0,
    ) -> list[PatternKnowledgeModel]:
        statement = select(PatternKnowledgeModel).where(
            PatternKnowledgeModel.confidence >= minimum_confidence,
            PatternKnowledgeModel.observation_count >= minimum_observations,
            PatternKnowledgeModel.task_count >= minimum_tasks,
        )
        if target_type is not None:
            statement = statement.where(
                PatternKnowledgeModel.target_type == target_type
            )
        if algorithm is not None:
            statement = statement.where(PatternKnowledgeModel.algorithm == algorithm)
        if pattern_type is not None:
            statement = statement.where(
                PatternKnowledgeModel.pattern_type == pattern_type
            )
        statement = statement.order_by(
            PatternKnowledgeModel.confidence.desc(),
            PatternKnowledgeModel.task_count.desc(),
            PatternKnowledgeModel.observation_count.desc(),
            PatternKnowledgeModel.pattern_type.asc(),
            PatternKnowledgeModel.pattern_signature.asc(),
            PatternKnowledgeModel.id.asc(),
        ).limit(limit)
        return list(self.session.scalars(statement))


class FeedbackRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, run_id: str) -> FeedbackRunModel | None:
        return self.session.scalar(
            select(FeedbackRunModel).where(FeedbackRunModel.run_id == run_id)
        )
