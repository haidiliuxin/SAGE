"""进程内运行控制原语：暂停/继续的耗时记账。

Mock 与 Real 执行共享同一个 RunControl（挂在 app.state 上）：
- pause/resume 记录每个 run 的累计暂停秒数；
- is_paused 让轮询端把 run 状态展示为 paused；
- offset_seconds 把暂停时间从“已耗时”中扣除，使暂停期间进度冻结。

说明：本控制只负责“冻结计时”，真正的调度/恢复语义由各执行器按批次边界
消费；进程重启后的状态收尾见 service.finalize_interrupted_tasks。
"""

from __future__ import annotations

import threading
import time
from typing import Iterable


class RunControl:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        # run_id -> (累计暂停秒数 acc, 本次暂停起始墙钟秒 since | None)
        self._pauses: dict[str, tuple[float, float | None]] = {}

    def pause(self, run_id: str) -> None:
        with self._lock:
            acc, since = self._pauses.get(run_id, (0.0, None))
            if since is None:
                self._pauses[run_id] = (acc, time.time())

    def pause_many(self, run_ids: Iterable[str]) -> None:
        for run_id in tuple(run_ids):
            self.pause(run_id)

    def resume(self, run_id: str) -> None:
        with self._lock:
            entry = self._pauses.get(run_id)
            if entry is None:
                return
            acc, since = entry
            if since is not None:
                acc += max(0.0, time.time() - since)
            self._pauses[run_id] = (acc, None)

    def resume_many(self, run_ids: Iterable[str]) -> None:
        for run_id in tuple(run_ids):
            self.resume(run_id)

    def is_paused(self, run_id: str) -> bool:
        with self._lock:
            entry = self._pauses.get(run_id)
            return entry is not None and entry[1] is not None

    def offset_seconds(self, run_id: str) -> float:
        """应从未暂停总耗时中扣除的秒数（暂停期间仍持续累计以冻结进度）。"""
        with self._lock:
            entry = self._pauses.get(run_id)
            if entry is None:
                return 0.0
            acc, since = entry
            if since is None:
                return acc
            return acc + max(0.0, time.time() - since)

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._pauses.pop(run_id, None)

    def clear_many(self, run_ids: Iterable[str]) -> None:
        with self._lock:
            for run_id in tuple(run_ids):
                self._pauses.pop(run_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._pauses.clear()
