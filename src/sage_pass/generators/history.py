from __future__ import annotations

from collections.abc import Iterator

from ..candidate_types import CandidateRecord
from ..enums import StrategyId
from .base import GeneratorPrepareRequest, GeneratorState, ReplayableGenerator
from .personalized import iter_history_records


class HistoryGenerator(ReplayableGenerator):
    generator_id = "history"
    strategy_id = StrategyId.S4
    model_version = "history-v1"

    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState:
        return self._new_state(request, restore_data={
            "historical_passwords": list(request.historical_passwords),
            "years": list(request.personalized_years or request.year_suffixes),
            "numbers": list(request.number_suffixes),
            "symbols": list(request.symbol_suffixes),
        })

    def _iter_records(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        yield from iter_history_records(
            tuple(str(item) for item in state.restore_data.get("historical_passwords", [])),
            years=tuple(str(item) for item in state.restore_data.get("years", [])),
            numbers=tuple(str(item) for item in state.restore_data.get("numbers", [])),
            symbols=tuple(str(item) for item in state.restore_data.get("symbols", [])),
        )
