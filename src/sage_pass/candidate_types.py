from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from .enums import StrategyId


CandidateSourceKind: TypeAlias = Literal[
    "baseline",
    "supplied",
    "rule",
    "pcfg_template",
    "pcfg_full",
    "markov",
    "keyword",
    "pinyin",
    "abbreviation",
    "year",
    "region",
    "organization",
    "historical_password",
    "historical_structure",
    "personalized_combination",
    "combination",
    "transfer_pattern",
    "hashcat_mask",
    "hashcat_rule",
    "hashcat_hybrid",
]


@dataclass(frozen=True, slots=True)
class CandidateSource:
    kind: CandidateSourceKind
    original: str | None = None
    normalized: str | None = None
    template: str | None = None
    probability: float | None = None
    log_probability: float | None = None
    score: float | None = None
    components: tuple[str, ...] = ()
    pattern_id: int | None = None
    pattern_signature: str | None = None
    pattern_confidence: float | None = None

    def public_dict(self) -> dict[str, object]:
        """Return provenance safe for UI/audit display.

        Historical-password provenance is structural only even if a caller
        accidentally populated ``original`` or ``normalized``.
        """
        if self.kind == "historical_password":
            return {
                "kind": self.kind,
                "original": None,
                "normalized": None,
                "template": self.template,
                "components": list(self.components),
            }
        return {
            "kind": self.kind,
            "original": self.original,
            "normalized": self.normalized,
            "template": self.template,
            "probability": self.probability,
            "log_probability": self.log_probability,
            "score": self.score,
            "components": list(self.components),
            "pattern_id": self.pattern_id,
            "pattern_signature": self.pattern_signature,
            "pattern_confidence": self.pattern_confidence,
        }


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    value: str
    strategy_id: StrategyId
    sources: tuple[CandidateSource, ...]


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    """One validated generator/pipeline batch.

    The first three fields preserve the historical construction contract used
    by tests and injected candidate pipelines. Registry-backed batches also set
    generator_id, exhausted, and snapshot.
    """

    strategy_id: StrategyId
    candidates: tuple[str, ...]
    records: tuple[CandidateRecord, ...] = ()
    generator_id: str = ""
    exhausted: bool = False
    snapshot: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.records and self.candidates:
            object.__setattr__(
                self,
                "records",
                tuple(
                    CandidateRecord(value, self.strategy_id, ())
                    for value in self.candidates
                ),
            )
        if len(self.records) != len(self.candidates):
            raise ValueError("records 与 candidates 数量必须一致")
        if tuple(item.value for item in self.records) != self.candidates:
            raise ValueError("records 与 candidates 的值和顺序必须一致")
        if any(
            item.strategy_id != self.strategy_id for item in self.records
        ):
            raise ValueError("records 的 strategy_id 必须与批次一致")
