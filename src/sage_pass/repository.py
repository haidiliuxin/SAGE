from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import FileModel, PRIRModel, StrategyRunModel, TaskModel


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
