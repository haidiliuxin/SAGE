from __future__ import annotations

import threading
import time
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .candidate_generator import CandidateBatch, CandidateGenerator
from .candidate_types import CandidateRecord
from .config import Settings
from .enums import ExecutionMode, SchedulerType, StrategyId, TargetType, TaskStatus
from .errors import AppError
from .feedback import FeedbackConfig, FeedbackService
from .generators import (
    GeneratorRegistry,
    GeneratorRegistryError,
    STRATEGY_GENERATOR_IDS,
)
from .hashcat_adapter import (
    HashcatAdapter,
    HashcatHandle,
    HashcatJob,
    HashcatResult,
    RecoveredCredential,
    resolve_hashcat_mode,
)
from .interfaces import BatchOutcome, DecisionEvent
from .decision.rewards import RewardWeights, RewardContext
from .decision.research_log import ResearchRecorder, SqliteResearchLog
from .decision.costs import UCBConfig
from .decision.ucb import UCBScheduler
from .models import RunRecordModel
from .repository import (
    FileRepository,
    PRIRRepository,
    PatternKnowledgeRepository,
    RunRecordRepository,
    StrategyRunRepository,
    TaskRepository,
)
from .run_control import RunControl
from .scheduler import (
    ArmSpec,
    SchedulerStopReason,
    build_scheduler,
    decision_diagnostics,
)
from .schemas import (
    ExecutionRequest,
    ExecutionStarted,
    RecoveredItem,
    RunResult,
    RunStatus,
    StrategyPlan,
    StrategyResult,
    TaskDetail,
)
from .service import now_iso, public_id, update_task_status
from .targets import build_target_extractors, extract_target
from .zip_adapter import ZipHashExtractor
from .transfer import load_transfer_knowledge

ZIP_EXTRACTION_TIMEOUT = 30.0
RUN_SNAPSHOT_SCHEMA_VERSION = 2
LOGGER = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


@dataclass
class _StrategyState:
    strategy_id: str
    strategy_name: str
    priority: int
    time_budget: int
    candidate_budget: int
    parameters: dict[str, Any]
    generator_id: str = ""
    candidate_batches: tuple[tuple[str, ...], ...] = ()
    candidate_record_batches: tuple[tuple[CandidateRecord, ...], ...] = ()
    status: str = TaskStatus.RUNNING.value
    started_at: str | None = None
    finished_at: str | None = None
    tested: int = 0
    recovered: int = 0
    recovered_items: list[RecoveredCredential] = field(default_factory=list)
    message: str = ""
    exit_code: int | None = None
    handle: HashcatHandle | None = None
    next_batch_index: int = 0
    scheduled_batches: int = 0
    consumed_batches: int = 0
    time_cost: float = 0.0
    transfer_score: float | None = None
    # 原生词表攻击：直接让 hashcat 读整本字典（单进程、不经过 Python 候选列表）。
    native_wordlist_path: Path | None = None
    native_pending: bool = False


@dataclass
class _RunState:
    run_id: str
    task_id: str
    targets: tuple[str, ...]
    hash_mode: int
    strategies: list[_StrategyState] = field(default_factory=list)
    status: TaskStatus = TaskStatus.RUNNING
    started_at: str = ""
    finished_at: str | None = None
    message: str = ""
    expected_candidates: int = 0
    cancel_requested: bool = False
    finished: bool = False
    failed_launch: str | None = None
    persist_error: str | None = None
    thread: threading.Thread | None = None
    scheduler: Any | None = None
    current_strategy_id: str | None = None
    decision_events: list[DecisionEvent] = field(default_factory=list)
    round_index: int = 0
    research: ResearchRecorder | None = None
    research_previous_arm: str | None = None
    research_stop_reason: str | None = None
    stop_on_hit: bool = False


