from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass


YEAR_MIN = 1900
YEAR_MAX = 2099
MAX_PATTERN_INPUT_LENGTH = 4096

CHAR_LOWER = "L"
CHAR_UPPER = "U"
CHAR_DIGIT = "D"
CHAR_SYMBOL = "S"
CHAR_OTHER_UNICODE = "O"

CHARACTER_CLASS_NAMES = {
    CHAR_LOWER: "lowercase",
    CHAR_UPPER: "uppercase",
    CHAR_DIGIT: "digit",
    CHAR_SYMBOL: "symbol",
    CHAR_OTHER_UNICODE: "other_unicode",
}

DIGIT_POSITIONS = frozenset({"prefix", "suffix", "middle", "none", "mixed"})
CASE_PATTERNS = frozenset({
    "all_lower", "all_upper", "capitalized", "mixed", "no_letters"
})
COMMON_SUBSTITUTIONS = {
    "@": "a_to_at",
    "3": "e_to_3",
    "1": "i_or_l_to_1",
    "0": "o_to_0",
    "5": "s_to_5",
    "$": "s_to_dollar",
}


@dataclass(frozen=True, slots=True)
class PatternFeatures:
    length: int
    character_classes: dict[str, bool]
    structure_signature: str
    digit_position: str
    prefix_patterns: tuple[str, ...]
    suffix_patterns: tuple[str, ...]
    case_pattern: str
    years: tuple[int, ...]
    common_substitutions: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PatternObservation:
    pattern_type: str
    pattern_signature: str
    feature_data: dict[str, object]


class PatternExtractor:
    """Extract deterministic abstractions without retaining the plaintext."""

    def extract(self, plaintext: str) -> PatternFeatures:
        if not isinstance(plaintext, str):
            raise TypeError("plaintext must be a string")
        if len(plaintext) > MAX_PATTERN_INPUT_LENGTH:
            raise ValueError(
                f"plaintext exceeds the {MAX_PATTERN_INPUT_LENGTH} character limit"
            )

        classes = [_character_class(char) for char in plaintext]
        class_flags = {
            name: code in classes for code, name in CHARACTER_CLASS_NAMES.items()
        }
        signature = _structure_signature(classes)
        digit_position = _digit_position(classes)
        prefix_patterns, suffix_patterns = _edge_patterns(plaintext, classes)
        years = tuple(
            int(match.group(0))
            for match in re.finditer(r"(?<!\d)\d{4}(?!\d)", plaintext)
            if YEAR_MIN <= int(match.group(0)) <= YEAR_MAX
        )
        substitutions = _common_substitutions(plaintext)
        return PatternFeatures(
            length=len(plaintext),
            character_classes=class_flags,
            structure_signature=signature,
            digit_position=digit_position,
            prefix_patterns=prefix_patterns,
            suffix_patterns=suffix_patterns,
            case_pattern=_case_pattern(plaintext),
            years=years,
            common_substitutions=substitutions,
        )

    def observations(self, plaintext: str) -> tuple[PatternObservation, ...]:
        features = self.extract(plaintext)
        observations = [
            PatternObservation("length", str(features.length), {"length": features.length}),
            PatternObservation(
                "character_classes",
                "+".join(
                    code
                    for code, name in CHARACTER_CLASS_NAMES.items()
                    if features.character_classes[name]
                ) or "EMPTY",
                {"classes": features.character_classes},
            ),
            PatternObservation(
                "structure_signature",
                features.structure_signature,
                {"signature": features.structure_signature, "length": features.length},
            ),
            PatternObservation(
                "digit_position",
                features.digit_position,
                {"position": features.digit_position},
            ),
            PatternObservation(
                "case_pattern",
                features.case_pattern,
                {"case": features.case_pattern},
            ),
        ]
        observations.extend(
            PatternObservation("prefix_pattern", item, {"pattern": item})
            for item in features.prefix_patterns
        )
        observations.extend(
            PatternObservation("suffix_pattern", item, {"pattern": item})
            for item in features.suffix_patterns
        )
        if features.years:
            observations.append(PatternObservation(
                "year_pattern", "year4", {"minimum": YEAR_MIN, "maximum": YEAR_MAX}
            ))
        observations.extend(
            PatternObservation(
                "common_substitution", item, {"substitution": item}
            )
            for item in features.common_substitutions
        )
        return tuple(observations)


