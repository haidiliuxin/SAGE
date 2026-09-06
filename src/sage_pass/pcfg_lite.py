from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import product

from .candidate_types import CandidateRecord, CandidateSource
from .enums import StrategyId


@dataclass(frozen=True, slots=True)
class PCFGTemplate:
    name: str
    structure: tuple[str, ...]
    probability: float


# Small and auditable by design. The probabilities sum to one.
PCFG_TEMPLATES: tuple[PCFGTemplate, ...] = (
    PCFGTemplate("W", ("W",), 0.260),
    PCFGTemplate("WY", ("W", "Y"), 0.200),
    PCFGTemplate("WD", ("W", "D"), 0.160),
    PCFGTemplate("C", ("C",), 0.100),
    PCFGTemplate("CY", ("C", "Y"), 0.090),
    PCFGTemplate("CD", ("C", "D"), 0.070),
    PCFGTemplate("WS", ("W", "S"), 0.040),
    PCFGTemplate("CS", ("C", "S"), 0.030),
    PCFGTemplate("WYS", ("W", "Y", "S"), 0.025),
    PCFGTemplate("WDS", ("W", "D", "S"), 0.015),
    PCFGTemplate("DW", ("D", "W"), 0.010),
)

DEFAULT_MAX_TEMPLATES = len(PCFG_TEMPLATES)
DEFAULT_MIN_PROBABILITY = 0.0
DEFAULT_MAX_STRUCTURE_LENGTH = 32


def ranked_templates(
    *,
    max_templates: int = DEFAULT_MAX_TEMPLATES,
    min_probability: float = DEFAULT_MIN_PROBABILITY,
) -> tuple[PCFGTemplate, ...]:
    """Return templates in stable descending probability order."""
    if max_templates <= 0:
        return ()
    eligible = (
        item for item in PCFG_TEMPLATES
        if item.probability >= min_probability
    )
    return tuple(sorted(eligible, key=lambda item: -item.probability))[
        :max_templates
    ]


def iter_pcfg_candidates(
    seeds: Iterable[str],
    *,
    years: Sequence[str],
    numbers: Sequence[str],
    symbols: Sequence[str],
    max_templates: int = DEFAULT_MAX_TEMPLATES,
    min_probability: float = DEFAULT_MIN_PROBABILITY,
    max_structure_length: int = DEFAULT_MAX_STRUCTURE_LENGTH,
) -> Iterator[CandidateRecord]:
    """Lazily expand the finite grammar into real candidate strings."""
    prepared_seeds = tuple(dict.fromkeys(seeds))
    segment_values = {
        "W": prepared_seeds,
        "C": tuple(seed[:1].upper() + seed[1:] for seed in prepared_seeds),
        "Y": tuple(dict.fromkeys(years)),
        "D": tuple(dict.fromkeys(numbers)),
        "S": tuple(dict.fromkeys(symbols)),
    }
    for template in ranked_templates(
        max_templates=max_templates,
        min_probability=min_probability,
    ):
        pools = tuple(segment_values[token] for token in template.structure)
        if any(not pool for pool in pools):
            continue
        for parts in product(*pools):
            value = "".join(parts)
            if not value or len(value) > max_structure_length:
                continue
            components = tuple(
                f"{token}:{part}"
                for token, part in zip(template.structure, parts)
            )
            yield CandidateRecord(
                value=value,
                strategy_id=StrategyId.S3,
                sources=(
                    CandidateSource(
                        kind="pcfg_template",
                        original=next(
                            (
                                part
                                for token, part in zip(template.structure, parts)
                                if token in {"W", "C"}
                            ),
                            None,
                        ),
                        normalized=value,
                        template=template.name,
                        probability=template.probability,
                        components=components,
                    ),
                ),
            )
