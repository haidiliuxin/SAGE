from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from itertools import islice
from typing import Any, Protocol, runtime_checkable

from ..candidate_types import CandidateBatch, CandidateRecord
from ..enums import StrategyId
from ..schemas import TaskContext
from ..transfer import PatternLike


JSONScalar = str | int | float | bool | None
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
GeneratorSnapshot = dict[str, JSONValue]


class GeneratorStateError(ValueError):
    """Raised when generator state or a persisted snapshot is invalid."""


@dataclass(frozen=True, slots=True)
class GeneratorPrepareRequest:
    """All inputs a currently implemented generator may need.

    The request is run-local. Registry instances never retain it.
    """

    strategy_id: StrategyId
    parameters: Mapping[str, object]
    supplied_candidates: tuple[str, ...] = ()
    baseline_candidates: tuple[str, ...] = ()
    rule_seeds: tuple[str, ...] = ()
    pcfg_seeds: tuple[str, ...] = ()
    task_context: TaskContext | None = None
    historical_passwords: tuple[str, ...] = ()
    transfer_patterns: tuple[PatternLike, ...] = ()
    transfer_seeds: tuple[str, ...] = ()
    number_suffixes: tuple[str, ...] = ()
    rule_year_suffixes: tuple[str, ...] = ()
    year_suffixes: tuple[str, ...] = ()
    personalized_years: tuple[str, ...] = ()
    symbol_suffixes: tuple[str, ...] = ()


@dataclass(slots=True)
class GeneratorState:
    """Run-local cursor and deterministic replay data for one generator."""

    generator_id: str
    strategy_id: StrategyId
    parameters: dict[str, JSONValue]
    restore_data: dict[str, JSONValue]
    cursor: int = 0
    generated_count: int = 0
    exhausted: bool = False
    model_version: str | None = None
    _iterator: Iterator[CandidateRecord] | None = field(
        default=None, repr=False, compare=False
    )
    _pending: CandidateRecord | None = field(
        default=None, repr=False, compare=False
    )


@runtime_checkable
class GeneratorProtocol(Protocol):
    @property
    def generator_id(self) -> str: ...

    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState: ...

    def next_batch(
        self, state: GeneratorState, limit: int
    ) -> CandidateBatch: ...

    def exhausted(self, state: GeneratorState) -> bool: ...

    def snapshot(self, state: GeneratorState) -> GeneratorSnapshot: ...

    def restore(self, snapshot: Mapping[str, object]) -> GeneratorState: ...


