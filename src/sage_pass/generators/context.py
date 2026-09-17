from __future__ import annotations

from collections.abc import Iterator

from ..candidate_types import CandidateRecord
from ..context import iter_context_candidates
from ..enums import StrategyId
from .base import GeneratorPrepareRequest, GeneratorState, ReplayableGenerator
from .personalized import restore_context, serialize_context


class ContextGenerator(ReplayableGenerator):
    generator_id = "context"
    strategy_id = StrategyId.S4

    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState:
        context = request.task_context
        return self._new_state(
            request,
            restore_data={
                "task_context": serialize_context(context),
            },
        )

    def _iter_records(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        context = restore_context(state.restore_data.get("task_context", {}))
        if context is None:
            return
        yield from iter_context_candidates(
            context,
            parameters=dict(state.parameters),
        )
