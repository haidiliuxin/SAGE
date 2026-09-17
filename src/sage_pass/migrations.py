"""轻量数据库迁移框架（A 第 8～14 天：完成数据库迁移）。

设计目标：
- 不引入额外依赖，用 `schema_migrations` 表记录已应用版本；
- 迁移函数幂等、可重复运行，失败即抛错（不吞异常）；
- SQLite 专用迁移在非 SQLite 后端自动跳过；
- 现有 `strategy_runs.run_id` 唯一性修复迁移已并入本框架（版本 0001）。

新增表由 `Base.metadata.create_all()` 处理；本框架负责“已存在对象的变更”，
例如加列、改索引、数据回填。新增变更时在 `MIGRATIONS` 末尾追加即可。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, text


def _now_iso() -> str:
    """本地时间戳（避免 import service 造成循环依赖）。"""
    shanghai = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(shanghai).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    description: str
    apply: Callable[[Engine], None]


def _quote_sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _strategy_runs_has_unique_run_id(connection) -> bool:
    indexes = connection.exec_driver_sql(
        "PRAGMA index_list('strategy_runs')"
    ).mappings()
    for index in indexes:
        if not index["unique"]:
            continue
        columns = connection.exec_driver_sql(
            f"PRAGMA index_info({_quote_sqlite_identifier(index['name'])})"
        ).mappings()
        if [column["name"] for column in columns] == ["run_id"]:
            return True
    return False


def _migrate_strategy_runs_run_id(engine: Engine) -> None:
    """0001：把 strategy_runs.run_id 的唯一约束改为普通索引（一个 run 多行）。"""
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
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
                id, run_id, task_id, strategy_id, strategy_name, priority,
                time_budget, candidate_budget, parameters, status, tested,
                recovered, started_at, finished_at
            )
            SELECT
                id, run_id, task_id, strategy_id, strategy_name, priority,
                time_budget, candidate_budget, COALESCE(parameters, '{}'),
                status, COALESCE(tested, 0), COALESCE(recovered, 0),
                started_at, finished_at
            FROM strategy_runs
            """
        )
        connection.exec_driver_sql("DROP TABLE strategy_runs")
        connection.exec_driver_sql(
            "ALTER TABLE strategy_runs_new RENAME TO strategy_runs"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_strategy_runs_run_id ON strategy_runs (run_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_strategy_runs_task_id ON strategy_runs (task_id)"
        )


MIGRATIONS: Sequence[Migration] = (
    Migration(
        version="0001_strategy_runs_run_id",
        description="strategy_runs.run_id 唯一约束 -> 普通索引（多策略同一 run）",
        apply=_migrate_strategy_runs_run_id,
    ),
)


def run_migrations(engine: Engine) -> list[str]:
    """应用未执行的迁移，返回本次应用的版本列表（幂等）。"""
    applied: list[str] = []
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(64) NOT NULL PRIMARY KEY,
                description VARCHAR(255) NOT NULL,
                applied_at VARCHAR(40) NOT NULL
            )
            """
        )
        done = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT version FROM schema_migrations"
            )
        }
    for migration in MIGRATIONS:
        if migration.version in done:
            continue
        migration.apply(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, description, applied_at) "
                    "VALUES (:version, :description, :applied_at)"
                ),
                {
                    "version": migration.version,
                    "description": migration.description,
                    "applied_at": _now_iso(),
                },
            )
        applied.append(migration.version)
    return applied


def applied_versions(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        try:
            rows = connection.exec_driver_sql(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        except Exception:  # pragma: no cover - 表尚未创建
            return []
        return [row[0] for row in rows]
