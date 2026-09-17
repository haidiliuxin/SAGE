"""数据库迁移框架测试（A 第 8～14 天：完成数据库迁移）。"""

from __future__ import annotations

import pytest

from sage_pass.database import Database
from sage_pass.migrations import applied_versions, run_migrations


def _table_sql(engine, name: str) -> str:
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=:name",
            {"name": name},
        ).fetchone()
    return row[0] if row else ""


def test_fresh_database_records_and_applies_migrations(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'm1.db').as_posix()}")
    db.create_all()
    assert applied_versions(db.engine) == [
        "0001_strategy_runs_run_id",
        "0002_task_wordlist_file",
    ]
    # run_id 现在是普通索引而非唯一约束
    assert "UNIQUE" not in _table_sql(db.engine, "strategy_runs").upper()
    db.dispose()


def test_migrations_are_idempotent(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'm2.db').as_posix()}")
    db.create_all()
    assert run_migrations(db.engine) == []
    assert applied_versions(db.engine) == [
        "0001_strategy_runs_run_id",
        "0002_task_wordlist_file",
    ]
    db.dispose()


def test_migration_upgrades_legacy_unique_run_id_table(tmp_path):
    path = tmp_path / "legacy.db"
    db = Database(f"sqlite:///{path.as_posix()}")
    # 模拟旧库：手工建带 UNIQUE(run_id) 的表并写入一行历史数据。
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE strategy_runs (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                run_id VARCHAR(32) NOT NULL UNIQUE,
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
                finished_at VARCHAR(40)
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO strategy_runs (run_id, task_id, strategy_id, "
            "strategy_name, priority, time_budget, candidate_budget, parameters,"
            " status, tested, recovered) VALUES "
            "('RLEGACY', 'T1', 'S1', 'Baseline', 1, 10, 100, '{}', 'completed', 5, 1)"
        )

    db.create_all()  # 应升级旧表且保留数据
    assert applied_versions(db.engine) == [
        "0001_strategy_runs_run_id",
        "0002_task_wordlist_file",
    ]
    with db.engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT run_id, tested FROM strategy_runs"
        ).fetchall()
    assert rows == [("RLEGACY", 5)]
    # 升级后同一 run 可有多条策略行
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO strategy_runs (run_id, task_id, strategy_id, "
            "strategy_name, priority, time_budget, candidate_budget, parameters,"
            " status, tested, recovered) VALUES "
            "('RLEGACY', 'T1', 'S2', 'Rule', 2, 10, 100, '{}', 'completed', 5, 1)"
        )
    with db.engine.connect() as connection:
        count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM strategy_runs WHERE run_id='RLEGACY'"
        ).scalar()
    assert count == 2
    db.dispose()
