"""Read-only research views. Never expose executor snapshots or recovered text."""
from __future__ import annotations

import json
from contextlib import closing
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .dependencies import get_session
from .errors import AppError
from .models import RunRecordModel, StrategyRunModel
from .repository import RunRecordRepository, StrategyRunRepository
from .service import require_task
from .decision.research_log import LOG_VERSION

router = APIRouter(tags=["research"])
SessionDependency = Annotated[Session, Depends(get_session)]


class ResearchEvent(BaseModel):
    sequence: int
    schema_version: int
    run_id: str
    attempt_id: str
    round_index: int
    event_type: str
    # Versioned privacy-projected ResearchRecorder payload, never a raw snapshot.
    payload: dict[str, Any]


class ResearchPage(BaseModel):
    items: list[ResearchEvent]
    next_after_sequence: int
    next_before_sequence: int | None
    has_more: bool


class ResearchTotals(BaseModel):
    completed_rounds: int = 0
    submitted_candidates: int = 0
    tested_candidates: int = 0
    recovered_targets: int = 0
    duration: float = 0
    evaluation_reward: float = 0


class ResearchConfiguration(BaseModel):
    mode: str
    policy_type: str
    policy_version: str
    policy_parameters: dict[str, float] = Field(default_factory=dict)
    reward_version: str
    learning_reward: str | None = None
    reward_weights: dict[str, float]
    reward_context: dict[str, int | float]
    seed: int | None = None
    resume_from_round: int = 0
    previous_arm_id: str | None = None
    decision_probability: str | None = None
    verification_profile: dict[str, Any] | None = None


class ResearchState(BaseModel):
    arms: dict[str, dict[str, int | float | None]]
    remaining_candidates: int
    remaining_time: float


class ResearchSummary(BaseModel):
    schema_version: int = 1
    run_id: str
    task_id: str
    status: str
    available: bool = False
    configuration: ResearchConfiguration | None = None
    latest_decision: ResearchEvent | None = None
    latest_completed: ResearchEvent | None = None
    latest_state: ResearchState | None = None
    stop_reason: str | None = None
    totals: ResearchTotals = Field(default_factory=ResearchTotals)
    through_sequence: int = 0


class RunReference(BaseModel):
    run_id: str
    task_id: str
    mode: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None


class RunPage(BaseModel):
    items: list[RunReference]
    total: int
    limit: int
    offset: int


def _run(session: Session, run_id: str) -> RunReference:
    record = RunRecordRepository(session).get(run_id)
    if record is not None:
        return RunReference(run_id=run_id, task_id=record.task_id, mode=record.mode,
                            status=record.status, started_at=record.started_at,
                            finished_at=record.finished_at)
    rows = StrategyRunRepository(session).list_by_run_id(run_id)
    if not rows:
        raise AppError("RUN_NOT_FOUND", "运行不存在", status_code=404)
    statuses = {row.status for row in rows}
    state = next((s for s in ("running", "paused", "failed", "cancelled") if s in statuses),
                 "completed")
    return RunReference(run_id=run_id, task_id=rows[0].task_id, mode="mock", status=state,
                        started_at=min((r.started_at for r in rows if r.started_at), default=None),
                        finished_at=max((r.finished_at for r in rows if r.finished_at), default=None)
                        if state in {"completed", "failed", "cancelled"} else None)


def _decode(row) -> dict | None:
    if row is None:
        return None
    value = json.loads(row[1])
    if value.get("schema_version") != LOG_VERSION:
        raise AppError("RESEARCH_VERSION_UNSUPPORTED", "研究日志版本不支持", status_code=409)
    return {**value, "sequence": row[0]}


def _download_chunks(store, run_id: str, upper: int):
    """Release SQLite read locks before yielding bytes to a slow HTTP client."""
    after = 0
    while after < upper:
        with closing(store._connect()) as connection:
            rows = connection.execute(
                "SELECT sequence,payload FROM decision_research_events "
                "WHERE run_id=? AND sequence>? AND sequence<=? ORDER BY sequence LIMIT 200",
                (run_id, after, upper)).fetchall()
        if not rows:
            break
        for row in rows:
            yield json.dumps(_decode(row), ensure_ascii=False, allow_nan=False) + "\n"
        after = rows[-1][0]


@router.get("/tasks/{task_id}/runs", response_model=RunPage)
def task_runs(task_id: str, session: SessionDependency,
              limit: Annotated[int, Query(ge=1, le=100)] = 20,
              offset: Annotated[int, Query(ge=0)] = 0) -> RunPage:
    require_task(session, task_id)
    # Include real records even when launch failed before strategy rows existed.
    sources = select(StrategyRunModel.run_id.label("run_id"),
                     func.min(StrategyRunModel.started_at).label("started_at")).where(
        StrategyRunModel.task_id == task_id).group_by(StrategyRunModel.run_id).union_all(
        select(RunRecordModel.run_id, RunRecordModel.started_at).where(
            RunRecordModel.task_id == task_id)).subquery()
    runs = select(sources.c.run_id, func.max(sources.c.started_at).label("started_at")).group_by(
        sources.c.run_id).subquery()
    total = session.scalar(select(func.count()).select_from(runs)) or 0
    ids = list(session.scalars(select(runs.c.run_id).order_by(
        runs.c.started_at.desc(), runs.c.run_id).limit(limit).offset(offset)))
    return RunPage(items=[_run(session, key) for key in ids], total=total, limit=limit, offset=offset)


