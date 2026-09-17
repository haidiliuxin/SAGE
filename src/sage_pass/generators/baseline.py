from __future__ import annotations

from collections.abc import Iterator

from ..candidate_types import CandidateRecord, CandidateSource
from ..enums import StrategyId
from .base import GeneratorPrepareRequest, GeneratorState, ReplayableGenerator


class BaselineGenerator(ReplayableGenerator):
    generator_id = "baseline"
    strategy_id = StrategyId.S1

    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState:
        values = tuple(dict.fromkeys(
            (*request.supplied_candidates, *request.baseline_candidates)
        ))
        return self._new_state(
            request,
            restore_data={
                "values": list(values),
                "supplied": list(dict.fromkeys(request.supplied_candidates)),
            },
        )

    def _iter_records(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        values = tuple(str(item) for item in state.restore_data.get("values", []))
        supplied = {
            str(item) for item in state.restore_data.get("supplied", [])
        }
        for value in values:
            yield CandidateRecord(
                value,
                StrategyId.S1,
                (CandidateSource(
                    kind="supplied" if value in supplied else "baseline",
                    original=value,
                    normalized=value,
                ),),
            )
