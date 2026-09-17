"""原生攻击单元的占位生成器。

S6（掩码/暴力）与 S7（混合攻击）的候选完全由 hashcat 自己枚举（`-a 3` / `-a 6`），
后端不展开明文空间，因此这里的生成器不产出任何 Python 候选——它只是让调度器
能够把这两个单元纳入候选流与预算记账。
"""

from __future__ import annotations

from collections.abc import Iterator

from ..candidate_types import CandidateRecord
from ..enums import StrategyId
from .base import GeneratorPrepareRequest, GeneratorState, ReplayableGenerator


class NativeOnlyGenerator(ReplayableGenerator):
    """不产出候选的原生攻击单元占位实现。"""

    model_version = "native-only-v1"

    def __init__(self, generator_id: str, strategy_id: StrategyId) -> None:
        self.generator_id = generator_id
        self.strategy_id = strategy_id

    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState:
        return self._new_state(request, restore_data={})

    def _iter_records(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        return iter(())
