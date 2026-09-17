from __future__ import annotations

from collections.abc import Iterator

from ..candidate_types import CandidateRecord
from ..enums import StrategyId
from ..pcfg_lite import (
    DEFAULT_MAX_STRUCTURE_LENGTH,
    DEFAULT_MAX_TEMPLATES,
    DEFAULT_MIN_PROBABILITY,
    iter_pcfg_candidates,
)
from .base import GeneratorPrepareRequest, GeneratorState, ReplayableGenerator


class PCFGLiteGenerator(ReplayableGenerator):
    generator_id = "pcfg_lite"
    strategy_id = StrategyId.S3
    model_version = "pcfg-lite-v1"

    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState:
        return self._new_state(
            request,
            restore_data={
                "seeds": list(request.pcfg_seeds),
                "years": list(request.year_suffixes),
                "numbers": list(request.number_suffixes),
                "symbols": list(request.symbol_suffixes),
            },
        )

    def _iter_records(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        yield from iter_pcfg_candidates(
            (str(item) for item in state.restore_data.get("seeds", [])),
            years=tuple(str(item) for item in state.restore_data.get("years", [])),
            numbers=tuple(str(item) for item in state.restore_data.get("numbers", [])),
            symbols=tuple(str(item) for item in state.restore_data.get("symbols", [])),
            max_templates=int(
                state.parameters.get("max_templates", DEFAULT_MAX_TEMPLATES)
            ),
            min_probability=float(
                state.parameters.get("min_probability", DEFAULT_MIN_PROBABILITY)
            ),
            max_structure_length=int(
                state.parameters.get(
                    "max_structure_length", DEFAULT_MAX_STRUCTURE_LENGTH
                )
            ),
        )
