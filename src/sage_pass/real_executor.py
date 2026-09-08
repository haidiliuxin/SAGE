from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .candidate_generator import CandidateBatch, CandidateGenerator
from .candidate_types import CandidateRecord
from .config import Settings
from .enums import ExecutionMode, TargetType, TaskStatus
from .errors import AppError
from .hashcat_adapter import (
    HashcatAdapter,
    HashcatHandle,
    HashcatJob,
    HashcatResult,
    RecoveredCredential,
    resolve_hashcat_mode,
)
from .repository import FileRepository, StrategyRunRepository, TaskRepository
from .run_control import RunControl
from .scheduler import (
    ArmSpec,
    BanditScheduler,
    SchedulerStopReason,
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
from .zip_adapter import ZipHashExtractor

ZIP_EXTRACTION_TIMEOUT = 30.0


@dataclass
class _StrategyState:
    strategy_id: str
    strategy_name: str
    priority: int
    time_budget: int
    candidate_budget: int
    parameters: dict[str, Any]
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
    time_cost: float = 0.0


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
    scheduler: BanditScheduler | None = None
    current_strategy_id: str | None = None


class RealExecutor:
    """基于 Hashcat 的真实执行器。

    后端按计划生成候选批次，并在每个策略的共享时间预算内依次交给 Hashcat
    执行；后台线程负责启动/轮询/回收，对外通过 run registry 提供
    status/result，并向数据库回写策略运行行与任务终态。运行状态保存在进程
    内存中（第二周范围），持久化与异常恢复属于第三周任务。
    """

    def __init__(
        self,
        *,
        session_factory,
        settings: Settings,
        hashcat: HashcatAdapter | None = None,
        zip_extractor: ZipHashExtractor | None = None,
        candidate_generator: CandidateGenerator | None = None,
        control: RunControl | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.hashcat = hashcat or HashcatAdapter(settings.hashcat_path)
        self.zip_extractor = zip_extractor or ZipHashExtractor(
            settings.zip2john_path
        )
        self.candidate_generator = candidate_generator or CandidateGenerator()
        self.control = control
        self._registry: dict[str, _RunState] = {}
        self._task_runs: dict[str, set[str]] = {}
        self._lock = threading.RLock()

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
        try:
            for batch in self.candidate_generator.iter_plan_batches(
                plan,
                supplied_candidates=payload.candidates,
                task_context=task.context,
            ):
                batches_by_strategy.setdefault(
                    batch.strategy_id.value, []
                ).append(batch)
        except ValueError as exc:
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
                    candidate_batches=candidate_batches,
                    candidate_record_batches=candidate_record_batches,
                )
            )
        if expected == 0:
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

        state = _RunState(
            run_id=run_id,
            task_id=task.task_id,
            targets=targets,
            hash_mode=hash_mode,
            strategies=strategies,
            started_at=started_at,
            expected_candidates=expected,
            scheduler=BanditScheduler(
                [
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
                    )
                    for item in strategies
                ],
                total_candidate_budget=task.candidate_budget,
                total_time_budget=float(plan.total_time_budget),
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
    def _resolve_targets(
        self, session: Session, task: TaskDetail, explicit_mode: int | None
    ) -> tuple[tuple[str, ...], int]:
        target_type = task.target.type
        if target_type == TargetType.HASH:
            lines = [
                line.strip()
                for line in (task.target.content or "").splitlines()
                if line.strip()
            ]
            if not lines:
                raise AppError(
                    "EXECUTION_FAILED",
                    "Hash 目标缺少内容",
                    status_code=422,
                    details={"task_id": task.task_id},
                )
            mode = explicit_mode
            if mode is None and task.known_algorithm:
                mode = resolve_hashcat_mode(task.known_algorithm)
            if mode is None:
                raise AppError(
                    "EXECUTION_FAILED",
                    "无法确定 Hashcat 模式，请提供 hashcat_mode 或 known_algorithm",
                    status_code=422,
                    details={"task_id": task.task_id},
                )
            return tuple(lines), mode

        if target_type == TargetType.ZIP:
            if not task.target.file_id:
                raise AppError(
                    "EXECUTION_FAILED",
                    "ZIP 目标缺少 file_id",
                    status_code=422,
                    details={"task_id": task.task_id},
                )
            file_item = FileRepository(session).get(task.target.file_id)
            if file_item is None:
                raise AppError(
                    "EXECUTION_FAILED",
                    "ZIP 目标文件不存在",
                    status_code=422,
                    details={"file_id": task.target.file_id},
                )
            archive = self.settings.upload_dir / file_item.stored_name
            extracted = self.zip_extractor.extract(
                archive, timeout=ZIP_EXTRACTION_TIMEOUT
            )
            if not extracted.hashes:
                raise AppError(
                    "EXECUTION_FAILED",
                    "ZIP 中未提取到受支持的加密目标",
                    status_code=422,
                    details={"file_id": task.target.file_id},
                )
            mode = explicit_mode or extracted.hashcat_mode
            return extracted.hashes, mode

        raise AppError(
            "EXECUTION_FAILED",
            f"{target_type.value} 目标类型暂未接入真实执行，本周仅支持 hash 与 ZIP",
            status_code=422,
            details={"task_id": task.task_id, "target_type": target_type.value},
        )

    def _run_worker(self, run_id: str) -> None:
        state = self._registry[run_id]
        try:
            self._execute_strategies(state)
        except AppError as exc:
            state.failed_launch = exc.message
        except Exception as exc:  # pragma: no cover - 防御未知异常
            state.failed_launch = f"内部错误：{exc}"
        finally:
            self._finalize_run(state)
            try:
                self._persist(state)
            except Exception as exc:  # pragma: no cover - 回写失败不阻塞结果返回
                state.persist_error = f"回写数据库失败：{exc}"

    def _execute_strategies(self, state: _RunState) -> None:
        scheduler = state.scheduler
        if scheduler is None:  # pragma: no cover - start() always installs one
            raise RuntimeError("Bandit scheduler is unavailable")
        by_strategy = {item.strategy_id: item for item in state.strategies}

        while True:
            if self._paused_wait_cancelled(state):
                self._skip_remaining(state, TaskStatus.CANCELLED, "执行已停止")
                return
            if state.cancel_requested:
                self._skip_remaining(state, TaskStatus.CANCELLED, "执行已停止")
                return
            next_batch_sizes = _next_batch_sizes(state.strategies)
            decision = scheduler.select(next_batch_sizes)
            if decision is None:
                self._complete_scheduled_execution(
                    state, scheduler.stop_reason(next_batch_sizes)
                )
                state.failed_launch = ""
                return

            item = by_strategy[decision.strategy_id]
            source_batch = item.candidate_batches[item.next_batch_index]
            candidates = source_batch[: decision.candidate_limit]
            item.next_batch_index += 1
            item.scheduled_batches += 1
            item.started_at = item.started_at or now_iso()
            with self._lock:
                state.current_strategy_id = item.strategy_id
            try:
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
                return
            item.handle = handle
            with self._lock:
                cancel_requested = state.cancel_requested
            if cancel_requested:
                result = handle.stop()
                item.handle = None
                with self._lock:
                    state.current_strategy_id = None
                self._record_batch_result(
                    scheduler, item, candidates, result
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
                return
            finally:
                item.handle = None
                with self._lock:
                    state.current_strategy_id = None
            self._record_batch_result(scheduler, item, candidates, result)
            if result.status == TaskStatus.FAILED:
                item.status = TaskStatus.FAILED.value
                item.finished_at = now_iso()
                state.failed_launch = result.message
                return
            if state.cancel_requested:
                self._skip_remaining(state, TaskStatus.CANCELLED, "执行已停止")
                return

    def _record_batch_result(
        self,
        scheduler: BanditScheduler,
        item: _StrategyState,
        candidates: tuple[str, ...],
        result: HashcatResult,
    ) -> None:
        recovered = self._accumulate_result(item, result)
        item.time_cost += result.duration
        scheduler.observe(
            item.strategy_id,
            candidate_count=len(candidates),
            tested=result.tested,
            recovered=recovered,
            duration=result.duration,
        )

    def _complete_scheduled_execution(
        self,
        state: _RunState,
        reason: SchedulerStopReason | None,
    ) -> None:
        finished_at = now_iso()
        for item in state.strategies:
            if item.status != TaskStatus.RUNNING.value:
                continue
            item.status = TaskStatus.COMPLETED.value
            item.finished_at = finished_at
            if not item.candidate_batches:
                item.message = "该策略未生成候选，无需执行"
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
            if task is None:
                return
            current = TaskStatus(task.status)
            if current == TaskStatus.RUNNING and state.status != TaskStatus.RUNNING:
                update_task_status(session, task, state.status)

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
        while True:
            with self._lock:
                cancelled = state.cancel_requested
            if cancelled:
                return True
            if not (
                self.control and self.control.is_paused(state.run_id)
            ):
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
            len(item.candidate_batches[item.next_batch_index])
            if item.next_batch_index < len(item.candidate_batches)
            else 0
        )
        for item in strategies
    }
