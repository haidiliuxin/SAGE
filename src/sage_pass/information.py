from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .enums import InformationScenario, InformationType
from .schemas import (
    InformationProfile,
    InformationStructureSummary,
    TaskContext,
)


_TYPE_FIELDS: tuple[tuple[InformationType, tuple[str, ...]], ...] = (
    (InformationType.NAME, ("name",)),
    (InformationType.NICKNAME, ("nickname",)),
    (InformationType.USERNAME, ("username",)),
    (InformationType.EMAIL_LOCAL_PART, ("email_local_part",)),
    (InformationType.PHONE_SUFFIX, ("phone_suffix",)),
    (InformationType.BIRTHDAY_OR_YEAR, ("birthday", "birth_year", "years")),
    (InformationType.REGION, ("region",)),
    (InformationType.ORGANIZATION, ("organization",)),
    (InformationType.INTEREST_WORD, ("interest_words",)),
    (
        InformationType.AUTHORIZED_KEYWORD,
        ("authorized_keywords", "keywords"),
    ),
)


def build_information_profile(
    context: TaskContext | dict[str, object] | None,
    historical_passwords: Iterable[str] = (),
    *,
    has_pattern_knowledge: bool = False,
) -> InformationProfile:
    """Create a deterministic, value-free profile for the Planner boundary."""
    prepared_context = (
        context
        if isinstance(context, TaskContext)
        else TaskContext.model_validate(context or {})
    )
    passwords = tuple(dict.fromkeys(historical_passwords))
    information_types = [
        information_type
        for information_type, fields in _TYPE_FIELDS
        if any(_present(getattr(prepared_context, field)) for field in fields)
    ]
    has_personal = bool(information_types)
    has_history = bool(passwords)
    scenario = {
        (False, False): InformationScenario.I0,
        (True, False): InformationScenario.I1,
        (False, True): InformationScenario.I2,
        (True, True): InformationScenario.I3,
    }[(has_personal, has_history)]

    personal_values = [
        value
        for _, fields in _TYPE_FIELDS
        for field in fields
        for value in _values(getattr(prepared_context, field))
    ]
    return InformationProfile(
        scenario=scenario,
        has_personal_information=has_personal,
        information_types=information_types,
        has_historical_passwords=has_history,
        historical_password_count=len(passwords),
        has_pattern_knowledge=has_pattern_knowledge,
        structure_summary=InformationStructureSummary(
            personal_field_count=len(information_types),
            personal_value_count=len(personal_values),
            historical_length_buckets=dict(sorted(Counter(
                _length_bucket(len(value)) for value in passwords
            ).items())),
            historical_character_classes=dict(sorted(Counter(
                _character_class_signature(value) for value in passwords
            ).items())),
        ),
    )


def masked_historical_source(value: str) -> str:
    """Safe label for candidate provenance; never contains password characters."""
    return f"[historical-password length={len(value)} classes={_character_class_signature(value)}]"


def _present(value: object) -> bool:
    return bool(value is not None and value != "" and value != [] and value != ())


def _values(value: object) -> list[object]:
    if not _present(value):
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _length_bucket(length: int) -> str:
    if length <= 7:
        return "1-7"
    if length <= 11:
        return "8-11"
    if length <= 15:
        return "12-15"
    return "16+"


def _character_class_signature(value: str) -> str:
    classes: list[str] = []
    if re.search(r"[a-z]", value):
        classes.append("lower")
    if re.search(r"[A-Z]", value):
        classes.append("upper")
    if re.search(r"\d", value):
        classes.append("digit")
    if re.search(r"[^A-Za-z0-9]", value):
        classes.append("symbol")
    return "+".join(classes) or "other"
