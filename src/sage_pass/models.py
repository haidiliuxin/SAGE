from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class TaskModel(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    target_type: Mapped[str] = mapped_column(String(20), index=True)
    target_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("files.file_id"), nullable=True
    )
    known_algorithm: Mapped[str | None] = mapped_column(String(100), nullable=True)
    time_budget: Mapped[int] = mapped_column(Integer)
    candidate_budget: Mapped[int] = mapped_column(Integer)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))

    prir: Mapped["PRIRModel | None"] = relationship(
        back_populates="task", cascade="all, delete-orphan", uselist=False
    )
    strategy_runs: Mapped[list["StrategyRunModel"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    file: Mapped["FileModel | None"] = relationship(back_populates="tasks")


class PRIRModel(Base):
    __tablename__ = "prirs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.task_id"), unique=True, index=True
    )
    target_type: Mapped[str] = mapped_column(String(20))
    algorithm: Mapped[str] = mapped_column(String(100), default="unknown")
    salt: Mapped[bool | None] = mapped_column(nullable=True)
    verification_cost: Mapped[str] = mapped_column(String(20), default="unknown")
    context_available: Mapped[bool] = mapped_column(default=False)
    candidate_space: Mapped[int | None] = mapped_column(nullable=True)
    time_budget: Mapped[int] = mapped_column(Integer)
    candidate_budget: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(default=0.0)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))

    task: Mapped[TaskModel] = relationship(back_populates="prir")


class StrategyRunModel(Base):
    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.task_id"), index=True
    )
    strategy_id: Mapped[str] = mapped_column(String(2))
    strategy_name: Mapped[str] = mapped_column(String(100))
    priority: Mapped[int] = mapped_column(Integer)
    time_budget: Mapped[int] = mapped_column(Integer)
    candidate_budget: Mapped[int] = mapped_column(Integer)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20))
    tested: Mapped[int] = mapped_column(Integer, default=0)
    recovered: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

    task: Mapped[TaskModel] = relationship(back_populates="strategy_runs")


class FileModel(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))

    tasks: Mapped[list[TaskModel]] = relationship(back_populates="file")


class RunRecordModel(Base):
    """真实执行的持久化运行记录（第 3 周甲后半：状态持久化与断点续跑）。

    snapshot 保存启动时一次性写入的大块内容（目标、计划、候选批次等）；
    progress 保存随批次推进高频更新的小状态（各策略游标、已测/已恢复与
    Bandit 统计）。服务重启后根据这两个字段重建运行状态并自动续跑。
    """

    __tablename__ = "run_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[str] = mapped_column(String(40))
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))