class ReplayableGenerator(ABC):
    """Stateless generator adapter with cursor-based deterministic restore."""

    generator_id: str
    strategy_id: StrategyId
    model_version: str | None = "1"

    @abstractmethod
    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState:
        raise NotImplementedError

    @abstractmethod
    def _iter_records(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        raise NotImplementedError

    def next_batch(
        self, state: GeneratorState, limit: int
    ) -> CandidateBatch:
        self._validate_state(state)
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        if state.exhausted:
            return CandidateBatch(
                strategy_id=state.strategy_id,
                candidates=(),
                records=(),
                generator_id=self.generator_id,
                exhausted=True,
                snapshot=self.snapshot(state),
            )
        iterator = self._ensure_iterator(state)
        records: list[CandidateRecord] = []
        if state._pending is not None:
            records.append(state._pending)
            state._pending = None
        while len(records) < limit:
            try:
                records.append(next(iterator))
            except StopIteration:
                state.exhausted = True
                break

        if len(records) == limit and not state.exhausted:
            try:
                state._pending = next(iterator)
            except StopIteration:
                state.exhausted = True

        state.cursor += len(records)
        state.generated_count = state.cursor
        prepared = tuple(records)
        return CandidateBatch(
            strategy_id=state.strategy_id,
            candidates=tuple(item.value for item in prepared),
            records=prepared,
            generator_id=self.generator_id,
            exhausted=state.exhausted,
            snapshot=self.snapshot(state),
        )

    def exhausted(self, state: GeneratorState) -> bool:
        self._validate_state(state)
        return state.exhausted

    def snapshot(self, state: GeneratorState) -> GeneratorSnapshot:
        self._validate_state(state)
        return {
            "schema_version": 1,
            "generator_id": state.generator_id,
            "strategy_id": state.strategy_id.value,
            "cursor": state.cursor,
            "generated_count": state.generated_count,
            "exhausted": state.exhausted,
            "parameters": state.parameters,
            "restore_data": state.restore_data,
            "model_version": state.model_version,
        }

    def restore(self, snapshot: Mapping[str, object]) -> GeneratorState:
        try:
            schema_version = int(snapshot.get("schema_version", 0))
        except (TypeError, ValueError) as exc:
            raise GeneratorStateError("GeneratorState 快照版本无效") from exc
        if schema_version != 1:
            raise GeneratorStateError("不支持的 GeneratorState 快照版本")
        if snapshot.get("generator_id") != self.generator_id:
            raise GeneratorStateError(
                f"快照属于生成器 {snapshot.get('generator_id')!r}，"
                f"不能由 {self.generator_id!r} 恢复"
            )
        try:
            state = GeneratorState(
                generator_id=self.generator_id,
                strategy_id=StrategyId(str(snapshot["strategy_id"])),
                parameters=_json_dict(snapshot.get("parameters", {})),
                restore_data=_json_dict(snapshot.get("restore_data", {})),
                cursor=int(snapshot.get("cursor", 0)),
                generated_count=int(snapshot.get("generated_count", 0)),
                exhausted=bool(snapshot.get("exhausted", False)),
                model_version=(
                    str(snapshot["model_version"])
                    if snapshot.get("model_version") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GeneratorStateError("GeneratorState 快照字段无效") from exc
        if state.cursor < 0 or state.generated_count < 0:
            raise GeneratorStateError("GeneratorState 游标不能为负数")
        if state.strategy_id != self.strategy_id:
            raise GeneratorStateError(
                f"生成器 {self.generator_id!r} 不支持策略 "
                f"{state.strategy_id.value}"
            )
        if state.generated_count != state.cursor:
            raise GeneratorStateError("GeneratorState 计数与游标不一致")
        if state.model_version != self.model_version:
            raise GeneratorStateError(
                f"生成器模型版本不兼容：快照={state.model_version!r}，"
                f"当前={self.model_version!r}"
            )
        iterator = self._iter_records(state)
        skipped = sum(1 for _ in islice(iterator, state.cursor))
        if skipped != state.cursor:
            raise GeneratorStateError("GeneratorState 游标超出候选序列")
        if state.exhausted:
            try:
                next(iterator)
            except StopIteration:
                pass
            else:
                raise GeneratorStateError("GeneratorState 耗尽标志与游标不一致")
        else:
            state._iterator = iterator
        return state

    def _new_state(
        self,
        request: GeneratorPrepareRequest,
        *,
        restore_data: dict[str, JSONValue],
        model_version: str | None = None,
    ) -> GeneratorState:
        if request.strategy_id != self.strategy_id:
            raise GeneratorStateError(
                f"生成器 {self.generator_id!r} 只支持策略 "
                f"{self.strategy_id.value}，收到 {request.strategy_id.value}"
            )
        state = GeneratorState(
            generator_id=self.generator_id,
            strategy_id=request.strategy_id,
            parameters=_json_dict(dict(request.parameters)),
            restore_data=restore_data,
            model_version=model_version or self.model_version,
        )
        state._iterator = self._iter_records(state)
        return state

    def _ensure_iterator(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        if state._iterator is None:
            state._iterator = self._iter_records(state)
        return state._iterator

    def _validate_state(self, state: GeneratorState) -> None:
        if state.generator_id != self.generator_id:
            raise GeneratorStateError(
                f"状态属于生成器 {state.generator_id!r}，"
                f"不能由 {self.generator_id!r} 使用"
            )


def _json_dict(value: object) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        raise GeneratorStateError("GeneratorState 对象字段必须为字典")
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise GeneratorStateError(
        f"GeneratorState 包含不可 JSON 序列化的值：{type(value).__name__}"
    )