def _character_class(char: str) -> str:
    if "a" <= char <= "z":
        return CHAR_LOWER
    if "A" <= char <= "Z":
        return CHAR_UPPER
    if unicodedata.category(char) == "Nd":
        return CHAR_DIGIT
    if unicodedata.category(char)[:1] in {"P", "S"}:
        return CHAR_SYMBOL
    return CHAR_OTHER_UNICODE


def _structure_signature(classes: list[str]) -> str:
    if not classes:
        return "EMPTY"
    pieces: list[str] = []
    current = classes[0]
    count = 1
    for item in classes[1:]:
        if item == current:
            count += 1
            continue
        pieces.append(f"{current}{count}")
        current, count = item, 1
    pieces.append(f"{current}{count}")
    return "".join(pieces)


def _digit_position(classes: list[str]) -> str:
    positions = [index for index, item in enumerate(classes) if item == CHAR_DIGIT]
    if not positions:
        return "none"
    groups = 1 + sum(
        current != previous + 1
        for previous, current in zip(positions, positions[1:])
    )
    if groups > 1 or len(positions) == len(classes):
        return "mixed"
    if positions[0] == 0:
        return "prefix"
    if positions[-1] == len(classes) - 1:
        return "suffix"
    return "middle"


def _edge_patterns(
    plaintext: str, classes: list[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    prefixes: list[str] = []
    suffixes: list[str] = []
    prefix_digits = re.match(r"\d+", plaintext)
    suffix_digits = re.search(r"\d+$", plaintext)
    if prefix_digits and 1 <= len(prefix_digits.group(0)) <= 4:
        prefixes.append("digits_1_4")
        value = int(prefix_digits.group(0))
        if len(prefix_digits.group(0)) == 4 and YEAR_MIN <= value <= YEAR_MAX:
            prefixes.append("year4")
    if suffix_digits and 1 <= len(suffix_digits.group(0)) <= 4:
        suffixes.append("digits_1_4")
        value = int(suffix_digits.group(0))
        if len(suffix_digits.group(0)) == 4 and YEAR_MIN <= value <= YEAR_MAX:
            suffixes.append("year4")
    if classes and classes[0] == CHAR_SYMBOL:
        if len(classes) == 1 or classes[1] != CHAR_SYMBOL:
            prefixes.append("single_symbol")
    if classes and classes[-1] == CHAR_SYMBOL:
        if len(classes) == 1 or classes[-2] != CHAR_SYMBOL:
            suffixes.append("single_symbol")
        if re.search(r"\d{1,4}[^\w\s]$", plaintext, flags=re.UNICODE):
            suffixes.append("digits_1_4_plus_symbol")
    return tuple(prefixes), tuple(suffixes)


def _case_pattern(plaintext: str) -> str:
    letters = [char for char in plaintext if char.isalpha()]
    if not letters:
        return "no_letters"
    if all(char.islower() for char in letters):
        return "all_lower"
    if all(char.isupper() for char in letters):
        return "all_upper"
    if letters[0].isupper() and all(char.islower() for char in letters[1:]):
        return "capitalized"
    return "mixed"


def _common_substitutions(plaintext: str) -> tuple[str, ...]:
    if not any(char.isalpha() for char in plaintext):
        return ()
    start = 0
    end = len(plaintext)
    leading_digits = re.match(r"\d+", plaintext)
    trailing_digits = re.search(r"\d+$", plaintext)
    if leading_digits:
        start = leading_digits.end()
    if trailing_digits:
        end = trailing_digits.start()
    found: list[str] = []
    for index, char in enumerate(plaintext):
        signature = COMMON_SUBSTITUTIONS.get(char)
        if signature is None:
            continue
        if not start <= index < end:
            continue
        if char in {"@", "$"}:
            run_start = index
            run_end = index
            while run_start > 0 and plaintext[run_start - 1] == char:
                run_start -= 1
            while run_end + 1 < len(plaintext) and plaintext[run_end + 1] == char:
                run_end += 1
            plausible = (
                run_start > 0
                and run_end + 1 < len(plaintext)
                and plaintext[run_start - 1].isalpha()
                and plaintext[run_end + 1].isalpha()
            )
        else:
            plausible = (
                (index > 0 and plaintext[index - 1].isalpha())
                or (index + 1 < len(plaintext) and plaintext[index + 1].isalpha())
            )
        if plausible and signature not in found:
            found.append(signature)
    return tuple(found)
