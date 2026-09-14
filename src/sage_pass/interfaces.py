"""A 冻结的公共接口（两周冲刺第 1 天约定）。

本模块集中导出跨人协作的四个公共结构，避免 B/C 各自定义造成漂移：

- `ArmSpec`：调度臂（Scheduler 只面向 Arm，不面向业务策略）；
- `CandidateBatch`：候选批次（Generator 的唯一输出单位）；
- `BatchOutcome`：单批执行反馈（Executor -> Scheduler/Feedback）；
- `InformationProfile`：信息条件（C 生成、Planner 只读脱敏摘要）；
- `DecisionEvent`：一次调度决策日志（研究日志，不含恢复明文）。

约定：
- `arm_id` 与 `strategy_id` 分离：当前实现中一个 Arm 对应一个策略，故 `arm_id`
  默认等于 `strategy_id`，但下游只应依赖 `arm_id`；
- `Strategy` 表示业务策略（S1~S5），`Generator` 表示具体算法实现，
  `Scheduler` 只面向 Arm，`Executor` 只接收候选并返回 `BatchOutcome`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .candidate_generator import CandidateBatch
from .enums import TaskStatus
from .scheduler import ArmSpec, ScoreBreakdown

__all__ = [
    "ArmSpec",
    "BatchOutcome",
    "CandidateBatch",
    "DecisionEvent",
    "DecisionPolicy",
    "InformationProfile",
    "ScoreBreakdown",
]


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    """一批候选在真实执行后的反馈（Executor 的唯一反馈结构）。"""

    run_id: str
    arm_id: str
    strategy_id: str
    batch_index: int
    candidate_count: int
    tested: int
    recovered: int
    duration: float
    status: TaskStatus
    exit_code: int | None = None
    message: str = ""

    @property
    def success_rate(self) -> float:
        return self.recovered / self.tested if self.tested > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "arm_id": self.arm_id,
            "strategy_id": self.strategy_id,
            "batch_index": self.batch_index,
            "candidate_count": self.candidate_count,
            "tested": self.tested,
            "recovered": self.recovered,
            "duration": self.duration,
            "status": getattr(self.status, "value", self.status),
            "exit_code": self.exit_code,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class InformationProfile:
    """信息条件画像（C 负责生成；Planner 只读取脱敏字段）。

    只允许暴露聚合/脱敏信息：不包含个人信息原文、历史口令原文，
    也不包含恢复明文。
    """

    has_personal_info: bool = False
    info_types: tuple[str, ...] = ()
    has_history: bool = False
    history_count: int = 0
    has_pattern_knowledge: bool = False
    masked_structure_summary: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "has_personal_info": self.has_personal_info,
            "info_types": list(self.info_types),
            "has_history": self.has_history,
            "history_count": self.history_count,
            "has_pattern_knowledge": self.has_pattern_knowledge,
            "masked_structure_summary": self.masked_structure_summary,
        }


@dataclass(frozen=True, slots=True)
class DecisionEvent:
    """一次调度决策（研究日志结构，按 B 的规范冻结字段）。

    不记录恢复明文；`scores` 为决策前各 Arm 的评分分解快照。
    """

    run_id: str
    round_index: int
    arm_id: str
    strategy_id: str
    candidate_limit: int
    time_limit: float
    exploration: bool
    scores: Mapping[str, float] = field(default_factory=dict)
    score_breakdown: ScoreBreakdown | None = None
    prior_state: Mapping[str, Any] = field(default_factory=dict)
    feedback: BatchOutcome | None = None
    reward: float | None = None
    stop_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "round_index": self.round_index,
            "arm_id": self.arm_id,
            "strategy_id": self.strategy_id,
            "candidate_limit": self.candidate_limit,
            "time_limit": self.time_limit,
            "exploration": self.exploration,
            "scores": dict(self.scores),
            "prior_state": dict(self.prior_state),
            "feedback": self.feedback.as_dict() if self.feedback else None,
            "reward": self.reward,
            "stop_reason": self.stop_reason,
        }


class DecisionPolicy(Protocol):
    """B 负责实现的可替换调度接口（A 只依赖该协议接线）。

    以构造函数注入 `arms` 与总预算完成初始化；以下为规范接口，
    当前 `BanditScheduler`、`FixedOrderScheduler`、`RoundRobinScheduler`
    均已实现（`observe_outcome` / `snapshot` / `restore` 为规范别名）。
    """

    def select(self, next_batch_sizes: Mapping[str, int]): ...

    def observe_outcome(self, arm_id: str, outcome: BatchOutcome) -> None: ...

    def stop_reason(self, next_batch_sizes: Mapping[str, int]) -> str | None: ...

    def snapshot(self) -> dict[str, Any]: ...

    def restore(self, snapshot: Mapping[str, Any]) -> None: ...
