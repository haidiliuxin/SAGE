"""Online nonnegative affine execution-cost fit, implemented for SAGE.

Only fully tested, completed batches are regression observations. Partial or
failed executions are censored observations, not cheap completed batches.
"""

from dataclasses import asdict, dataclass, field
import math

from .types import require_count, require_seconds


@dataclass(frozen=True, slots=True)
class UCBConfig:
    exploration_coefficient: float = math.sqrt(2)
    startup_prior: float = 0.0
    seconds_per_candidate_prior: float = 0.01
    minimum_cost: float = 1e-6

    def __post_init__(self):
        for key, value in asdict(self).items():
            require_seconds(value, key, positive=key != "startup_prior")


@dataclass(frozen=True, slots=True)
class CostEstimate:
    startup_cost: float
    seconds_per_candidate: float
    estimated_batch_time: float
    confidence: float
    complete_samples: int
    censored_samples: int
    recent_sample_count: int
    recent_throughput: float | None
    last_batch_throughput: float | None
    identifiable: bool
    extrapolated: bool
    confidence_kind: str = "heuristic_quality_not_probability"

    def as_dict(self):
        return asdict(self)


@dataclass
class OnlineCostModel:
    config: UCBConfig
    samples: int = 0
    censored_samples: int = 0
    sum_x: float = 0.0
    sum_y: float = 0.0
    sum_xx: float = 0.0
    sum_xy: float = 0.0
    recent: list[dict] = field(default_factory=list)
    window_size: int = 20

    def __post_init__(self):
        require_count(self.window_size, "window_size", positive=True)
        if self.window_size > 10000:
            raise ValueError("window_size exceeds 10000")

    def observe(self, candidates: int, duration: float, *, complete: bool, tested: int | None = None) -> None:
        require_count(candidates, "candidates", positive=True)
        require_seconds(duration, "duration")
        if type(complete) is not bool:
            raise ValueError("complete must be boolean")
        if tested is None and complete:
            tested = candidates
        if tested is not None:
            require_count(tested, "tested")
            if tested > candidates or (complete and tested != candidates):
                raise ValueError("invalid calibrated tested count")
        observation = {"candidates": candidates, "duration": duration, "tested": tested, "complete": complete}
        if not complete:
            self.censored_samples += 1
            self.recent = (self.recent + [observation])[-self.window_size:]
            return
        values = (self.sum_x + candidates, self.sum_y + duration,
                  self.sum_xx + candidates * candidates, self.sum_xy + candidates * duration)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("cost statistics overflow")
        self.samples += 1
        self.sum_x, self.sum_y, self.sum_xx, self.sum_xy = values
        self.recent = (self.recent + [observation])[-self.window_size:]

    def coefficients(self) -> tuple[float, float]:
        if not self.samples:
            return self.config.startup_prior, self.config.seconds_per_candidate_prior
        n, sx, sy, sxx, sxy = self.samples, self.sum_x, self.sum_y, self.sum_xx, self.sum_xy
        determinant = n * sxx - sx * sx
        if determinant <= 1e-12 * max(1.0, n * sxx):
            # Equal-sized batches cannot identify both coefficients. Fix the
            # intercept at the configured prior, bounded by observed mean time.
            intercept = min(self.config.startup_prior, sy / n)
            return intercept, max(0.0, (sy - n * intercept) / sx)
        slope = (n * sxy - sx * sy) / determinant
        intercept = (sy - slope * sx) / n
        choices = [(0.0, sxy / sxx), (sy / n, 0.0)]
        if slope >= 0 and intercept >= 0:
            choices.append((intercept, slope))
        # Sum of squared errors with the common sum(y^2) omitted.
        return min(choices, key=lambda ab: (
            n * ab[0] ** 2 + 2 * ab[0] * ab[1] * sx + ab[1] ** 2 * sxx
            - 2 * ab[0] * sy - 2 * ab[1] * sxy
        ))

    def predict(self, candidates: int) -> float:
        require_count(candidates, "candidates", positive=True)
        intercept, slope = self.coefficients()
        result = max(self.config.minimum_cost, intercept + slope * candidates)
        if not math.isfinite(result):
            raise ValueError("predicted cost is not finite")
        return result

    def snapshot(self) -> dict:
        return {key: value for key, value in asdict(self).items() if key != "config"}

    def estimate(self, candidates: int) -> CostEstimate:
        require_count(candidates, "candidates", positive=True)
        complete = [row for row in self.recent if row["complete"]]
        fitted = OnlineCostModel(self.config)
        for row in complete:
            fitted.observe(row["candidates"], row["duration"], complete=True)
        source = fitted if complete else self
        intercept, slope = source.coefficients()
        predicted = source.predict(candidates)
        sizes = [row["candidates"] for row in complete]
        identifiable = len(set(sizes)) > 1
        extrapolated = not sizes or not min(sizes) <= candidates <= max(sizes)
        residual = sum(abs(intercept + slope * row["candidates"] - row["duration"]) for row in complete)
        total_time = sum(row["duration"] for row in complete)
        quality = 1 / (1 + residual / max(total_time, self.config.minimum_cost))
        confidence = (min(1.0, len(complete) / 10) * quality
                      * (len(complete) / max(1, len(self.recent)))
                      * (1.0 if identifiable else 0.5) * (0.5 if extrapolated else 1.0))
        # Zero duration cannot establish a measurable throughput or fit confidence.
        if total_time == 0:
            confidence = 0.0
        measured = [row for row in self.recent if row["tested"] is not None and row["duration"] > 0]
        seconds = sum(row["duration"] for row in measured)
        throughput = sum(row["tested"] for row in measured) / seconds if seconds else None
        last = self.recent[-1] if self.recent else None
        last_throughput = last["tested"] / last["duration"] if last and last["tested"] is not None and last["duration"] > 0 else None
        return CostEstimate(intercept, slope, predicted, confidence, self.samples, self.censored_samples,
                            len(self.recent), throughput, last_throughput, identifiable, extrapolated)

    @classmethod
    def from_snapshot(cls, config: UCBConfig, value: dict):
        value = dict(value)
        recent = value.pop("recent", [])
        window_size = value.pop("window_size", 20)
        require_count(window_size, "window_size", positive=True)
        if window_size > 10000 or not isinstance(recent, list) or len(recent) > window_size:
            raise ValueError("invalid recent cost window")
        if set(value) != {"samples", "censored_samples", "sum_x", "sum_y", "sum_xx", "sum_xy"}:
            raise ValueError("invalid cost snapshot fields")
        for key in ("samples", "censored_samples"):
            require_count(value[key], key)
        for key in ("sum_x", "sum_y", "sum_xx", "sum_xy"):
            require_seconds(value[key], key)
        n, sx, sy, sxx, sxy = (value[k] for k in ("samples", "sum_x", "sum_y", "sum_xx", "sum_xy"))
        if n == 0:
            if any((sx, sy, sxx, sxy)):
                raise ValueError("empty cost fit has observations")
        elif sx < n or sxx < sx or n * sxx < sx * sx * (1 - 1e-12) or (sy == 0 and sxy != 0):
            raise ValueError("inconsistent cost statistics")
        check = cls(config, window_size=window_size)
        for row in recent:
            check.observe(**row)
        if check.samples > n or check.censored_samples > value["censored_samples"]:
            raise ValueError("recent observations exceed historical counts")
        if check.sum_x > sx or check.sum_y > sy + 1e-10:
            raise ValueError("recent observations exceed historical resources")
        return cls(config, **value, recent=check.recent, window_size=window_size)
