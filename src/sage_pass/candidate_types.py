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
]


@dataclass(frozen=True, slots=True)
class CandidateSource:
    kind: CandidateSourceKind
    original: str | None = None
    normalized: str | None = None
    template: str | None = None
    probability: float | None = None
    components: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    value: str
    strategy_id: StrategyId
    sources: tuple[CandidateSource, ...]
