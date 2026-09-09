from __future__ import annotations

import json

import pytest

from sage_pass.database import Database
from sage_pass.feedback import FeedbackConfig, FeedbackService, pattern_confidence
from sage_pass.hashcat_adapter import RecoveredCredential
from sage_pass.models import (
    FeedbackRunModel,
    PRIRModel,
    PatternKnowledgeModel,
    TaskModel,
)
from sage_pass.patterns import (
    MAX_PATTERN_INPUT_LENGTH,
    YEAR_MAX,
    YEAR_MIN,
    PatternExtractor,
)
from sage_pass.repository import PatternKnowledgeRepository
from sage_pass.service import now_iso
from sage_pass.transfer import load_transfer_knowledge


@pytest.mark.parametrize(
    ("plaintext", "signature", "case_pattern", "digit_position"),
    [
        ("password", "L8", "all_lower", "none"),
        ("Password", "U1L7", "capitalized", "none"),
        ("PASSWORD", "U8", "all_upper", "none"),
        ("Admin2025!", "U1L4D4S1", "capitalized", "middle"),
        ("nku@2026", "L3S1D4", "all_lower", "suffix"),
        ("abc123", "L3D3", "all_lower", "suffix"),
        ("123abc", "D3L3", "all_lower", "prefix"),
        ("a1b2c3", "L1D1L1D1L1D1", "all_lower", "mixed"),
        ("123456", "D6", "no_letters", "mixed"),
        ("!@#$", "S4", "no_letters", "none"),
        ("", "EMPTY", "no_letters", "none"),
    ],
)
def test_pattern_extractor_core_examples(
    plaintext, signature, case_pattern, digit_position
):
    result = PatternExtractor().extract(plaintext)
    assert result.structure_signature == signature
    assert result.case_pattern == case_pattern
    assert result.digit_position == digit_position
    assert result.length == len(plaintext)


def test_pattern_extractor_classes_unicode_edges_and_year_boundaries():
    extractor = PatternExtractor()
    result = extractor.extract(f"南开{YEAR_MIN}-{YEAR_MAX}")
    assert result.character_classes == {
        "lowercase": False,
        "uppercase": False,
        "digit": True,
        "symbol": True,
        "other_unicode": True,
    }
    assert result.years == (YEAR_MIN, YEAR_MAX)
    assert extractor.extract(f"x{YEAR_MIN - 1}").years == ()
    assert extractor.extract(f"x{YEAR_MAX + 1}").years == ()
    assert "year4" in extractor.extract(f"word{YEAR_MIN}").suffix_patterns
    assert "year4" in extractor.extract(f"{YEAR_MAX}word").prefix_patterns


def test_pattern_extractor_common_substitution_is_contextual():
    extractor = PatternExtractor()
    assert extractor.extract("p@ssw0rd").common_substitutions == (
        "a_to_at",
        "o_to_0",
    )
    assert extractor.extract("t3st1ng").common_substitutions == (
        "e_to_3",
        "i_or_l_to_1",
    )
    assert extractor.extract("pa55word").common_substitutions == ("s_to_5",)
    assert extractor.extract("pa$$word").common_substitutions == ("s_to_dollar",)
    assert extractor.extract("nku@2026").common_substitutions == ()
    assert extractor.extract("123abc").common_substitutions == ()
    assert extractor.extract("123456").common_substitutions == ()


def test_pattern_extractor_long_input_boundary():
    extractor = PatternExtractor()
    assert extractor.extract("a" * MAX_PATTERN_INPUT_LENGTH).length == MAX_PATTERN_INPUT_LENGTH
    with pytest.raises(ValueError, match="exceeds"):
        extractor.extract("a" * (MAX_PATTERN_INPUT_LENGTH + 1))


def _seed_task(db: Database, task_id: str, algorithm: str = "md5") -> None:
    timestamp = now_iso()
    with db.session_factory() as session:
        session.add(TaskModel(
            task_id=task_id,
            name=task_id,
            target_type="hash",
            target_content="0123456789abcdef0123456789abcdef",
            file_id=None,
            known_algorithm=algorithm,
            time_budget=60,
            candidate_budget=10_000,
            context={},
            status="completed",
            created_at=timestamp,
            updated_at=timestamp,
        ))
        session.add(PRIRModel(
            task_id=task_id,
            target_type="hash",
            algorithm=algorithm,
            salt=False,
            verification_cost="low",
            context_available=False,
            candidate_space=None,
            time_budget=60,
            candidate_budget=10_000,
            status="analyzed",
            confidence=0.9,
            warnings=[],
            created_at=timestamp,
            updated_at=timestamp,
        ))
        session.commit()