class RealExecutor:
    """基于 Hashcat 的真实执行器。

    后端按计划生成候选批次，由 Bandit Scheduler 在批次边界动态选择策略并
    交给 Hashcat 执行；后台线程负责启动/轮询/回收，对外通过 run registry
    提供 status/result，支持批次边界暂停/继续/取消，并向数据库回写策略运行
    行、任务终态与 RunRecordModel 运行记录。运行记录持久化目标、计划、候选
    批次与逐批进度/统计检查点，服务重启后可自动从断点续跑。
    """

    def __init__(
        self,
        *,
        session_factory,
        settings: Settings,
        hashcat: HashcatAdapter | None = None,
        zip_extractor: ZipHashExtractor | None = None,
        pdf_extractor: Any | None = None,
        office_extractor: Any | None = None,
        candidate_generator: CandidateGenerator | None = None,
        generator_registry: GeneratorRegistry | None = None,
        control: RunControl | None = None,
        research_log: SqliteResearchLog | None = None,
        reward_weights: RewardWeights | None = None,
        ucb_config: UCBConfig | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.hashcat = hashcat or HashcatAdapter(settings.hashcat_path)
        self.zip_extractor = zip_extractor or ZipHashExtractor(
            settings.zip2john_path
        )
        self.pdf_extractor = pdf_extractor
        self.office_extractor = office_extractor
        if candidate_generator is not None and generator_registry is not None:
            raise ValueError(
                "candidate_generator 与 generator_registry 不能同时提供"
            )
        self.candidate_generator = candidate_generator or CandidateGenerator(
            registry=generator_registry,
            pcfg_variant=settings.pcfg_variant,
            pcfg_ruleset_path=(
                str(settings.pcfg_ruleset_path)
                if settings.pcfg_ruleset_path is not None
                else None
            ),
            markov_ruleset_path=(
                str(settings.markov_ruleset_path)
                if settings.markov_ruleset_path is not None
                else None
            ),
            markov_order=settings.markov_order,
            s3_generator_id=settings.s3_generator_id,
            s4_generator_id=settings.s4_generator_id,
        )
        self.control = control
        self.feedback_config = FeedbackConfig(
            minimum_observations=settings.feedback_minimum_observations,
            minimum_tasks=settings.feedback_minimum_tasks,
            maximum_patterns_per_scope=settings.feedback_maximum_patterns_per_scope,
            recency_half_life_days=settings.feedback_recency_half_life_days,
        )
        self.feedback_service = FeedbackService(
            session_factory, config=self.feedback_config
        )
        self._registry: dict[str, _RunState] = {}
        self._task_runs: dict[str, set[str]] = {}
        self._lock = threading.RLock()
        self._research_log = research_log
        self.reward_weights = reward_weights or RewardWeights()
        self.ucb_config = ucb_config or UCBConfig()

    # ------------------------------------------------------------------ 查询
    def has_run(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._registry

    def status(self, run_id: str) -> RunStatus:
        state = self._require(run_id)
        with self._lock:
            tested = sum(item.tested for item in state.strategies)
            recovered = sum(item.recovered for item in state.strategies)
            current = state.current_strategy_id
            inflight = any(
                item.handle is not None for item in state.strategies
            )
            pause_requested = bool(
                self.control
                and self.control.is_paused(run_id)
                and state.status == TaskStatus.RUNNING
            )
            paused = pause_requested and not inflight
            message = self._status_message(state, current, paused, inflight)
        elapsed = _elapsed_seconds(state.started_at, state.finished_at)
        progress = 1.0 if state.finished else (
            min(1.0, tested / state.expected_candidates)
            if state.expected_candidates > 0
            else 1.0
        )
        return RunStatus(
            task_id=state.task_id,
            run_id=run_id,
            status=TaskStatus.PAUSED if paused else state.status,
            progress=progress,
            current_strategy=current if not paused else None,
            elapsed_time=elapsed,
            tested=tested,
            recovered=recovered,
            message=message,
        )

    def result(self, run_id: str) -> RunResult:
        state = self._require(run_id)
        with self._lock:
            if not state.finished:
                raise AppError(
                    "RUN_IN_PROGRESS",
                    "真实执行尚未结束，请稍后获取结果",
                    status_code=409,
                    details={"run_id": run_id},
                )
            strategy_results: list[StrategyResult] = []
            recovered_seen: set[tuple[str, str]] = set()
            recovered_items: list[RecoveredItem] = []
            for item in state.strategies:
                strategy_results.append(
                    StrategyResult(
                        strategy_id=item.strategy_id,
                        time=_strategy_result_time(item),
                        tested=item.tested,
                        recovered=item.recovered,
                        success_rate=(
                            item.recovered / item.tested if item.tested > 0 else 0.0
                        ),
                    )
                )
                for credential in item.recovered_items:
                    key = (credential.target, credential.plaintext)
                    if key in recovered_seen:
                        continue
                    recovered_seen.add(key)
                    recovered_items.append(
                        RecoveredItem(
                            target=credential.target,
                            plaintext=credential.plaintext,
                        )
                    )
            total_time = _elapsed_seconds(state.started_at, state.finished_at)
            finished_at = state.finished_at or now_iso()
            return RunResult(
                task_id=state.task_id,
                run_id=run_id,
                status=state.status,
                total_time=total_time,
                total_tested=sum(
                    result.tested for result in strategy_results
                ),
                total_recovered=len(recovered_items),
                strategy_results=strategy_results,
                finished_at=datetime.fromisoformat(finished_at),
                recovered_items=recovered_items,
                message=state.message,
            )

    # ------------------------------------------------------------------ 启动
    def start(
        self,
        session: Session,
        task: TaskDetail,
        plan: StrategyPlan,
        payload: ExecutionRequest,
    ) -> ExecutionStarted:
        if payload.mode != ExecutionMode.REAL:
            raise AppError(
                "EXECUTION_FAILED",
                "真实执行器仅支持 real 模式",
                status_code=422,
                details={"mode": payload.mode.value},
            )
        if not plan.strategies:
            raise AppError(
                "EXECUTION_FAILED",
                "策略计划为空",
                status_code=422,
                details={"task_id": task.task_id},
            )
        targets, hash_mode = self._resolve_targets(
            session, task, payload.hashcat_mode
        )
        started_at = now_iso()
        run_id = public_id("R")
        batches_by_strategy: dict[str, list[CandidateBatch]] = {}
        native_wordlist = self._native_wordlist_path(session, task)
        prir_model = PRIRRepository(session).get(task.task_id)
        transfer_patterns, knowledge_summary = load_transfer_knowledge(
            PatternKnowledgeRepository(session),
            target_type=task.target.type.value,
            algorithm=prir_model.algorithm if prir_model is not None else "unknown",
            candidate_budget=task.candidate_budget,
            config=self.feedback_config,
        )
        try:
            for batch in self.candidate_generator.iter_plan_batches(
                plan,
                supplied_candidates=payload.candidates,
                task_context=task.context,
                historical_passwords=task.historical_passwords,
                transfer_patterns=transfer_patterns,
                batch_size=self.settings.decision_batch_size,
            ):
                batches_by_strategy.setdefault(
                    batch.strategy_id.value, []
                ).append(batch)
        except (ValueError, GeneratorRegistryError) as exc:
            raise AppError(
                "EXECUTION_FAILED",
                "生成真实执行候选失败",
                status_code=422,
                details={"task_id": task.task_id, "reason": str(exc)},
            ) from exc
        strategies: list[_StrategyState] = []
        expected = 0
        for item in plan.strategies:
            generated_batches = tuple(
                batches_by_strategy.get(item.strategy_id.value, ())
            )
            candidate_batches = tuple(batch.candidates for batch in generated_batches)
            candidate_record_batches = tuple(
                batch.records for batch in generated_batches
            )
            expected += sum(len(batch) for batch in candidate_batches)
            strategies.append(
                _StrategyState(
                    strategy_id=item.strategy_id.value,
                    strategy_name=item.strategy_name,
                    priority=item.priority,
                    time_budget=item.time_budget,
                    candidate_budget=item.candidate_budget,
                    parameters=item.parameters,
                    generator_id=(
                        generated_batches[0].generator_id
                        if generated_batches
                        and generated_batches[0].generator_id
                        else STRATEGY_GENERATOR_IDS[item.strategy_id]
                    ),
                    candidate_batches=candidate_batches,
                    candidate_record_batches=candidate_record_batches,
                    transfer_score=(
                        knowledge_summary.transfer_score
                        if item.strategy_id.value == "S5"
                        else None
                    ),
                    native_wordlist_path=(
                        native_wordlist
                        if (
                            native_wordlist is not None
                            and item.strategy_id == StrategyId.S1
                        )
                        else None
                    ),
                    native_pending=(
                        native_wordlist is not None
                        and item.strategy_id == StrategyId.S1
                    ),
                )
            )
        if expected == 0 and native_wordlist is None:
            raise AppError(
                "EXECUTION_FAILED",
                "后端未能为策略计划生成候选，无法启动真实执行",
                status_code=422,
                details={"task_id": task.task_id},
            )
        rows = [
            _to_run_row(run_id, task.task_id, item)
            for item in strategies
        ]
        StrategyRunRepository(session).add_many(rows)

        snapshot = _serialize_snapshot(
            plan=plan,
            payload=payload,
            targets=targets,
            hash_mode=hash_mode,
            batches_by_strategy=batches_by_strategy,
            expected=expected,
            total_candidate_budget=task.candidate_budget,
            transfer_scores={
                item.strategy_id: item.transfer_score for item in strategies
            },
            scheduler_type=self.settings.scheduler_type.value,
            stop_on_hit=(
                payload.stop_on_hit
                if payload.stop_on_hit is not None
                else self.settings.stop_on_hit
            ),
        )
        # Freeze reward configuration with the run; a later service default
        # must not change the interpretation of resumed batches.
        snapshot["research_reward_weights"] = asdict(self.reward_weights)
        snapshot["ucb_config"] = asdict(self.ucb_config)
        record = RunRecordModel(
            run_id=run_id,
            task_id=task.task_id,
            mode=ExecutionMode.REAL.value,
            status=TaskStatus.RUNNING.value,
            started_at=started_at,
            snapshot=snapshot,
            progress=_initial_progress(snapshot, started_at),
            created_at=started_at,
            updated_at=started_at,
        )
        RunRecordRepository(session).add(record)

        arms = [
            ArmSpec(
                strategy_id=item.strategy_id,
                priority=item.priority,
                candidate_budget=item.candidate_budget,
                time_budget=float(
                    max(
                        1,
                        payload.timeout
                        if payload.timeout is not None
                        else item.time_budget,
                    )
                ),
                transfer_score=item.transfer_score,
            )
            for item in strategies
        ]
        try:
            scheduler = build_scheduler(
                self.settings.scheduler_type,
                arms,
                total_candidate_budget=task.candidate_budget,
                total_time_budget=float(plan.total_time_budget),
                initial_targets=len(set(targets)), ucb_config=self.ucb_config,
            )
        except ValueError as exc:
            raise AppError(
                "EXECUTION_FAILED",
                str(exc),
                status_code=422,
                details={"scheduler_type": self.settings.scheduler_type.value},
            ) from exc
        state = _RunState(
            run_id=run_id,
            task_id=task.task_id,
            targets=targets,
            hash_mode=hash_mode,
            strategies=strategies,
            started_at=started_at,
            expected_candidates=expected,
            scheduler=scheduler,
            stop_on_hit=(
                payload.stop_on_hit
                if payload.stop_on_hit is not None
                else self.settings.stop_on_hit
            ),
        )
        with self._lock:
            self._registry[run_id] = state
            self._task_runs.setdefault(task.task_id, set()).add(run_id)
        thread = threading.Thread(
            target=self._run_worker,
            args=(run_id,),
            name=f"sage-real-{run_id}",
            daemon=True,
        )
        state.thread = thread
        thread.start()
        return ExecutionStarted(
            task_id=task.task_id,
            run_id=run_id,
            status=TaskStatus.RUNNING,
            started_at=datetime.fromisoformat(started_at),
        )

    # ------------------------------------------------------------------ 持久化读取
    def research_log_reader(self) -> SqliteResearchLog | None:
        """Open the configured journal without creating a missing database."""
        path = (self._research_log.path if self._research_log is not None else
                self.settings.upload_dir.parent / "research" / "real.sqlite3")
        return SqliteResearchLog(path, read_only=True) if path.is_file() else None

    def has_record(self, run_id: str) -> bool:
        """是否存在该 run 的持久化记录（用于重启后查询真实结果）。"""
        with self.session_factory() as session:
            return RunRecordRepository(session).get(run_id) is not None

    def load_persisted_status(self, run_id: str) -> RunStatus:
        record = self._require_record(run_id)
        snapshot, progress = _validate_record_payload(record)
        del snapshot
        strategies = progress["strategies"]
        tested = sum(int(item.get("tested", 0)) for item in strategies.values())
        recovered = sum(
            int(item.get("recovered", 0)) for item in strategies.values()
        )
        expected = int(progress.get("expected_candidates", 0))
        status = TaskStatus(record.status)
        progress_ratio = (
            min(1.0, tested / expected) if expected > 0 else 1.0
        )
        return RunStatus(
            task_id=record.task_id,
            run_id=run_id,
            status=status,
            progress=1.0 if status in _TERMINAL_STATUSES else progress_ratio,
            current_strategy=None,
            elapsed_time=_record_elapsed(record),
            tested=tested,
            recovered=recovered,
            message=record.message or _record_default_message(status),
        )

    def load_persisted_result(self, run_id: str) -> RunResult:
        record = self._require_record(run_id)
        snapshot, progress = _validate_record_payload(record)
        status = TaskStatus(record.status)
        if status not in _TERMINAL_STATUSES:
            raise AppError(
                "RUN_IN_PROGRESS",
                "真实执行尚未结束，请稍后获取结果",
                status_code=409,
                details={"run_id": run_id},
            )
        strategies = progress["strategies"]
        recovered_seen: set[tuple[str, str]] = set()
        recovered_items: list[RecoveredItem] = []
        strategy_results: list[StrategyResult] = []
        for entry in snapshot["plan"]["strategies"]:
            strategy_id = entry["strategy_id"]
            item = strategies.get(strategy_id, {})
            tested = int(item.get("tested", 0))
            recovered = int(item.get("recovered", 0))
            strategy_results.append(
                StrategyResult(
                    strategy_id=strategy_id,
                    time=float(item.get("time_cost", 0.0)),
                    tested=tested,
                    recovered=recovered,
                    success_rate=(
                        recovered / tested if tested > 0 else 0.0
                    ),
                )
            )
            for target, plaintext in item.get("recovered_items", []):
                key = (target, plaintext)
                if key in recovered_seen:
                    continue
                recovered_seen.add(key)
                recovered_items.append(
                    RecoveredItem(target=target, plaintext=plaintext)
                )
        finished_at = record.finished_at or record.updated_at
        return RunResult(
            task_id=record.task_id,
            run_id=run_id,
            status=status,
            total_time=_record_elapsed(record),
            total_tested=sum(item.tested for item in strategy_results),
            total_recovered=len(recovered_items),
            strategy_results=strategy_results,
            finished_at=datetime.fromisoformat(finished_at),
            recovered_items=recovered_items,
            message=record.message or _record_default_message(status),
        )

    def _require_record(self, run_id: str) -> RunRecordModel:
        with self.session_factory() as session:
            record = RunRecordRepository(session).get(run_id)
        if record is None:
            raise AppError(
                "EXECUTION_FAILED",
                "执行不存在",
                status_code=404,
                details={"run_id": run_id},
            )
        return record

    # ------------------------------------------------------------------ 取消
    def cancel_task_runs(self, task_id: str) -> list[str]:
        with self._lock:
            run_ids = sorted(self._task_runs.get(task_id, ()))
        stopped: list[str] = []
        for run_id in run_ids:
            if self._request_cancel(run_id):
                stopped.append(run_id)
        return stopped

    def shutdown(self) -> None:
        with self._lock:
            active = [
                state
                for state in self._registry.values()
                if not state.finished and state.thread is not None
            ]
        for state in active:
            self._request_cancel(state.run_id)
        for state in active:
            if state.thread is not None:
                state.thread.join(timeout=5)
        if self.control is not None:
            self.control.clear_all()

    # ------------------------------------------------------------------ 内部
    def _target_extractors(self):
        """按当前实例的 zip 适配器构造提取器链（保持测试可注入 zip_extractor）。"""
        return build_target_extractors(
            self.settings,
            zip_extractor=self.zip_extractor,
            pdf_extractor=self.pdf_extractor,
            office_extractor=self.office_extractor,
            zip_timeout=ZIP_EXTRACTION_TIMEOUT,
        )

    def _native_wordlist_path(
        self, session: Session, task: TaskDetail
    ) -> Path | None:
        """S1 的原生词表攻击来源：任务上传的词表优先，其次 SAGE_WORDLIST_PATH。

        两种情况都把文件路径直接交给 hashcat（`--attack-mode 0 <target> <wordlist>`），
        因此字典由 hashcat 按需流式读取，不进入 Python 候选列表。
        """
        if task.wordlist_file_id:
            item = FileRepository(session).get(task.wordlist_file_id)
            if item is None:
                raise AppError(
                    "EXECUTION_FAILED",
                    "任务引用的词表文件记录不存在",
                    status_code=422,
                    details={"field": "wordlist_file_id"},
                )
            path = self.settings.upload_dir / item.stored_name
            if not path.is_file():
                raise AppError(
                    "EXECUTION_FAILED",
                    "任务引用的词表文件已丢失",
                    status_code=422,
                    details={"wordlist": item.stored_name},
                )
            return path
        configured = self.settings.wordlist_path
        if configured is None:
            return None
        path = Path(configured).expanduser()
        if not path.is_file():
            raise AppError(
                "EXECUTION_FAILED",
                "SAGE_WORDLIST_PATH 指向的词表文件不存在",
                status_code=422,
                details={"wordlist": str(path)},
            )
        return path

    def _target_file_path(self, session: Session, task: TaskDetail):
        if not task.target.file_id:
            return None
        file_item = FileRepository(session).get(task.target.file_id)
        if file_item is None:
            raise AppError(
                "EXECUTION_FAILED",
                "目标文件不存在",
                status_code=422,
                details={"file_id": task.target.file_id},
            )
        return self.settings.upload_dir / file_item.stored_name

    def _resolve_targets(
        self, session: Session, task: TaskDetail, explicit_mode: int | None
    ) -> tuple[tuple[str, ...], int]:
        """目标解析与 Hashcat 模式判定。

        模式判定顺序（A 第 2 项验收）：显式 hashcat_mode → known_algorithm →
        Analyzer 写入的 PRIR.algorithm → 报错；未知算法仍可用显式模式覆盖。
        """
        target_type = task.target.type
        extracted = extract_target(
            self._target_extractors(),
            target_type=target_type,
            content=task.target.content,
            file_path=self._target_file_path(session, task),
        )
        mode = explicit_mode
        if mode is None and target_type == TargetType.HASH:
            if task.known_algorithm:
                mode = resolve_hashcat_mode(task.known_algorithm)
            else:
                prir_model = PRIRRepository(session).get(task.task_id)
                prir_algorithm = (
                    prir_model.algorithm if prir_model is not None else None
                )
                if prir_algorithm and prir_algorithm != "unknown":
                    mode = resolve_hashcat_mode(prir_algorithm)
        if mode is None and extracted.hashcat_mode is not None:
            mode = extracted.hashcat_mode
        if mode is None:
            raise AppError(
                "EXECUTION_FAILED",
                "无法确定 Hashcat 模式：请提供 hashcat_mode、known_algorithm，"
                "或先完成 Analyzer 识别",
                status_code=422,
                details={
                    "task_id": task.task_id,
                    "target_type": target_type.value,
                    "algorithm": extracted.algorithm,
                },
            )
        return extracted.hashes, mode

    def _run_worker(self, run_id: str) -> None:
        state = self._registry[run_id]
        try:
            self._begin_research(state)
            self._execute_strategies(state)
        except AppError as exc:
            state.failed_launch = exc.message
        except Exception as exc:  # pragma: no cover - 防御未知异常
            state.failed_launch = f"内部错误：{exc}"
        finally:
            if state.research is not None:
                try:
                    state.research.finish(
                        "cancelled" if state.cancel_requested else (
                            state.research_stop_reason or ("failed" if state.failed_launch else "completed")
                        ),
                        state=state.scheduler.snapshot_statistics(),
                    )
                except Exception as exc:
                    state.persist_error = f"研究日志写入失败：{type(exc).__name__}"
                    state.failed_launch = "研究日志未能完整保存"
            self._finalize_run(state)
            persisted = False
            try:
                self._persist(state)
                persisted = True
            except Exception as exc:  # pragma: no cover - 回写失败不阻塞结果返回
                state.persist_error = f"回写数据库失败：{exc}"
            if persisted and state.status == TaskStatus.COMPLETED:
                try:
                    recovered = [
                        credential
                        for strategy in state.strategies
                        for credential in strategy.recovered_items
                    ]
                    self.feedback_service.process_completed_run(
                        run_id=state.run_id,
                        task_id=state.task_id,
                        recovered_items=recovered,
                    )
                except Exception as exc:  # feedback never changes run outcome
                    state.persist_error = (
                        "Feedback 处理失败（不影响评测结果）："
                        f"{type(exc).__name__}"
                    )
                    LOGGER.error(
                        "feedback processing failed for run_id=%s task_id=%s type=%s",
                        state.run_id,
                        state.task_id,
                        type(exc).__name__,
                    )

    def _execute_strategies(self, state: _RunState) -> None:
        scheduler = state.scheduler
        if scheduler is None:  # pragma: no cover - start() always installs one
            raise RuntimeError("Bandit scheduler is unavailable")
        self._begin_research(state)
        by_strategy = {item.strategy_id: item for item in state.strategies}

        while True:
            if self._paused_wait_cancelled(state):
                self._skip_remaining(state, TaskStatus.CANCELLED, "执行已停止")
                return
            if state.cancel_requested:
                self._skip_remaining(state, TaskStatus.CANCELLED, "执行已停止")
                return
            next_batch_sizes = _next_batch_sizes(state.strategies)
            prior_state = scheduler.snapshot_statistics()
            diagnostics = decision_diagnostics(scheduler, next_batch_sizes)
            decision = scheduler.select(next_batch_sizes)
            if decision is None:
                self._complete_scheduled_execution(
                    state, scheduler.stop_reason(next_batch_sizes)
                )
                state.failed_launch = ""
                return

            item = by_strategy[decision.strategy_id]
            if item.native_wordlist_path is not None:
                # 原生词表攻击：不经过 Python 候选，hashcat 自己读整本字典。
                candidates: tuple[str, ...] = ()
            else:
                source_batch = item.candidate_batches[item.next_batch_index]
                candidates = source_batch[: decision.candidate_limit]
            item.next_batch_index += 1
            item.scheduled_batches += 1
            state.round_index += 1
            prior_scores = {key: value.score for key, value in diagnostics["scores"].items()}
            if state.research is not None:
                state.research.begin(
                    decision=decision, available=diagnostics["available"],
                    scores=diagnostics["scores"], prior_state=prior_state,
                )
            item.started_at = item.started_at or now_iso()
            with self._lock:
                state.current_strategy_id = item.strategy_id
            try:
                if item.native_wordlist_path is not None:
                    handle = self.hashcat.start(
                        HashcatJob(
                            run_id=(
                                f"{state.run_id}-{item.strategy_id}-"
                                f"{item.scheduled_batches}"
                            ),
                            target_hashes=state.targets,
                            hash_mode=state.hash_mode,
                            timeout_seconds=decision.time_limit,
                            candidate_budget=0,
                            attack_mode=0,
                            wordlist_path=item.native_wordlist_path,
                            candidate_estimate=item.candidate_budget,
                        )
                    )
                    item.native_pending = False
                else:
                    handle = self.hashcat.start(
                        HashcatJob(
                            run_id=(
                                f"{state.run_id}-{item.strategy_id}-"
                                f"{item.scheduled_batches}"
                            ),
                            target_hashes=state.targets,
                            hash_mode=state.hash_mode,
                            candidates=candidates,
                            timeout_seconds=decision.time_limit,
                            candidate_budget=len(candidates),
                        )
                    )
            except AppError as exc:
                with self._lock:
                    state.current_strategy_id = None
                item.status = TaskStatus.FAILED.value
                item.finished_at = now_iso()
                item.message = exc.message
                state.failed_launch = exc.message
                state.research_stop_reason = "launch_failed"
                return
            item.handle = handle
            with self._lock:
                cancel_requested = state.cancel_requested
            if cancel_requested:
                result = handle.stop()
                item.handle = None
                with self._lock:
                    state.current_strategy_id = None
                outcome = self._record_batch_result(
                    scheduler, state, item, candidates, result
                )
                self._record_decision_event(
                    state,
                    decision,
                    item,
                    prior_state,
                    prior_scores,
                    outcome,
                    stop_reason="cancelled",
                )
                self._skip_remaining(state, TaskStatus.CANCELLED, "执行已停止")
                return
            try:
                result = handle.wait()
            except Exception as exc:  # pragma: no cover - wait 失败按执行失败处理
                item.status = TaskStatus.FAILED.value
                item.finished_at = now_iso()
                item.message = f"等待 Hashcat 失败：{exc}"
                state.failed_launch = item.message
                state.research_stop_reason = "execution_failed"
                return
            finally:
                item.handle = None
                with self._lock:
                    state.current_strategy_id = None
            outcome = self._record_batch_result(
                scheduler, state, item, candidates, result
            )
            self._record_decision_event(
                state, decision, item, prior_state, prior_scores, outcome
            )
            if result.status == TaskStatus.FAILED:
                item.status = TaskStatus.FAILED.value
                item.finished_at = now_iso()
                state.failed_launch = result.message or "执行失败"
                state.research_stop_reason = "execution_failed"
                return
            item.consumed_batches += 1
            self._checkpoint(state)
            if state.stop_on_hit and outcome.recovered > 0:
                # 命中即停：真实破解语义，命中后不再消耗剩余候选。
                self._complete_scheduled_execution(
                    state, None, label="all_targets_recovered"
                )
                state.failed_launch = ""
                return
            if state.cancel_requested:
                self._skip_remaining(state, TaskStatus.CANCELLED, "执行已停止")
                return

    def _record_batch_result(
        self,
        scheduler: Any,
        state: _RunState,
        item: _StrategyState,
        candidates: tuple[str, ...],
        result: HashcatResult,
    ) -> BatchOutcome:
        already_recovered = {
            credential.target for strategy in state.strategies
            for credential in strategy.recovered_items
        }
        recovered = self._accumulate_result(item, result)
        new_targets = {
            credential.target for credential in result.recovered
            if credential.target in state.targets
        } - already_recovered
        item.time_cost += result.duration
        # 原生攻击（词表/掩码）没有 Python 候选列表，用实测数量作为提交量，
        # 保证调度器收到正的候选计数。
        candidate_count = len(candidates) if candidates else max(result.tested, 1)
        outcome = BatchOutcome(
            run_id=state.run_id,
            arm_id=item.strategy_id,
            strategy_id=item.strategy_id,
            batch_index=item.scheduled_batches,
            candidate_count=candidate_count,
            tested=result.tested,
            recovered=len(new_targets),
            duration=result.duration,
            status=result.status,
            exit_code=result.exit_code,
            message=result.message,
        )
        if isinstance(scheduler, UCBScheduler):
            remaining_time = min(scheduler.total_time_budget - scheduler.total_time_cost,
                                 scheduler._arms[item.strategy_id].time_budget - scheduler.statistics(item.strategy_id).time_cost)
            # A raw progress count can overcount whole candidates for multiple
            # salts. Only an exhausted, non-recovering batch is safe to fit.
            complete = (result.status == TaskStatus.COMPLETED and result.exit_code == 1
                        and not result.recovered and result.duration < remaining_time)
            scheduler.observe(item.strategy_id, candidate_count=outcome.candidate_count,
                              tested=outcome.tested, recovered=outcome.recovered,
                              duration=outcome.duration, complete=complete, cost_tested_known=complete)
        else:
            scheduler.observe(
                item.strategy_id, candidate_count=outcome.candidate_count,
                tested=outcome.tested, recovered=recovered, duration=outcome.duration,
            )
        return outcome

    def _record_decision_event(
        self,
        state: _RunState,
        decision,
        item: _StrategyState,
        prior_state: dict[str, dict[str, object]],
        prior_scores: dict[str, float],
        outcome: BatchOutcome,
        stop_reason: str | None = None,
    ) -> None:
        """记录一次调度决策（研究日志；不含恢复明文）。"""
        if state.research is None:
            raise RuntimeError("research logging must start before batch execution")
        reason = stop_reason or (
            "cancelled" if state.cancel_requested or outcome.status == TaskStatus.CANCELLED else
            "execution_failed" if outcome.status == TaskStatus.FAILED else
            state.scheduler.stop_reason(_next_batch_sizes(state.strategies))
        )
        logged = state.research.complete(
            outcome, updated_state=state.scheduler.snapshot_statistics(),
            stop_reason=reason,
        )
        state.research_previous_arm = item.strategy_id
        event = DecisionEvent(
            run_id=state.run_id,
            round_index=state.round_index,
            arm_id=item.strategy_id,
            strategy_id=item.strategy_id,
            candidate_limit=decision.candidate_limit,
            time_limit=decision.time_limit,
            exploration=decision.exploration,
            scores=prior_scores,
            score_breakdown=decision.score,
            prior_state=prior_state,
            feedback=replace(outcome, message=""),
            reward=logged["reward"],
            stop_reason=reason,
        )
        with self._lock:
            state.decision_events.append(event)
            if len(state.decision_events) > 200:
                del state.decision_events[:-200]

    def _begin_research(self, state: _RunState) -> None:
        if state.research is not None:
            return
        with self._lock:
            if self._research_log is None:
                self._research_log = SqliteResearchLog(
                    self.settings.upload_dir.parent / "research" / "real.sqlite3"
                )
        weights = self.reward_weights
        with self.session_factory() as session:
            record = RunRecordRepository(session).get(state.run_id)
            if record is not None:
                if record.snapshot.get("research_reward_weights"):
                    weights = RewardWeights(**record.snapshot["research_reward_weights"])
                else:
                    # Freeze defaults on first research-enabled resume of an old run.
                    record.snapshot = {**record.snapshot, "research_reward_weights": asdict(weights)}
                    RunRecordRepository(session).save(record)
        scheduler = state.scheduler
        names = {
            "BanditScheduler": "heuristic_bandit", "FixedOrderScheduler": "fixed",
            "RoundRobinScheduler": "round_robin",
        }
        parameters = {"exploration_rounds": scheduler.exploration_rounds}
        if hasattr(scheduler, "weights"):
            parameters.update(asdict(scheduler.weights))
        policy_name = names.get(type(scheduler).__name__, self.settings.scheduler_type.value)
        if isinstance(scheduler, UCBScheduler):
            policy_name = "cost_aware_ucb" if scheduler.cost_aware else "ucb"
            parameters = asdict(scheduler.config)
        state.research = ResearchRecorder(
            run_id=state.run_id,
            policy_type=policy_name,
            context=RewardContext(
                len(set(state.targets)), scheduler.total_candidate_budget,
                scheduler.total_time_budget,
            ),
            weights=weights, store=self._research_log, mode="real",
            resume_from_round=state.round_index,
            previous_arm_id=state.research_previous_arm, policy_parameters=parameters,
        )

    # ------------------------------------------------------------------ 持久化
    def _checkpoint(self, state: _RunState) -> None:
        """每个批次完成后写一次进度检查点（崩溃后据此续跑）。"""
        try:
            with self.session_factory() as session:
                record = RunRecordRepository(session).get(state.run_id)
                if record is None:
                    return
                record.progress = _serialize_progress(state)
                record.status = (
                    TaskStatus.PAUSED.value
                    if self.control and self.control.is_paused(state.run_id)
                    else TaskStatus.RUNNING.value
                )
                record.updated_at = now_iso()
                RunRecordRepository(session).save(record)
            if state.research is not None:
                state.research.checkpoint()
        except Exception as exc:  # pragma: no cover - 检查点失败不应中断执行
            state.persist_error = f"检查点写库失败：{exc}"

    def _set_record_status(self, state: _RunState, status: str) -> None:
        try:
            with self.session_factory() as session:
                record = RunRecordRepository(session).get(state.run_id)
                if record is None:
                    return
                record.status = status
                record.updated_at = now_iso()
                RunRecordRepository(session).save(record)
        except Exception:  # pragma: no cover - 记录状态失败不阻塞控制流
            return

    # ------------------------------------------------------------------ 恢复
    def recover_after_restart(self) -> set[str]:
        """启动时扫描持久化的 running/paused 真实运行记录并自动续跑。

        返回被续跑的任务 id 集合；无法恢复的记录与对应任务/策略行收尾为
        failed。Mock 运行没有记录，仍由 service.finalize_interrupted_tasks
        按原逻辑收尾（调用方需排除本方法返回的任务）。
        """
        resumed_task_ids: set[str] = set()
        with self.session_factory() as session:
            stale = RunRecordRepository(session).list_stale()
        for record in stale:
            try:
                self._resume_record(
                    record.run_id,
                    record.task_id,
                    record.snapshot,
                    record.progress,
                )
                resumed_task_ids.add(record.task_id)
            except Exception as exc:
                self._fail_stale_record(
                    record.run_id, record.task_id, f"恢复失败：{exc}"
                )
        return resumed_task_ids

    def _resume_record(
        self,
        run_id: str,
        task_id: str,
        snapshot: dict[str, Any],
        progress: dict[str, Any],
    ) -> None:
        strategies = _state_from_snapshot(snapshot, progress)
        with self.session_factory() as session:
            existing = StrategyRunRepository(session).list_by_run_id(run_id)
            if not existing:
                rows = [_to_run_row(run_id, task_id, item) for item in strategies]
                StrategyRunRepository(session).add_many(rows)
            else:
                record = RunRecordRepository(session).get(run_id)
                if record is not None:
                    record.status = TaskStatus.RUNNING.value
                    record.updated_at = now_iso()
                    RunRecordRepository(session).save(record)
        started_at = progress.get("started_at") or now_iso()
        scheduler = _scheduler_from_snapshot(
            snapshot, progress, strategies
        )
        state = _RunState(
            run_id=run_id,
            task_id=task_id,
            targets=tuple(snapshot.get("targets", ())),
            hash_mode=int(snapshot.get("hash_mode", 0)),
            strategies=strategies,
            started_at=started_at,
            expected_candidates=int(
                snapshot.get("expected_candidates", 0)
            ),
            scheduler=scheduler,
            stop_on_hit=bool(snapshot.get("stop_on_hit", False)),
            round_index=int(progress.get(
                "round_index", sum(item.consumed_batches for item in strategies)
            )),
            research_previous_arm=progress.get("research_previous_arm"),
        )
        with self._lock:
            self._registry[run_id] = state
            self._task_runs.setdefault(task_id, set()).add(run_id)
        thread = threading.Thread(
            target=self._run_worker,
            args=(run_id,),
            name=f"sage-real-{run_id}-recover",
            daemon=True,
        )
        state.thread = thread
        thread.start()

    def _fail_stale_record(self, run_id: str, task_id: str, reason: str) -> None:
        timestamp = now_iso()
        with self.session_factory() as session:
            rows = StrategyRunRepository(session).list_by_run_id(run_id)
            for row in rows:
                if row.status in (
                    TaskStatus.RUNNING.value,
                    TaskStatus.PAUSED.value,
                ):
                    row.status = TaskStatus.FAILED.value
                    row.finished_at = timestamp
            StrategyRunRepository(session).save_all(rows)
            record = RunRecordRepository(session).get(run_id)
            if record is not None:
                record.status = TaskStatus.FAILED.value
                record.message = reason
                record.finished_at = timestamp
                record.updated_at = timestamp
                RunRecordRepository(session).save(record)
            task = TaskRepository(session).get(task_id)
            if task is not None and TaskStatus(task.status) in (
                TaskStatus.RUNNING,
                TaskStatus.PAUSED,
            ):
                update_task_status(session, task, TaskStatus.FAILED)

    def _complete_scheduled_execution(
        self,
        state: _RunState,
        reason: SchedulerStopReason | None,
        *,
        label: str | None = None,
    ) -> None:
        state.research_stop_reason = (
            label or (reason.value if reason is not None else "completed")
        )
        finished_at = now_iso()
        for item in state.strategies:
            if item.status != TaskStatus.RUNNING.value:
                continue
            item.status = TaskStatus.COMPLETED.value
            item.finished_at = finished_at
            if not item.candidate_batches and item.native_wordlist_path is None:
                item.message = "该策略未生成候选，无需执行"
            elif label == "all_targets_recovered":
                item.message = (
                    f"命中即停：已恢复 {item.recovered} 项，停止后续候选"
                )
            elif reason == SchedulerStopReason.TIME_BUDGET:
                item.message = "已达到任务时间预算"
            elif reason == SchedulerStopReason.CANDIDATE_BUDGET:
                item.message = "已达到任务候选预算"
            else:
                item.message = (
                    f"策略调度结束，共测试 {item.tested} 个候选，"
                    f"恢复 {item.recovered} 项"
                )

    def _skip_remaining(
        self, state: _RunState, status: TaskStatus, message: str
    ) -> None:
        for item in state.strategies:
            if item.status != TaskStatus.RUNNING.value:
                continue
            item.status = status.value
            item.finished_at = now_iso()
            item.message = message

    def _accumulate_result(
        self, item: _StrategyState, result: HashcatResult
    ) -> int:
        item.tested += result.tested
        recovered_before = len(item.recovered_items)
        recovered_seen = {
            (credential.target, credential.plaintext)
            for credential in item.recovered_items
        }
        for credential in result.recovered:
            key = (credential.target, credential.plaintext)
            if key in recovered_seen:
                continue
            recovered_seen.add(key)
            item.recovered_items.append(credential)
        item.recovered = len(item.recovered_items)
        item.exit_code = result.exit_code
        item.message = result.message
        return item.recovered - recovered_before

    def _finalize_run(self, state: _RunState) -> None:
        with self._lock:
            state.finished_at = now_iso()
            state.finished = True
            if state.cancel_requested:
                state.status = TaskStatus.CANCELLED
                state.message = "执行已停止"
            elif state.failed_launch:
                state.status = TaskStatus.FAILED
                state.message = state.failed_launch
            else:
                state.status = TaskStatus.COMPLETED
                state.message = "真实执行已完成"

    def _persist(self, state: _RunState) -> None:
        # 等待路由先把任务置为 running，避免极快的执行把终态写回在
        # running 之前（此时直接跳过终态更新，路由侧随后负责置为 running）。
        deadline = time.monotonic() + 3.0
        while True:
            with self.session_factory() as session:
                task = TaskRepository(session).get(state.task_id)
                if task is not None:
                    current = TaskStatus(task.status)
                    if current == TaskStatus.RUNNING:
                        break
                    if current in {
                        TaskStatus.COMPLETED,
                        TaskStatus.FAILED,
                        TaskStatus.CANCELLED,
                    }:
                        return
            if time.monotonic() > deadline:
                break
            time.sleep(0.01)
        with self.session_factory() as session:
            rows = StrategyRunRepository(session).list_by_run_id(state.run_id)
            by_strategy = {row.strategy_id: row for row in rows}
            for item in state.strategies:
                row = by_strategy.get(item.strategy_id)
                if row is None:
                    continue
                row.status = item.status
                row.tested = item.tested
                row.recovered = item.recovered
                row.started_at = item.started_at or row.started_at
                row.finished_at = item.finished_at or row.finished_at
            StrategyRunRepository(session).save_all(rows)
            task = TaskRepository(session).get(state.task_id)
            if task is not None:
                current = TaskStatus(task.status)
                if (
                    current == TaskStatus.RUNNING
                    and state.status != TaskStatus.RUNNING
                ):
                    update_task_status(session, task, state.status)
            record = RunRecordRepository(session).get(state.run_id)
            if record is not None:
                record.status = state.status.value
                record.message = state.message
                record.finished_at = state.finished_at or now_iso()
                record.progress = _serialize_progress(state)
                record.updated_at = now_iso()
                RunRecordRepository(session).save(record)

    def _status_message(
        self,
        state: _RunState,
        current: str | None,
        paused: bool = False,
        inflight: bool = False,
    ) -> str:
        if paused:
            return "真实执行已暂停（批次边界），继续后将从下一批候选恢复"
        if (
            state.status == TaskStatus.RUNNING
            and inflight
            and self.control
            and self.control.is_paused(state.run_id)
        ):
            return "已受理暂停请求，等待当前批次结束后暂停"
        if state.status != TaskStatus.RUNNING:
            if state.status == TaskStatus.CANCELLED:
                return "真实执行已停止"
            if state.status == TaskStatus.FAILED:
                return state.message or "真实执行失败"
            return state.message or "真实执行已完成"
        if current is None:
            return "真实执行已启动，等待 Hashcat 返回"
        running = next(
            (
                item
                for item in state.strategies
                if item.strategy_id == current
            ),
            None,
        )
        name = running.strategy_name if running else current
        return f"正在执行策略 {name}（{current}）"

    def _paused_wait_cancelled(self, state: _RunState) -> bool:
        """在批次边界等待暂停解除；被取消时返回 True，由调用方收尾。"""
        persisted_paused = False
        while True:
            with self._lock:
                cancelled = state.cancel_requested
            if cancelled:
                return True
            paused = bool(
                self.control and self.control.is_paused(state.run_id)
            )
            if paused != persisted_paused:
                self._set_record_status(
                    state,
                    TaskStatus.PAUSED.value if paused else TaskStatus.RUNNING.value,
                )
                persisted_paused = paused
            if not paused:
                return False
            time.sleep(0.1)

    def _require(self, run_id: str) -> _RunState:
        with self._lock:
            state = self._registry.get(run_id)
        if state is None:
            raise AppError(
                "EXECUTION_FAILED",
                "执行不存在",
                status_code=404,
                details={"run_id": run_id},
            )
        return state

    def _request_cancel(self, run_id: str) -> bool:
        with self._lock:
            state = self._registry.get(run_id)
            if state is None or state.finished or state.cancel_requested:
                return False
            state.cancel_requested = True
            handles = [
                item.handle
                for item in state.strategies
                if item.handle is not None
            ]
        for handle in handles:
            handle.stop()
        return True


def _serialize_snapshot(
    *,
    plan: StrategyPlan,
    payload: ExecutionRequest,
    targets: tuple[str, ...],
    hash_mode: int,
    batches_by_strategy: dict[str, list[CandidateBatch]],
    expected: int,
    total_candidate_budget: int,
    transfer_scores: dict[str, float | None] | None = None,
    scheduler_type: str = SchedulerType.HEURISTIC_BANDIT.value,
    stop_on_hit: bool = False,
) -> dict[str, Any]:
    """启动时写入运行记录的静态快照（目标、计划、候选批次等）。"""
    return {
        "schema_version": RUN_SNAPSHOT_SCHEMA_VERSION,
        "targets": list(targets),
        "hash_mode": hash_mode,
        "expected_candidates": expected,
        "timeout_override": payload.timeout,
        "stop_on_hit": bool(stop_on_hit),
        "scheduler_type": scheduler_type,
        "plan": {
            "total_time_budget": plan.total_time_budget,
            "total_candidate_budget": total_candidate_budget,
            "strategies": [
                {
                    "strategy_id": strategy.strategy_id.value,
                    "strategy_name": strategy.strategy_name,
                    "priority": strategy.priority,
                    "time_budget": strategy.time_budget,
                    "candidate_budget": strategy.candidate_budget,
                    "parameters": dict(strategy.parameters),
                    "generator_id": (
                        batches_by_strategy[strategy.strategy_id.value][0].generator_id
                        if batches_by_strategy.get(strategy.strategy_id.value)
                        and batches_by_strategy[strategy.strategy_id.value][0].generator_id
                        else STRATEGY_GENERATOR_IDS[strategy.strategy_id]
                    ),
                    "transfer_score": (transfer_scores or {}).get(
                        strategy.strategy_id.value
                    ),
                }
                for strategy in plan.strategies
            ],
        },
        "batches": {
            strategy_id: [
                list(batch.candidates) for batch in batches
            ]
            for strategy_id, batches in batches_by_strategy.items()
        },
    }


def _initial_progress(
    snapshot: dict[str, Any], started_at: str
) -> dict[str, Any]:
    return {
        "started_at": started_at,
        "expected_candidates": int(snapshot.get("expected_candidates", 0)),
        "strategies": {
            entry["strategy_id"]: {
                "consumed": 0,
                "tested": 0,
                "recovered": 0,
                "time_cost": 0.0,
                "recovered_items": [],
            }
            for entry in snapshot["plan"]["strategies"]
        },
        "scheduler_stats": {},
    }


def _serialize_progress(state: _RunState) -> dict[str, Any]:
    scheduler_stats: dict[str, Any] = {}
    if state.scheduler is not None:
        scheduler_stats = state.scheduler.snapshot_statistics()
    return {
        "started_at": state.started_at,
        "expected_candidates": state.expected_candidates,
        "strategies": {
            item.strategy_id: {
                "consumed": item.consumed_batches,
                "tested": item.tested,
                "recovered": item.recovered,
                "time_cost": item.time_cost,
                "recovered_items": [
                    [credential.target, credential.plaintext]
                    for credential in item.recovered_items
                ],
            }
            for item in state.strategies
        },
        "scheduler_stats": scheduler_stats,
        "scheduler_snapshot": state.scheduler.snapshot() if state.scheduler is not None else None,
        "round_index": state.round_index,
        "research_previous_arm": state.research_previous_arm,
        "research_log_version": 1,
        "research_attempt_id": state.research.attempt_id if state.research else None,
        "decision_events": [
            event.as_dict() for event in state.decision_events[-50:]
        ],
    }


def _state_from_snapshot(
    snapshot: dict[str, Any], progress: dict[str, Any]
) -> list[_StrategyState]:
    """按持久化快照重建策略状态；已消费批次之前的内容不再执行。"""
    _validate_snapshot_version(snapshot)
    entries = snapshot["plan"]["strategies"]
    batches = snapshot.get("batches", {})
    strategy_progress = progress.get("strategies", {})
    states: list[_StrategyState] = []
    for entry in entries:
        strategy_id = entry["strategy_id"]
        item_progress = strategy_progress.get(strategy_id, {})
        consumed = int(item_progress.get("consumed", 0))
        all_batches = batches.get(strategy_id, [])
        remaining = tuple(
            tuple(candidates) for candidates in all_batches[consumed:]
        )
        recovered_items = [
            RecoveredCredential(target=target, plaintext=plaintext)
            for target, plaintext in item_progress.get(
                "recovered_items", []
            )
        ]
        states.append(
            _StrategyState(
                strategy_id=strategy_id,
                strategy_name=entry.get("strategy_name", strategy_id),
                priority=entry["priority"],
                time_budget=entry["time_budget"],
                candidate_budget=entry["candidate_budget"],
                parameters=dict(entry.get("parameters", {})),
                generator_id=entry.get(
                    "generator_id",
                    STRATEGY_GENERATOR_IDS[StrategyId(strategy_id)],
                ),
                candidate_batches=remaining,
                tested=int(item_progress.get("tested", 0)),
                recovered=len(recovered_items),
                recovered_items=recovered_items,
                time_cost=float(item_progress.get("time_cost", 0.0)),
                consumed_batches=consumed,
                scheduled_batches=consumed,
                transfer_score=entry.get("transfer_score"),
            )
        )
    return states


def _validate_snapshot_version(snapshot: dict[str, Any]) -> int:
    """Accept legacy unversioned/v1 records and the registry-backed v2."""
    raw = snapshot.get("schema_version", 1)
    try:
        version = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("运行快照 schema_version 无效") from exc
    if version not in {1, RUN_SNAPSHOT_SCHEMA_VERSION}:
        raise ValueError(
            f"不支持的运行快照版本 {version}；"
            f"当前支持 1 和 {RUN_SNAPSHOT_SCHEMA_VERSION}"
        )
    return version


def _scheduler_from_snapshot(
    snapshot: dict[str, Any],
    progress: dict[str, Any],
    strategies: list[_StrategyState],
) -> Any:
    timeout = snapshot.get("timeout_override")
    arms = [
        ArmSpec(
            strategy_id=item.strategy_id,
            priority=item.priority,
            candidate_budget=item.candidate_budget,
            time_budget=float(
                max(
                    1,
                    timeout if timeout is not None else item.time_budget,
                )
            ),
            transfer_score=item.transfer_score,
        )
        for item in strategies
    ]
    plan_meta = snapshot["plan"]
    scheduler_type = SchedulerType(
        snapshot.get("scheduler_type", SchedulerType.HEURISTIC_BANDIT.value)
    )
    scheduler = build_scheduler(
        scheduler_type,
        arms,
        total_candidate_budget=int(
            plan_meta.get(
                "total_candidate_budget",
                sum(item.candidate_budget for item in arms),
            )
        ),
        total_time_budget=float(plan_meta["total_time_budget"]),
        initial_targets=len(set(snapshot["targets"])),
        ucb_config=UCBConfig(**snapshot.get("ucb_config", {})),
    )
    full_snapshot = progress.get("scheduler_snapshot")
    if full_snapshot is not None:
        scheduler.restore(full_snapshot)
    else:
        stats = progress.get("scheduler_stats") or {}
        if isinstance(scheduler, UCBScheduler) and (stats or any(item.consumed_batches for item in strategies)):
            raise ValueError("UCB resume requires a full scheduler snapshot")
        if stats:
            scheduler.restore_statistics(stats)
    return scheduler


def _validate_record_payload(
    record: RunRecordModel,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """校验持久化记录可用；损坏/旧格式给出明确错误而不是静默降级。"""
    snapshot = record.snapshot if isinstance(record.snapshot, dict) else None
    progress = record.progress if isinstance(record.progress, dict) else None
    plan_entries = (
        snapshot.get("plan", {}).get("strategies")
        if isinstance(snapshot, dict)
        else None
    )
    if (
        not snapshot
        or not progress
        or not isinstance(plan_entries, list)
        or not isinstance(progress.get("strategies"), dict)
    ):
        raise AppError(
            "EXECUTION_FAILED",
            "运行记录损坏或不兼容，无法恢复真实结果",
            status_code=409,
            details={"run_id": record.run_id},
        )
    return snapshot, progress


def _record_elapsed(record: RunRecordModel) -> float:
    if not record.started_at:
        return 0.0
    return _elapsed_seconds(record.started_at, record.finished_at)


def _record_default_message(status: TaskStatus) -> str:
    if status == TaskStatus.COMPLETED:
        return "真实执行已完成"
    if status == TaskStatus.CANCELLED:
        return "真实执行已停止"
    if status == TaskStatus.FAILED:
        return "真实执行失败"
    if status == TaskStatus.PAUSED:
        return "真实执行已暂停"
    return "真实执行中"


def _to_run_row(run_id: str, task_id: str, item: _StrategyState) -> Any:
    from .models import StrategyRunModel

    return StrategyRunModel(
        run_id=run_id,
        task_id=task_id,
        strategy_id=item.strategy_id,
        strategy_name=item.strategy_name,
        priority=item.priority,
        time_budget=item.time_budget,
        candidate_budget=item.candidate_budget,
        parameters=item.parameters,
        status=item.status,
        tested=0,
        recovered=0,
        started_at=item.started_at,
    )


def _elapsed_seconds(started_at: str | None, finished_at: str | None) -> float:
    if started_at is None:
        return 0.0
    started = datetime.fromisoformat(started_at)
    end = (
        datetime.fromisoformat(finished_at)
        if finished_at is not None
        else datetime.fromisoformat(now_iso())
    )
    return max(0.0, (end - started).total_seconds())


def _strategy_result_time(item: _StrategyState) -> float:
    return item.time_cost


def _next_batch_sizes(
    strategies: list[_StrategyState],
) -> dict[str, int]:
    return {
        item.strategy_id: (
            item.candidate_budget
            if (
                item.native_wordlist_path is not None
                and item.native_pending
            )
            else 0
        )
        if item.native_wordlist_path is not None
        else (
            len(item.candidate_batches[item.next_batch_index])
            if item.next_batch_index < len(item.candidate_batches)
            else 0
        )
        for item in strategies
    }
