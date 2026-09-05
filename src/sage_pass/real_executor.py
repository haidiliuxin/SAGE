from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

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
    candidates: tuple[str, ...] = ()
    status: str = TaskStatus.RUNNING.value
    started_at: str | None = None
    finished_at: str | None = None
    tested: int = 0
    recovered: int = 0
    recovered_items: list[RecoveredCredential] = field(default_factory=list)
    message: str = ""
    exit_code: int | None = None
    handle: HashcatHandle | None = None


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
    timeout_override: int | None = None
    cancel_requested: bool = False
    finished: bool = False
    failed_launch: str | None = None
    persist_error: str | None = None
    thread: threading.Thread | None = None


class RealExecutor:
    """基于 Hashcat 的真实执行器。

    以计划中的每个策略为一次独立的 Hashcat 任务（带各自的候选切片与时间
    预算），后台线程依次启动/轮询/回收；对外通过 run registry 提供
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
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.hashcat = hashcat or HashcatAdapter(settings.hashcat_path)
        self.zip_extractor = zip_extractor or ZipHashExtractor(
            settings.zip2john_path
        )
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
            current = next(
                (
                    item.strategy_id
                    for item in state.strategies
                    if item.status == TaskStatus.RUNNING.value
                ),
                None,
            )
            message = self._status_message(state, current)
        elapsed = _elapsed_seconds(state.started_at, state.finished_at)
        progress = (
            min(1.0, tested / state.expected_candidates)
            if state.expected_candidates > 0
            else 1.0
        )
        return RunStatus(
            task_id=state.task_id,
            run_id=run_id,
            status=state.status,
            progress=progress,
            current_strategy=current,
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
        if not payload.candidates:
            raise AppError(
                "EXECUTION_FAILED",
                "真实执行候选集为空，请先提供 candidates",
                status_code=422,
                details={"task_id": task.task_id},
            )
        targets, hash_mode = self._resolve_targets(
            session, task, payload.hashcat_mode
        )
        started_at = now_iso()
        run_id = public_id("R")
        pool = list(payload.candidates)
        strategies: list[_StrategyState] = []
        expected = 0
        for item in plan.strategies:
            take = min(max(item.candidate_budget, 0), len(pool))
            slice_candidates = tuple(pool[:take])
            pool = pool[take:]
            expected += len(slice_candidates)
            strategies.append(
                _StrategyState(
                    strategy_id=item.strategy_id.value,
                    strategy_name=item.strategy_name,
                    priority=item.priority,
                    time_budget=item.time_budget,
                    candidate_budget=item.candidate_budget,
                    parameters=item.parameters,
                    candidates=slice_candidates,
                    started_at=started_at,
                )
            )
        if expected == 0:
            raise AppError(
                "EXECUTION_FAILED",
                "候选分配结果为空，无法启动真实执行",
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
            timeout_override=payload.timeout,
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
        for item in state.strategies:
            if state.cancel_requested:
                self._skip_remaining(state, TaskStatus.CANCELLED, "执行已停止")
                return
            if not item.candidates:
                item.status = TaskStatus.COMPLETED.value
                item.tested = 0
                item.finished_at = now_iso()
                item.message = "该策略未分配到候选，无需执行"
                continue
            try:
                handle = self.hashcat.start(
                    HashcatJob(
                        run_id=f"{state.run_id}-{item.strategy_id}",
                        target_hashes=state.targets,
                        hash_mode=state.hash_mode,
                        candidates=item.candidates,
                        timeout_seconds=self._strategy_runtime(state, item),
                        candidate_budget=len(item.candidates),
                    )
                )
            except AppError as exc:
                item.status = TaskStatus.FAILED.value
                item.finished_at = now_iso()
                item.message = exc.message
                state.failed_launch = exc.message
                return
            item.handle = handle
            item.started_at = now_iso()
            with self._lock:
                cancel_requested = state.cancel_requested
            if cancel_requested:
                # 启动与取消几乎同时发生时，立即停止刚启动的进程。
                result = handle.stop()
                item.handle = None
                self._apply_result(item, result)
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
            self._apply_result(item, result)
            if state.cancel_requested:
                self._skip_remaining(state, TaskStatus.CANCELLED, "执行已停止")
                return
        if state.failed_launch is None:
            state.failed_launch = ""

    def _skip_remaining(
        self, state: _RunState, status: TaskStatus, message: str
    ) -> None:
        for item in state.strategies:
            if item.status != TaskStatus.RUNNING.value:
                continue
            item.status = status.value
            item.finished_at = now_iso()
            item.message = message

    def _apply_result(self, item: _StrategyState, result: HashcatResult) -> None:
        item.status = result.status.value
        item.tested = result.tested
        item.recovered = len(result.recovered)
        item.recovered_items = list(result.recovered)
        item.finished_at = now_iso()
        item.exit_code = result.exit_code
        item.message = result.message

    def _strategy_runtime(self, state: _RunState, item: _StrategyState) -> float:
        if state.timeout_override is not None:
            return float(max(1, state.timeout_override))
        return float(max(1, item.time_budget))

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
        self, state: _RunState, current: str | None
    ) -> str:
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
    if item.started_at is None:
        return 0.0
    end = item.finished_at or item.started_at
    return _elapsed_seconds(item.started_at, end)