def _feedback_db(tmp_path):
    db = Database(f"sqlite:///{(tmp_path / 'feedback.db').as_posix()}")
    db.create_all()
    return db


def test_feedback_is_run_idempotent_and_task_count_is_distinct(tmp_path):
    db = _feedback_db(tmp_path)
    _seed_task(db, "T1")
    _seed_task(db, "T2")
    service = FeedbackService(db.session_factory)
    recovered = [
        RecoveredCredential("hash-a", "Admin2025!"),
        RecoveredCredential("hash-a", "Admin2025!"),
        RecoveredCredential("hash-b", "Hello2026!"),
    ]

    first = service.process_completed_run(
        run_id="R1", task_id="T1", recovered_items=recovered
    )
    duplicate = service.process_completed_run(
        run_id="R1", task_id="T1", recovered_items=recovered
    )
    service.process_completed_run(
        run_id="R2",
        task_id="T1",
        recovered_items=[RecoveredCredential("hash-c", "Other2024!")],
    )
    service.process_completed_run(
        run_id="R3",
        task_id="T2",
        recovered_items=[RecoveredCredential("hash-d", "Third2023!")],
    )

    assert first.processed is True
    assert duplicate.processed is False
    with db.session_factory() as session:
        row = PatternKnowledgeRepository(session).get_identity(
            "hash:md5", "structure_signature", "U1L4D4S1"
        )
        assert row is not None
        assert row.observation_count == 4
        assert row.task_count == 2
        assert 0.0 <= row.confidence <= 1.0
        assert session.query(FeedbackRunModel).count() == 3
    db.dispose()


def test_low_support_is_not_transferable_and_query_order_is_stable(tmp_path):
    db = _feedback_db(tmp_path)
    _seed_task(db, "T1")
    service = FeedbackService(db.session_factory)
    service.process_completed_run(
        run_id="R1",
        task_id="T1",
        recovered_items=[RecoveredCredential("hash", "Admin2025!")],
    )
    config = FeedbackConfig(minimum_observations=2, minimum_tasks=2)
    with db.session_factory() as session:
        repo = PatternKnowledgeRepository(session)
        patterns, summary = load_transfer_knowledge(
            repo,
            target_type="hash",
            algorithm="md5",
            candidate_budget=1000,
            config=config,
        )
        assert patterns == []
        assert summary.available is False
        first = [(r.pattern_type, r.pattern_signature) for r in repo.list(limit=100)]
        second = [(r.pattern_type, r.pattern_signature) for r in repo.list(limit=100)]
        assert first == second
    db.dispose()


def test_pattern_database_never_contains_recovered_plaintext(tmp_path):
    db = _feedback_db(tmp_path)
    _seed_task(db, "T1")
    secret = "UniqueRecoveredSecret2025!"
    FeedbackService(db.session_factory).process_completed_run(
        run_id="R1",
        task_id="T1",
        recovered_items=[RecoveredCredential("hash", secret)],
    )
    with db.session_factory() as session:
        serialized = json.dumps([
            {
                "signature": row.pattern_signature,
                "features": row.feature_data,
            }
            for row in session.query(PatternKnowledgeModel).all()
        ])
        assert secret not in serialized
    assert secret.encode() not in (tmp_path / "feedback.db").read_bytes()
    db.dispose()


def test_feedback_commit_failure_rolls_back_patterns_and_marker(tmp_path):
    db = _feedback_db(tmp_path)
    _seed_task(db, "T1")

    def failing_factory():
        session = db.session_factory()
        session.commit = lambda: (_ for _ in ()).throw(RuntimeError("commit failed"))
        return session

    with pytest.raises(RuntimeError, match="commit failed"):
        FeedbackService(failing_factory).process_completed_run(
            run_id="R1",
            task_id="T1",
            recovered_items=[RecoveredCredential("hash", "Admin2025!")],
        )
    with db.session_factory() as session:
        assert session.query(PatternKnowledgeModel).count() == 0
        assert session.query(FeedbackRunModel).count() == 0
    db.dispose()


@pytest.mark.parametrize("observations", range(0, 30))
@pytest.mark.parametrize("tasks", range(0, 10))
def test_confidence_is_always_bounded(observations, tasks):
    assert 0.0 <= pattern_confidence(observations, tasks) <= 1.0
