from __future__ import annotations

from collections.abc import Iterator

from ..candidate_types import CandidateRecord, CandidateSource
from ..enums import StrategyId
from .base import GeneratorPrepareRequest, GeneratorState, ReplayableGenerator


S2_PARAMETER_NAMES = frozenset({
    "capitalize_first", "all_upper", "all_lower", "common_number_suffix",
    "year_suffix", "common_substitution", "symbol_suffix",
})
HASHCAT_MASK_PARAMETER = "hashcat_masks"
COMMON_SUBSTITUTIONS = str.maketrans({
    "a": "@", "A": "@", "e": "3", "E": "3", "i": "1", "I": "1",
    "o": "0", "O": "0", "s": "5", "S": "5",
})


class RuleGenerator(ReplayableGenerator):
    generator_id = "rule"
    strategy_id = StrategyId.S2

    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState:
        masks = _validated_masks(request.parameters.get(HASHCAT_MASK_PARAMETER))
        return self._new_state(
            request,
            restore_data={
                "seeds": list(request.rule_seeds),
                "number_suffixes": list(request.number_suffixes),
                "year_suffixes": list(request.rule_year_suffixes),
                "symbol_suffixes": list(request.symbol_suffixes),
                "hashcat_masks": list(masks),
            },
        )

    def _iter_records(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        masks = tuple(
            str(item) for item in state.restore_data.get("hashcat_masks", [])
        )
        if masks:
            for mask in masks:
                yield CandidateRecord(
                    mask,
                    StrategyId.S2,
                    (CandidateSource(kind="hashcat_mask", template=mask),),
                )
            return

        enabled = {
            name
            for name in S2_PARAMETER_NAMES
            if state.parameters.get(name) is True
        }
        seeds = tuple(str(item) for item in state.restore_data.get("seeds", []))
        suffix_groups = (
            (
                "common_number_suffix",
                tuple(str(item) for item in state.restore_data.get("number_suffixes", [])),
            ),
            (
                "year_suffix",
                tuple(str(item) for item in state.restore_data.get("year_suffixes", [])),
            ),
            (
                "symbol_suffix",
                tuple(str(item) for item in state.restore_data.get("symbol_suffixes", [])),
            ),
        )
        for seed in seeds:
            direct: list[tuple[str, str]] = []
            if "capitalize_first" in enabled:
                direct.append((seed[:1].upper() + seed[1:], "capitalize_first"))
            if "all_upper" in enabled:
                direct.append((seed.upper(), "all_upper"))
            if "all_lower" in enabled:
                direct.append((seed.lower(), "all_lower"))
            if "common_substitution" in enabled:
                direct.append((
                    seed.translate(COMMON_SUBSTITUTIONS),
                    "common_substitution",
                ))

            stems = tuple(dict.fromkeys((seed, *(value for value, _ in direct))))
            for value, rule in direct:
                if value != seed:
                    yield _rule_record(value, seed, rule)
            for rule, suffixes in suffix_groups:
                if rule not in enabled:
                    continue
                for stem in stems:
                    for suffix in suffixes:
                        yield _rule_record(f"{stem}{suffix}", seed, rule)


def _rule_record(value: str, seed: str, rule: str) -> CandidateRecord:
    return CandidateRecord(
        value,
        StrategyId.S2,
        (CandidateSource(
            kind="rule",
            original=seed,
            normalized=value,
            components=(rule,),
        ),),
    )


def _validated_masks(value: object) -> tuple[str, ...]:
    if value is None or value is False:
        return ()
    if isinstance(value, str):
        raw = (value,)
    elif isinstance(value, list | tuple):
        raw = tuple(value)
    else:
        raise ValueError("hashcat_masks 必须为字符串或字符串列表")
    masks: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item or "\n" in item or "\r" in item:
            raise ValueError(f"hashcat_masks[{index}] 必须为非空单行文本")
        masks.append(item)
    return tuple(dict.fromkeys(masks))
