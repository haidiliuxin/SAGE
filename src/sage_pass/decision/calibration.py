"""Profile-isolated cost calibration and portable, non-sensitive measurements."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

from .costs import OnlineCostModel, UCBConfig
from .salts import SaltCondition
from .types import require_count, require_seconds


@dataclass(frozen=True)
class VerificationProfile:
    algorithm: str
    hash_mode: int
    target_count: int
    salts: SaltCondition
    device: str = "default"
    engine_version: str = "unspecified"
    parameters: dict = field(default_factory=dict)

    def __post_init__(self):
        for name in ("algorithm", "device", "engine_version"):
            if not isinstance(getattr(self, name), str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", getattr(self, name)):
                raise ValueError("profile labels must be non-sensitive symbolic identifiers")
        require_count(self.hash_mode, "hash_mode")
        require_count(self.target_count, "target_count", positive=True)
        if self.salts.target_count != self.target_count:
            raise ValueError("salt group sizes differ from target count")
        allowed = {"iterations", "memory_kib", "parallelism", "cost", "n", "r", "p", "version"}
        if set(self.parameters) - allowed:
            raise ValueError("unknown algorithm parameter")
        for key, value in self.parameters.items():
            require_seconds(value, key)
        object.__setattr__(self, "parameters", dict(self.parameters))

    def as_dict(self):
        return {**asdict(self), "salts": self.salts.as_dict()}

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["salts"] = SaltCondition(**data["salts"])
        return cls(**data)

    @property
    def key(self):
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True, allow_nan=False).encode()).hexdigest()


class CostCalibrator:
    def __init__(self, profile: VerificationProfile, *, config=None, window_size=20):
        require_count(window_size, "window_size", positive=True)
        if window_size > 10000:
            raise ValueError("window_size exceeds 10000")
        self.profile = VerificationProfile.from_dict(profile.as_dict())
        self.model = OnlineCostModel(config or UCBConfig(), window_size=window_size)
        self.seen: set[str] = set()
        self.sources: set[str] = set()

    def observe(self, sample: dict):
        # Reconstruct a strict profile; raw candidate/hash/output fields are never copied.
        if sample.get("version") != 1 or VerificationProfile.from_dict(sample["profile"]).key != self.profile.key:
            raise ValueError("measurement belongs to a different verification profile")
        identity = sample["measurement_id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identity):
            raise ValueError("invalid measurement identifier")
        if identity in self.seen:
            raise ValueError("duplicate measurement identifier")
        source = sample.get("source", "imported")
        if source not in {"measured", "synthetic", "imported"}:
            raise ValueError("unknown calibration source")
        self.model.observe(sample["candidates"], sample["duration"],
                           complete=sample["complete"], tested=sample["tested"])
        self.seen.add(identity)
        self.sources.add(source)

    def report(self, batch_size: int):
        estimate = self.model.estimate(batch_size)
        return {"version": 1, "profile": self.profile.as_dict(), "profile_key": self.profile.key,
                "requested_batch_size": batch_size, "estimate": estimate.as_dict(),
                "model": self.model.snapshot(), "config": asdict(self.model.config),
                "cost_scope": "whole_target_group", "measurement_sources": sorted(self.sources)}


def replay_cost_from_calibration(report: dict, profile: VerificationProfile):
    """Use measured whole-group coefficients; never multiply by salt count again."""
    if report.get("version") != 1 or report.get("cost_scope") != "whole_target_group":
        raise ValueError("invalid calibration report")
    if VerificationProfile.from_dict(report["profile"]).key != profile.key or report.get("profile_key") != profile.key:
        raise ValueError("calibration profile mismatch")
    model = OnlineCostModel.from_snapshot(UCBConfig(**report["config"]), report["model"])
    estimate = model.estimate(report["requested_batch_size"])
    if estimate.as_dict() != report["estimate"]:
        raise ValueError("calibration estimate does not match its observations")
    if not model.samples or estimate.seconds_per_candidate <= 0:
        raise ValueError("calibration requires complete observations and a positive fitted slope")
    return {"startup_cost": estimate.startup_cost, "seconds_per_candidate": estimate.seconds_per_candidate}
