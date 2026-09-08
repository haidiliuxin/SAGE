from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Protocol

from sqlalchemy.exc import IntegrityError

from .models import (
    FeedbackRunModel,
    PatternKnowledgeModel,
    PatternTaskObservationModel,
)
from .patterns import PatternExtractor
from .repository import (
    FeedbackRunRepository,
    PRIRRepository,
    PatternKnowledgeRepository,
    TaskRepository,
)
from .service import now_iso


FEEDBACK_SCHEMA_VERSION = 1
DEFAULT_MINIMUM_OBSERVATIONS = 2
DEFAULT_MINIMUM_TASKS = 2
DEFAULT_MAXIMUM_PATTERNS_PER_SCOPE = 500
DEFAULT_RECENCY_HALF_LIFE_DAYS = 90.0


class RecoveredValue(Protocol):
    target: str
    plaintext: str


@dataclass(frozen=True, slots=True)
class FeedbackConfig:
    minimum_observations: int = DEFAULT_MINIMUM_OBSERVATIONS
    minimum_tasks: int = DEFAULT_MINIMUM_TASKS
    maximum_patterns_per_scope: int = DEFAULT_MAXIMUM_PATTERNS_PER_SCOPE
    recency_half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS

    def __post_init__(self) -> None:
        if self.minimum_observations <= 0 or self.minimum_tasks <= 0:
            raise ValueError("feedback support thresholds must be positive")
        if self.maximum_patterns_per_scope <= 0:
            raise ValueError("maximum_patterns_per_scope must be positive")
        if self.recency_half_life_days <= 0:
            raise ValueError("recency_half_life_days must be positive")


@dataclass(frozen=True, slots=True)
class FeedbackResult:
    processed: bool
    pattern_count: int


class FeedbackService:
    """Idempotently aggregate a completed real run in one DB transaction."""

    def __init__(
        self,
        session_factory,
        *,
        extractor: PatternExtractor | None = None,
        config: FeedbackConfig | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.extractor = extractor or PatternExtractor()
        self.config = config or FeedbackConfig()

    def process_completed_run(
        self,
        *,
        run_id: str,
        task_id: str,
        recovered_items: Iterable[RecoveredValue],
    ) -> FeedbackResult:
        deduplicated: dict[tuple[str, str], RecoveredValue] = {}
        for item in recovered_items:
            deduplicated.setdefault((item.target, item.plaintext), item)

        grouped: Counter[tuple[str, str]] = Counter()
        features: dict[tuple[str, str], dict[str, object]] = {}
        for item in deduplicated.values():
            for observation in self.extractor.observations(item.plaintext):
                key = (observation.pattern_type, observation.pattern_signature)
                grouped[key] += 1
                features.setdefault(key, observation.feature_data)

        with self.session_factory() as session:
            try:
                feedback_runs = FeedbackRunRepository(session)
                if feedback_runs.get(run_id) is not None:
                    existing = feedback_runs.get(run_id)
                    return FeedbackResult(False, existing.pattern_count if existing else 0)
                task = TaskRepository(session).get(task_id)
                prir = PRIRRepository(session).get(task_id)
                if task is None or prir is None:
                    raise ValueError("feedback task or PRIR is unavailable")

                target_type = task.target_type
                algorithm = (prir.algorithm or "unknown").strip().casefold()
                scope = make_scope(target_type, algorithm)
                repository = PatternKnowledgeRepository(session)
                timestamp = now_iso()
                scope_count = repository.count_scope(scope)
                touched = 0
                for pattern_type, signature in sorted(grouped):
                    row = repository.get_identity(scope, pattern_type, signature)
                    if row is None:
                        if scope_count >= self.config.maximum_patterns_per_scope:
                            continue
                        row = PatternKnowledgeModel(
                            scope=scope,
                            target_type=target_type,
                            algorithm=algorithm,
                            pattern_type=pattern_type,
                            pattern_signature=signature,
                            feature_data=features[(pattern_type, signature)],
                            observation_count=0,
                            task_count=0,
                            confidence=0.0,
                            first_seen_at=timestamp,
                            last_seen_at=timestamp,
                            created_at=timestamp,
                            updated_at=timestamp,
                        )
                        session.add(row)
                        session.flush()
                        scope_count += 1
                    row.observation_count += grouped[(pattern_type, signature)]
                    row.last_seen_at = timestamp
                    row.updated_at = timestamp
                    if not repository.task_observed(row.id, task_id):
                        session.add(PatternTaskObservationModel(
                            pattern_id=row.id,
                            task_id=task_id,
                            first_seen_at=timestamp,
                        ))
                        row.task_count += 1
                    row.confidence = pattern_confidence(
                        row.observation_count, row.task_count
                    )
                    touched += 1

                session.add(FeedbackRunModel(
                    run_id=run_id,
                    task_id=task_id,
                    processed_at=timestamp,
                    pattern_count=touched,
                    schema_version=FEEDBACK_SCHEMA_VERSION,
                ))
                session.commit()
                return FeedbackResult(True, touched)
            except IntegrityError:
                session.rollback()
                if FeedbackRunRepository(session).get(run_id) is not None:
                    existing = FeedbackRunRepository(session).get(run_id)
                    return FeedbackResult(False, existing.pattern_count if existing else 0)
                raise
            except Exception:
                session.rollback()
                raise


def make_scope(target_type: str, algorithm: str) -> str:
    return f"{target_type.strip().casefold()}:{algorithm.strip().casefold() or 'unknown'}"


def pattern_confidence(observation_count: int, task_count: int) -> float:
    """Stable support score; one observation in one task yields only 0.30."""
    observations = max(0, observation_count)
    tasks = max(0, task_count)
    value = (
        0.4 * observations / (observations + 3.0)
        + 0.6 * tasks / (tasks + 2.0)
    )
    return round(min(1.0, max(0.0, value)), 6)
