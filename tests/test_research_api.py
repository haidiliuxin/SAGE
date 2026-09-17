"""HTTP journal reads, restart persistence, pagination and privacy boundaries."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sage_pass.config import Settings
from sage_pass.main import create_app
from sage_pass.decision.research_log import ResearchRecorder, SqliteResearchLog
from sage_pass.decision.rewards import RewardContext
from sage_pass.enums import TaskStatus
from sage_pass.interfaces import BatchOutcome
from sage_pass.models import RunRecordModel
from sage_pass.research_api import _download_chunks


def make_run(client, payload):
    response = client.post('/api/tasks', json=payload)
    assert response.status_code == 201
    task_id = response.json()['task_id']
    assert client.post(f'/api/tasks/{task_id}/analyze').status_code == 200
    assert client.post(f'/api/tasks/{task_id}/plan').status_code == 200
    response = client.post(f'/api/tasks/{task_id}/execute', json={'mode': 'mock'})
    assert response.status_code == 200
    run_id = response.json()['run_id']
    # Complete mock rows so restarting the service does not change this fixture.
    assert client.get(f'/api/runs/{run_id}/result').status_code == 200
    return task_id, run_id


def write_journal(client, run_id, count=60, attempt='first', interrupted=False):
    path = client.app.state.settings.upload_dir.parent / 'research' / 'real.sqlite3'
    store = SqliteResearchLog(path)
    recorder = ResearchRecorder(run_id=run_id, policy_type='ucb',
                                context=RewardContext(100, 1000, 1000), store=store,
                                mode='real', attempt_id=attempt)
    stats = {'S1': {'allocated_candidates': 0, 'time_cost': 0, 'cost_samples': 0,
                    'recent_throughput': None}}
    for index in range(count):
        recorder.begin(decision=SimpleNamespace(strategy_id='S1', candidate_limit=1,
                                                time_limit=2, exploration=index == 0),
                       available={'S1': 1}, scores={'S1': {'score': 0.5, 'mean_reward': 0.1}},
                       prior_state=stats)
        if interrupted and index == count - 1:
            recorder.finish('shutdown', state=stats)
            break
        stats['S1'].update(allocated_candidates=index + 1, time_cost=index + 1)
        recorder.complete(BatchOutcome(run_id, 'S1', 'S1', index + 1, 1, 1, 1, 1,
                                        TaskStatus.COMPLETED, message='secret-plaintext'),
                          updated_state=stats)
    else:
        recorder.finish('completed', state=stats)
    return store


def test_summary_config_paging_export_and_run_isolation(client, hash_task_payload):
    task_id, run_id = make_run(client, hash_task_payload)
    _, other = make_run(client, hash_task_payload)
    store = write_journal(client, run_id)
    write_journal(client, other, count=1)
    root = f'/api/runs/{run_id}/research'
    summary = client.get(root).json()
    assert summary['configuration']['policy_type'] == 'ucb'
    assert summary['configuration']['reward_context']['initial_targets'] == 100
    assert summary['totals']['completed_rounds'] == 60
    assert summary['totals']['recovered_targets'] == 60
    assert summary['totals']['duration'] == 60
    assert summary['totals']['evaluation_reward'] == pytest.approx(60 * (0.01 - 0.0001 - 0.0001))
    assert summary['latest_completed']['payload']['learning_reward'] == 0.01
    assert summary['latest_state']['remaining_candidates'] == 940
    latest = client.get(root + '/events?latest=true&limit=50').json()
    assert len(latest['items']) == 50 and latest['has_more']
    events = latest['items']
    before = latest['next_before_sequence']
    while latest['has_more']:
        latest = client.get(root + f'/events?before_sequence={before}&limit=50').json()
        events = latest['items'] + events
        before = latest['next_before_sequence']
    assert events == list(store.events(run_id))
    assert len(events) == 122  # exceeds both checkpoint and page windows
    forward = client.get(root + f'/events?after_sequence={events[-2]["sequence"]}').json()
    assert forward['items'] == [events[-1]]
    downloaded = client.get(root + '/download')
    assert downloaded.status_code == 200
    assert 'attachment' in downloaded.headers['content-disposition']
    assert [json.loads(line) for line in downloaded.text.splitlines()] == events
    assert 'secret-plaintext' not in downloaded.text
    assert other not in downloaded.text
    assert client.get(f'/api/tasks/{task_id}/runs').json()['items'][0]['run_id'] == run_id
    assert client.get(f'/api/tasks/{task_id}/runs?offset=1').json()['items'] == []


def test_unavailable_and_invalid_queries_do_not_create_journal(client, hash_task_payload):
    _, run_id = make_run(client, hash_task_payload)
    root = f'/api/runs/{run_id}/research'
    assert client.get(root).json()['available'] is False
    assert client.get(root + '/events').json()['items'] == []
    assert client.get(root + '/download').status_code == 404
    assert not (client.app.state.settings.upload_dir.parent / 'research' / 'real.sqlite3').exists()
    for query in ('limit=0', 'limit=201', 'after_sequence=-1', 'before_sequence=0',
                  'latest=true&after_sequence=1', 'before_sequence=2&after_sequence=1'):
        assert client.get(root + '/events?' + query).status_code == 422
    for suffix in ('', '/events', '/download'):
        assert client.get('/api/runs/missing/research' + suffix).status_code == 404


def test_interrupted_and_retried_rounds_are_not_double_counted(client, hash_task_payload):
    _, run_id = make_run(client, hash_task_payload)
    store = write_journal(client, run_id, count=2, interrupted=True)
    root = f'/api/runs/{run_id}/research'
    first = client.get(root).json()
    assert first['totals']['completed_rounds'] == 1
    assert first['latest_decision']['payload']['reward'] is None
    assert first['stop_reason'] == 'shutdown'
    write_journal(client, run_id, count=2, attempt='second')
    second = client.get(root).json()
    assert second['totals']['completed_rounds'] == 2
    assert second['totals']['recovered_targets'] == 2
    assert sum(e['event_type'] == 'decision_completed' for e in store.events(run_id)) == 3


def test_history_and_journal_survive_fresh_service(tmp_path, hash_task_payload):
    settings = Settings(
        database_url=f'sqlite:///{(tmp_path / "restart.db").as_posix()}',
        upload_dir=tmp_path / 'uploads',
        max_upload_bytes=1024,
        cors_origins=('http://localhost:5173',),
    )
    with TestClient(create_app(settings)) as client:
        task_id, run_id = make_run(client, hash_task_payload)
        write_journal(client, run_id, count=3)
        # A finished real record can contain private fields. API reads only its
        # identifiers/status, and reads research from the separate journal.
        now = '2026-09-17T00:00:00+00:00'
        with client.app.state.database.session_factory() as session:
            session.add(RunRecordModel(run_id=run_id, task_id=task_id, mode='real',
                                      status='completed', started_at=now, finished_at=now,
                                      snapshot={'secret': 'never-expose'}, progress={},
                                      created_at=now, updated_at=now))
            session.commit()
        expected = client.get(f'/api/runs/{run_id}/research').json()
    with TestClient(create_app(settings)) as restarted:
        actual = restarted.get(f'/api/runs/{run_id}/research')
        assert actual.json() == expected
        assert 'never-expose' not in actual.text
        history = restarted.get(f'/api/tasks/{task_id}/runs').json()
        assert history['total'] == 1
        assert history['items'][0]['mode'] == 'real'
        assert len(restarted.get(f'/api/runs/{run_id}/research/download').text.splitlines()) == 8


def test_download_releases_reader_and_freezes_event_boundary(client, hash_task_payload):
    _, run_id = make_run(client, hash_task_payload)
    store = write_journal(client, run_id, count=3)
    expected = list(store.events(run_id))
    chunks = _download_chunks(store, run_id, expected[-1]['sequence'])
    first = next(chunks)
    # An in-progress download must not hold a SQLite read lock while the real
    # executor appends its next transaction. New rows belong to the next export.
    write_journal(client, run_id, count=1, attempt='during-download')
    actual = [json.loads(first), *(json.loads(line) for line in chunks)]
    assert actual == expected
