from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import FileModel, TaskModel


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
