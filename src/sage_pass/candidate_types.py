from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from .enums import StrategyId


CandidateSourceKind: TypeAlias = Literal[
    "baseline",
    "supplied",
    "rule",
    "pcfg_template",
    "keyword",
    "pinyin",
    "abbreviation",
    "year",
    "region",
    "organization",
    "combination",
    "transfer_pattern",
]


@dataclass(frozen=True, slots=True)
class CandidateSource:
    kind: CandidateSourceKind
    original: str | None = None
    normalized: str | None = None
    template: str | None = None
    probability: float | None = None
    components: tuple[str, ...] = ()
    pattern_id: int | None = None
    pattern_signature: str | None = None
    pattern_confidence: float | None = None


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    value: str
    strategy_id: StrategyId
    sources: tuple[CandidateSource, ...]
