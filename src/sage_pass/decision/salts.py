"""Explicit salt conditions without storing salts or target values."""

from dataclasses import asdict, dataclass

from .types import require_count


@dataclass(frozen=True, slots=True)
class SaltCondition:
    mode: str
    group_sizes: tuple[int, ...]

    def __post_init__(self):
        if self.mode not in {"unsalted", "shared", "grouped", "independent"}:
            raise ValueError("unknown salt condition")
        groups = tuple(self.group_sizes)
        if not groups:
            raise ValueError("salt groups must be nonempty")
        for count in groups:
            require_count(count, "salt group size", positive=True)
        if self.mode in {"unsalted", "shared"} and len(groups) != 1:
            raise ValueError("unsalted/shared requires one computation group")
        if self.mode == "independent" and any(count != 1 for count in groups):
            raise ValueError("independent salts require one target per group")
        if self.mode == "grouped" and not 1 < len(groups) < sum(groups):
            raise ValueError("grouped salts require multiple groups with some shared targets")
        object.__setattr__(self, "group_sizes", tuple(sorted(groups)))

    @property
    def target_count(self):
        return sum(self.group_sizes)

    @property
    def group_count(self):
        return len(self.group_sizes)

    def as_dict(self):
        return {"mode": self.mode, "group_sizes": list(self.group_sizes)}


@dataclass(frozen=True, slots=True)
class SaltCostModel:
    """Synthetic reuse model; measured whole-group costs must not be multiplied.

    Per candidate = group_count * group_hash_seconds + target_count * compare_seconds.
    Parameters are explicit experiment inputs, never inferred from 'salted'.
    """
    group_hash_seconds: float
    compare_seconds: float = 0.0
    startup_cost: float = 0.0

    def __post_init__(self):
        from .types import require_seconds
        for key, value in asdict(self).items():
            require_seconds(value, key, positive=key == "group_hash_seconds")

    def seconds_per_candidate(self, condition: SaltCondition):
        from .types import require_seconds
        result = condition.group_count * self.group_hash_seconds + condition.target_count * self.compare_seconds
        require_seconds(result, "salt model cost", positive=True)
        return result
