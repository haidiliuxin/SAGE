from __future__ import annotations

from sage_pass.candidate_generator import CandidateGenerator
from sage_pass.config import Settings
from sage_pass.database import Database
from sage_pass.generators import (
    BaselineGenerator,
    GeneratorRegistry,
    HistoryGenerator,
)
from sage_pass.real_executor import RealExecutor
from sage_pass.schemas import StrategyItem, StrategyPlan


class SpyBaselineGenerator(BaselineGenerator):
    def __init__(self) -> None:
        self.prepare_calls = 0

    def prepare(self, request):
        self.prepare_calls += 1
        return super().prepare(request)


def test_real_executor_accepts_and_owns_injected_generator_registry(tmp_path):
    database = Database(f"sqlite:///{(tmp_path / 'registry.db').as_posix()}")
    database.create_all()
    registry = GeneratorRegistry()
    spy = SpyBaselineGenerator()
    registry.register(spy)

    executor = RealExecutor(
        session_factory=database.session_factory,
        settings=Settings(
            database_url=f"sqlite:///{(tmp_path / 'registry.db').as_posix()}",
            upload_dir=tmp_path / "uploads",
            max_upload_bytes=1024,
            cors_origins=(),
        ),
        generator_registry=registry,
    )

    assert isinstance(executor.candidate_generator, CandidateGenerator)
    assert executor.candidate_generator.registry is registry
    assert executor.candidate_generator.registry.get("baseline") is spy
    batches = list(executor.candidate_generator.iter_plan_batches(
        StrategyPlan(
            task_id="T-REGISTRY",
            planner_type="rule",
            total_time_budget=1,
            strategies=[StrategyItem(
                strategy_id="S1",
                strategy_name="Baseline",
                priority=1,
                time_budget=1,
                candidate_budget=1,
                reason="registry integration",
            )],
        )
    ))
    assert spy.prepare_calls == 1
    assert batches[0].generator_id == "baseline"
    executor.shutdown()
    database.dispose()


def test_real_executor_pipeline_routes_history_to_personalized_generator(tmp_path):
    database = Database(f"sqlite:///{(tmp_path / 'history-registry.db').as_posix()}")
    database.create_all()
    registry = GeneratorRegistry()
    registry.register(HistoryGenerator())
    executor = RealExecutor(
        session_factory=database.session_factory,
        settings=Settings(
            database_url=f"sqlite:///{(tmp_path / 'history-registry.db').as_posix()}",
            upload_dir=tmp_path / "uploads",
            max_upload_bytes=1024,
            cors_origins=(),
        ),
        generator_registry=registry,
    )
    plan = StrategyPlan(
        task_id="T-HISTORY-REGISTRY",
        planner_type="rule",
        total_time_budget=1,
        strategies=[StrategyItem(
            strategy_id="S4",
            strategy_name="Personalized",
            priority=1,
            time_budget=1,
            candidate_budget=3,
            reason="history integration",
        )],
    )
    batches = list(executor.candidate_generator.iter_plan_batches(
        plan, historical_passwords=("Admin2020!",)
    ))
    assert batches[0].generator_id == "history"
    assert batches[0].candidates == (
        "Admin2020!", "admin2020!", "ADMIN2020!"
    )
    executor.shutdown()
    database.dispose()