@router.get("/runs/{run_id}/research", response_model=ResearchSummary)
def research_summary(run_id: str, request: Request, session: SessionDependency) -> ResearchSummary:
    run = _run(session, run_id)
    result = ResearchSummary(run_id=run_id, task_id=run.task_id, status=run.status)
    store = request.app.state.real_executor.research_log_reader()
    if store is None:
        return result
    with closing(store._connect()) as connection:
        # One read transaction gives metadata, totals and detail the same boundary.
        connection.execute("BEGIN")
        result.through_sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM decision_research_events WHERE run_id=?",
            (run_id,)).fetchone()[0]
        if not result.through_sequence:
            return result
        result.available = True

        def latest(kinds):
            placeholders = ",".join("?" for _ in kinds)
            return _decode(connection.execute(
                "SELECT sequence,payload FROM decision_research_events WHERE run_id=? "
                f"AND event_type IN ({placeholders}) ORDER BY sequence DESC LIMIT 1",
                (run_id, *kinds)).fetchone())

        start = latest(("run_started",))
        result.configuration = ResearchConfiguration.model_validate(start["payload"]) if start else None
        decision = latest(("decision_started", "decision_completed", "decision_interrupted"))
        completed = latest(("decision_completed",))
        result.latest_decision = ResearchEvent.model_validate(decision) if decision else None
        result.latest_completed = ResearchEvent.model_validate(completed) if completed else None
        state = latest(("decision_started", "decision_completed", "decision_interrupted", "run_finished"))
        if state:
            payload = state["payload"]
            values = payload.get("updated_state", payload.get("state", payload.get("prior_state")))
            result.latest_state = ResearchState.model_validate(values) if values else None
        finish = latest(("run_finished",))
        if finish and (not start or finish["sequence"] > start["sequence"]):
            result.stop_reason = finish["payload"].get("stop_reason")
        # A resumed round may have multiple attempts. Only its latest completed
        # event contributes to the logical-round summary; export retains all attempts.
        row = connection.execute("""
            WITH completed AS (
                SELECT MAX(sequence) AS sequence FROM decision_research_events
                WHERE run_id=? AND event_type='decision_completed' GROUP BY round_index
            )
            SELECT COUNT(*),
              COALESCE(SUM(json_extract(e.payload,'$.payload.feedback.candidate_count')),0),
              COALESCE(SUM(json_extract(e.payload,'$.payload.feedback.tested')),0),
              COALESCE(SUM(json_extract(e.payload,'$.payload.feedback.recovered')),0),
              COALESCE(SUM(json_extract(e.payload,'$.payload.feedback.duration')),0),
              COALESCE(SUM(json_extract(e.payload,'$.payload.reward')),0)
            FROM decision_research_events e JOIN completed c ON e.sequence=c.sequence
        """, (run_id,)).fetchone()
        result.totals = ResearchTotals(**dict(zip(ResearchTotals.model_fields, row)))
    return result


@router.get("/runs/{run_id}/research/events", response_model=ResearchPage)
def research_events(run_id: str, request: Request, session: SessionDependency,
                    after_sequence: Annotated[int, Query(ge=0)] = 0,
                    before_sequence: Annotated[int | None, Query(ge=1)] = None,
                    latest: bool = False,
                    limit: Annotated[int, Query(ge=1, le=200)] = 50) -> ResearchPage:
    _run(session, run_id)
    if (latest and (after_sequence or before_sequence is not None)) or (after_sequence and before_sequence is not None):
        raise AppError("INVALID_CURSOR", "不能混用事件查询方向", status_code=422)
    store = request.app.state.real_executor.research_log_reader()
    rows = []
    reverse = latest or before_sequence is not None
    if store is not None:
        with closing(store._connect()) as connection:
            query = "SELECT sequence,payload FROM decision_research_events WHERE run_id=? AND sequence>?"
            args = [run_id, after_sequence]
            if before_sequence is not None:
                query += " AND sequence<?"
                args.append(before_sequence)
            query += " ORDER BY sequence " + ("DESC" if reverse else "ASC") + " LIMIT ?"
            rows = connection.execute(query, (*args, limit + 1)).fetchall()
    more = len(rows) > limit
    items = [_decode(row) for row in rows[:limit]]
    if reverse:
        items.reverse()
    return ResearchPage(items=items, has_more=more,
                        next_after_sequence=items[-1]["sequence"] if items else after_sequence,
                        next_before_sequence=items[0]["sequence"] if items else None)


@router.get("/runs/{run_id}/research/download")
def research_download(run_id: str, request: Request, session: SessionDependency):
    _run(session, run_id)
    store = request.app.state.real_executor.research_log_reader()
    if store is None:
        raise AppError("RESEARCH_NOT_AVAILABLE", "该运行没有研究日志", status_code=404)
    with closing(store._connect()) as connection:
        upper = connection.execute(
            "SELECT MAX(sequence) FROM decision_research_events WHERE run_id=?", (run_id,)).fetchone()[0]
    if upper is None:
        raise AppError("RESEARCH_NOT_AVAILABLE", "该运行没有研究日志", status_code=404)

    return StreamingResponse(_download_chunks(store, run_id, upper), media_type="application/x-ndjson",
                             headers={"Content-Disposition": 'attachment; filename="research.jsonl"',
                                      "X-Research-Through-Sequence": str(upper)})
