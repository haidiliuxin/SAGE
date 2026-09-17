from __future__ import annotations

from collections.abc import Iterator

from ..candidate_types import CandidateRecord, CandidateSource
from ..context import iter_context_candidates
from ..enums import StrategyId
from ..transfer import TransferCandidateGenerator
from .base import GeneratorPrepareRequest, GeneratorState, ReplayableGenerator
from .personalized import (
    apply_shape,
    context_roots,
    historical_shape,
    iter_history_records,
    restore_context,
    serialize_context,
    stable_records,
)
from .transfer import pattern_snapshots, restore_patterns


class HybridGenerator(ReplayableGenerator):
    generator_id = "hybrid"
    strategy_id = StrategyId.S4
    model_version = "hybrid-v1"

    def __init__(self, transfer_generator: TransferCandidateGenerator | None = None) -> None:
        self._pattern_generator = transfer_generator or TransferCandidateGenerator()

    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState:
        return self._new_state(request, restore_data={
            "task_context": serialize_context(request.task_context),
            "historical_passwords": list(request.historical_passwords),
            "patterns": pattern_snapshots(request.transfer_patterns),
            "years": list(request.personalized_years or request.year_suffixes),
            "numbers": list(request.number_suffixes),
            "symbols": list(request.symbol_suffixes),
        })

    def _iter_records(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        context = restore_context(state.restore_data.get("task_context", {}))
        passwords = tuple(str(item) for item in state.restore_data.get("historical_passwords", []))
        years = tuple(str(item) for item in state.restore_data.get("years", []))
        numbers = tuple(str(item) for item in state.restore_data.get("numbers", []))
        symbols = tuple(str(item) for item in state.restore_data.get("symbols", []))
        roots = context_roots(context)

        def records() -> Iterator[CandidateRecord]:
            if context is not None:
                yield from iter_context_candidates(context, parameters=dict(state.parameters))
            yield from iter_history_records(
                passwords, years=years, numbers=numbers, symbols=symbols
            )
            current_year = years[0] if years else ""
            for root, root_source in roots:
                if current_year:
                    value = f"{root}{current_year}"
                    yield CandidateRecord(value, StrategyId.S4, (
                        root_source,
                        CandidateSource(kind="year", original=current_year, normalized=current_year),
                        CandidateSource(kind="personalized_combination", template="root+current-year"),
                    ))
                for password in passwords:
                    shape = historical_shape(password)
                    value = apply_shape(
                        root,
                        shape,
                        current_year=current_year,
                        number_fallback=numbers[0] if numbers else "",
                        symbol_fallback=symbols[0] if symbols else "",
                    )
                    yield CandidateRecord(value, StrategyId.S4, (
                        root_source,
                        CandidateSource(
                            kind="historical_structure",
                            template=shape.signature,
                            components=("masked-history-structure",),
                        ),
                        CandidateSource(kind="personalized_combination", template="personal-root+history-shape"),
                    ))
            patterns = restore_patterns(state.restore_data.get("patterns", []))
            pattern_records = self._pattern_generator.iter_records(
                patterns,
                seeds=tuple(root for root, _ in roots),
                years=years,
                numbers=numbers,
                symbols=symbols,
            )
            for record in pattern_records:
                yield CandidateRecord(record.value, StrategyId.S4, record.sources)

        yield from stable_records(records())
