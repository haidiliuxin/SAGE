from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        if self.engine.dialect.name == "sqlite":
            self._migrate_sqlite_strategy_runs_run_id()

    def dispose(self) -> None:
        self.engine.dispose()

    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session

    def _migrate_sqlite_strategy_runs_run_id(self) -> None:
        with self.engine.begin() as connection:
            if not _strategy_runs_has_unique_run_id(connection):
                return
            connection.exec_driver_sql("DROP TABLE IF EXISTS strategy_runs_new")
            connection.exec_driver_sql(
                """
                CREATE TABLE strategy_runs_new (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    run_id VARCHAR(32) NOT NULL,
                    task_id VARCHAR(32) NOT NULL,
                    strategy_id VARCHAR(2) NOT NULL,
                    strategy_name VARCHAR(100) NOT NULL,
                    priority INTEGER NOT NULL,
                    time_budget INTEGER NOT NULL,
                    candidate_budget INTEGER NOT NULL,
                    parameters JSON NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    tested INTEGER NOT NULL,
                    recovered INTEGER NOT NULL,
                    started_at VARCHAR(40),
                    finished_at VARCHAR(40),
                    FOREIGN KEY(task_id) REFERENCES tasks (task_id)
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO strategy_runs_new (
                    id,
                    run_id,
                    task_id,
                    strategy_id,
                    strategy_name,
                    priority,
                    time_budget,
                    candidate_budget,
                    parameters,
                    status,
                    tested,
                    recovered,
                    started_at,
                    finished_at
                )
                SELECT
                    id,
                    run_id,
                    task_id,
                    strategy_id,
                    strategy_name,
                    priority,
                    time_budget,
                    candidate_budget,
                    COALESCE(parameters, '{}'),
                    status,
                    COALESCE(tested, 0),
                    COALESCE(recovered, 0),
                    started_at,
                    finished_at
                FROM strategy_runs
                """
            )
            connection.exec_driver_sql("DROP TABLE strategy_runs")
            connection.exec_driver_sql("ALTER TABLE strategy_runs_new RENAME TO strategy_runs")
            connection.exec_driver_sql(
                "CREATE INDEX ix_strategy_runs_run_id ON strategy_runs (run_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX ix_strategy_runs_task_id ON strategy_runs (task_id)"
            )


def _strategy_runs_has_unique_run_id(connection) -> bool:
    indexes = connection.exec_driver_sql("PRAGMA index_list('strategy_runs')").mappings()
    for index in indexes:
        if not index["unique"]:
            continue
        columns = connection.exec_driver_sql(
            f"PRAGMA index_info({_quote_sqlite_identifier(index['name'])})"
        ).mappings()
        if [column["name"] for column in columns] == ["run_id"]:
            return True
    return False


def _quote_sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
