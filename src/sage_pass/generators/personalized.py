from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from ..candidate_types import CandidateRecord, CandidateSource
from ..context import abbreviate, normalize_keyword, to_pinyin
from ..enums import StrategyId
from ..information import masked_historical_source
from ..schemas import TaskContext


_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_ALPHA_RE = re.compile(r"[A-Za-z]+")
_EDGE_RE = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")
_TRAILING_DIGITS_RE = re.compile(r"\d+$")
_LEET_TO_ALPHA = str.maketrans({"@": "a", "3": "e", "1": "i", "0": "o", "$": "s", "5": "s", "7": "t"})
_SUBSTITUTIONS = (("a", "@"), ("e", "3"), ("i", "1"), ("o", "0"), ("s", "$"))


@dataclass(frozen=True, slots=True)
class HistoricalShape:
    has_year: bool
    digit_prefix: str
    digit_suffix: str
    symbol_prefix: str
    symbol_suffix: str
    case_style: str

    @property
    def signature(self) -> str:
        parts = [self.case_style]
        if self.symbol_prefix:
            parts.append("symbol-prefix")
        if self.digit_prefix:
            parts.append(f"digit-prefix-{len(self.digit_prefix)}")
        if self.has_year:
            parts.append("year4")
        elif self.digit_suffix:
            parts.append(f"digit-suffix-{len(self.digit_suffix)}")
        if self.symbol_suffix:
            parts.append("symbol-suffix")
        return "+".join(parts)


def context_roots(context: TaskContext | None) -> tuple[tuple[str, CandidateSource], ...]:
    if context is None:
        return ()
    terms: list[tuple[str, str]] = []
    for value in (
        context.name,
        context.nickname,
        context.username,
        context.email_local_part,
        context.phone_suffix,
        context.birthday,
        *context.interest_words,
        *context.authorized_keywords,
        *context.keywords,
    ):
        if value:
            terms.append(("keyword", str(value)))
    if context.region:
        terms.append(("region", context.region))
    if context.organization:
        terms.append(("organization", context.organization))

    roots: dict[str, CandidateSource] = {}
    for kind, original in terms:
        normalized = normalize_keyword(original)
        if normalized:
            roots.setdefault(normalized, CandidateSource(
                kind=kind, original=original, normalized=normalized
            ))
        converted = to_pinyin(original)
        if converted and converted != normalized:
            roots.setdefault(converted, CandidateSource(
                kind="pinyin", original=original, normalized=converted
            ))
        short = abbreviate(original)
        if short and short != normalized:
            roots.setdefault(short, CandidateSource(
                kind="abbreviation", original=original, normalized=short
            ))
    return tuple(roots.items())


def historical_roots(password: str) -> tuple[str, ...]:
    prepared = _EDGE_RE.sub("", password)
    without_year = _YEAR_RE.sub("", prepared)
    without_digits = _TRAILING_DIGITS_RE.sub("", without_year)
    candidates = [without_digits, *_ALPHA_RE.findall(password.translate(_LEET_TO_ALPHA))]
    roots: dict[str, None] = {}
    for value in candidates:
        normalized = value.strip().casefold()
        if len(normalized) >= 2:
            roots.setdefault(normalized, None)
    return tuple(roots)


def historical_shape(password: str) -> HistoricalShape:
    digit_prefix_match = re.match(r"\d+", password)
    digit_suffix_match = re.search(r"\d+$", password)
    symbol_prefix_match = re.match(r"[^A-Za-z0-9]+", password)
    symbol_suffix_match = re.search(r"[^A-Za-z0-9]+$", password)
    letters = "".join(_ALPHA_RE.findall(password))
    if letters.isupper():
        case_style = "upper"
    elif letters[:1].isupper() and letters[1:].islower():
        case_style = "capitalized"
    else:
        case_style = "lower"
    return HistoricalShape(
        has_year=bool(_YEAR_RE.search(password)),
        digit_prefix=digit_prefix_match.group(0) if digit_prefix_match else "",
        digit_suffix=digit_suffix_match.group(0) if digit_suffix_match else "",
        symbol_prefix=symbol_prefix_match.group(0) if symbol_prefix_match else "",
        symbol_suffix=symbol_suffix_match.group(0) if symbol_suffix_match else "",
        case_style=case_style,
    )


def apply_shape(
    root: str,
    shape: HistoricalShape,
    *,
    current_year: str,
    number_fallback: str,
    symbol_fallback: str,
) -> str:
    if shape.case_style == "upper":
        prepared = root.upper()
    elif shape.case_style == "capitalized":
        prepared = root[:1].upper() + root[1:].lower()
    else:
        prepared = root.lower()
    suffix = current_year if shape.has_year else (shape.digit_suffix or number_fallback)
    return "".join((
        shape.symbol_prefix,
        shape.digit_prefix,
        prepared,
        suffix,
        shape.symbol_suffix or symbol_fallback,
    ))


def iter_history_records(
    historical_passwords: Sequence[str],
    *,
    years: Sequence[str],
    numbers: Sequence[str],
    symbols: Sequence[str],
) -> Iterator[CandidateRecord]:
    current_year = years[0] if years else ""
    emitted: set[str] = set()
    for password in historical_passwords:
        masked = masked_historical_source(password)
        base_source = CandidateSource(
            kind="historical_password",
            template=masked,
            components=(historical_shape(password).signature,),
        )
        roots = historical_roots(password)
        values: list[tuple[str, CandidateSource]] = []
        for value in (password.lower(), password.upper(), password[:1].upper() + password[1:].lower(), password.swapcase()):
            values.append((value, base_source))
        if current_year and _YEAR_RE.search(password):
            values.append((_YEAR_RE.sub(current_year, password), CandidateSource(
                kind="historical_structure", template="year-replacement", components=(masked,)
            )))
        for root in roots:
            values.append((root, CandidateSource(
                kind="historical_structure", template="word-root", components=(masked,)
            )))
            for number in numbers:
                values.extend((
                    (f"{root}{number}", CandidateSource(kind="historical_structure", template="number-suffix", components=(masked,))),
                    (f"{number}{root}", CandidateSource(kind="historical_structure", template="number-prefix", components=(masked,))),
                ))
            for symbol in symbols:
                values.append((f"{root}{symbol}", CandidateSource(
                    kind="historical_structure", template="symbol-variation", components=(masked,)
                )))
            for old, new in _SUBSTITUTIONS:
                if old in root:
                    values.append((root.replace(old, new), CandidateSource(
                        kind="historical_structure", template=f"substitute-{old}", components=(masked,)
                    )))
            migrated = apply_shape(
                root,
                historical_shape(password),
                current_year=current_year,
                number_fallback=numbers[0] if numbers else "",
                symbol_fallback=symbols[0] if symbols else "",
            )
            values.append((migrated, CandidateSource(
                kind="historical_structure",
                template=historical_shape(password).signature,
                components=(masked,),
            )))
        for value, source in values:
            if value and value not in emitted:
                emitted.add(value)
                yield CandidateRecord(value, StrategyId.S4, (source,))


def serialize_context(context: TaskContext | None) -> dict[str, object]:
    if context is None:
        return {}
    return context.model_dump(exclude={"description"})


def restore_context(raw: object) -> TaskContext | None:
    if not isinstance(raw, Mapping) or not raw:
        return None
    return TaskContext.model_validate(dict(raw))


def stable_records(records: Iterable[CandidateRecord]) -> Iterator[CandidateRecord]:
    emitted: set[str] = set()
    for record in records:
        if record.value not in emitted:
            emitted.add(record.value)
            yield record
