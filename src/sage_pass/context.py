from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator

from pypinyin import Style, lazy_pinyin

from .candidate_types import CandidateRecord, CandidateSource, CandidateSourceKind
from .enums import StrategyId
from .schemas import TaskContext


_CHINESE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_ORGANIZATION_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "有限公司",
    "大学",
    "学院",
    "集团",
    "公司",
)
DEFAULT_CONTEXT_PARAMETERS: dict[str, object] = {
    "use_keywords": True,
    "use_pinyin": True,
    "use_abbreviations": True,
    "use_years": True,
    "use_region": True,
    "use_organization": True,
    "max_combinations": 100_000,
}


def normalize_keyword(value: str) -> str:
    """Normalize a context term while rejecting multiline input."""
    if not isinstance(value, str):
        raise TypeError("上下文词必须是字符串")
    if "\n" in value or "\r" in value:
        raise ValueError("上下文词必须是单行文本")
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = "".join(normalized.split())
    return normalized.strip(".,;:!?，。；：！？、'\"")


def to_pinyin(value: str) -> str:
    normalized = normalize_keyword(value)
    if not normalized:
        return ""
    return "".join(
        lazy_pinyin(normalized, style=Style.NORMAL, errors=lambda text: list(text))
    ).casefold()


def abbreviate(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        return ""
    if _CHINESE_RE.search(normalized):
        return "".join(
            lazy_pinyin(
                normalized,
                style=Style.FIRST_LETTER,
                errors=lambda text: list(text),
            )
        ).casefold()
    words = _WORD_RE.findall(normalized)
    return "".join(word[0] for word in words).casefold() if len(words) > 1 else ""


def iter_context_candidates(
    task_context: TaskContext | dict[str, object],
    *,
    parameters: dict[str, object] | None = None,
) -> Iterator[CandidateRecord]:
    """Generate stable context candidates with per-candidate provenance."""
    context = (
        task_context
        if isinstance(task_context, TaskContext)
        else TaskContext.model_validate(task_context)
    )
    options = DEFAULT_CONTEXT_PARAMETERS | (parameters or {})
    limit = int(options["max_combinations"])
    if limit <= 0:
        return

    bases: list[tuple[str, CandidateSource]] = []
    if options["use_keywords"]:
        for original in context.keywords:
            _append_term(bases, "keyword", original)
    if options["use_region"] and context.region:
        _append_term(bases, "region", context.region)
    if options["use_organization"] and context.organization:
        _append_term(bases, "organization", context.organization)
        shortened = _strip_organization_suffix(context.organization)
        if shortened != context.organization:
            _append_term(bases, "organization", shortened)

    derived: list[tuple[str, CandidateSource]] = []
    if options["use_pinyin"]:
        for value, source in bases:
            converted = to_pinyin(source.original or value)
            if converted and converted != value:
                derived.append((converted, CandidateSource(
                    kind="pinyin",
                    original=source.original,
                    normalized=converted,
                )))
    if options["use_abbreviations"]:
        for value, source in bases:
            short = abbreviate(source.original or value)
            if short and short != value:
                derived.append((short, CandidateSource(
                    kind="abbreviation",
                    original=source.original,
                    normalized=short,
                )))

    years = [
        (str(year), CandidateSource(
            kind="year", original=str(year), normalized=str(year)
        ))
        for year in context.years
    ] if options["use_years"] else []

    emitted: dict[str, CandidateRecord] = {}
    for value, source in (*bases, *derived, *years):
        _remember(emitted, value, (source,), limit=limit)
        if len(emitted) >= limit:
            break
    if len(emitted) >= limit:
        yield from emitted.values()
        return
    for value, source in (*bases, *derived):
        for year, year_source in years:
            combined = f"{value}{year}"
            _remember(emitted, combined, (
                source,
                year_source,
                CandidateSource(
                    kind="combination",
                    normalized=combined,
                    components=(
                        f"{source.kind}:{source.normalized}",
                        f"year:{year}",
                    ),
                ),
            ), limit=limit)
            if len(emitted) >= limit:
                break
        if len(emitted) >= limit:
            break
    yield from emitted.values()


def _append_term(
    target: list[tuple[str, CandidateSource]],
    kind: CandidateSourceKind,
    original: str,
) -> None:
    normalized = normalize_keyword(original)
    if normalized:
        target.append((normalized, CandidateSource(
            kind=kind,
            original=original,
            normalized=normalized,
        )))


def _strip_organization_suffix(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    for suffix in _ORGANIZATION_SUFFIXES:
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _remember(
    target: dict[str, CandidateRecord],
    value: str,
    sources: Iterable[CandidateSource],
    *,
    limit: int,
) -> None:
    if not value or len(value) > 1024 or "\n" in value or "\r" in value:
        return
    prepared = tuple(sources)
    previous = target.get(value)
    if previous is None:
        if len(target) >= limit:
            return
        target[value] = CandidateRecord(value, StrategyId.S4, prepared)
        return
    merged = tuple(dict.fromkeys((*previous.sources, *prepared)))
    target[value] = CandidateRecord(value, StrategyId.S4, merged)
