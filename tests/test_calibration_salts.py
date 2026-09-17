"""Cost calibration and explicit salt conditions; no real tool execution."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage_pass.decision.calibration import CostCalibrator, VerificationProfile, replay_cost_from_calibration
from sage_pass.decision.costs import OnlineCostModel, UCBConfig
from sage_pass.decision.research_log import SqliteResearchLog
from sage_pass.decision.salts import SaltCondition, SaltCostModel
from sage_pass.experiments.calibrate import main, measure_batches
from sage_pass.experiments.replay import ReplayCost, ReplayEnvironment, ReplayScenario
from sage_pass.hashcat_adapter import HashcatResult, RecoveredCredential
from sage_pass.enums import TaskStatus

ROOT = Path(__file__).resolve().parents[1]


def profile(mode="unsalted", sizes=(4,), **kwargs):
    return VerificationProfile("md5", 0, sum(sizes), SaltCondition(mode, sizes), **kwargs)


def sample(p, index, count, duration, complete=True, tested=None):
    return {"version": 1, "measurement_id": f"sample-{index}", "source": "synthetic",
            "profile": p.as_dict(), "candidates": count, "duration": duration,
            "tested": count if complete else tested, "complete": complete}


def test_estimate_reports_affine_fit_and_throughput():
    p = profile()
    calibrator = CostCalibrator(p)
    for i, size in enumerate((10, 20, 40)):
        calibrator.observe(sample(p, i, size, 2 + 0.5 * size))
    estimate = calibrator.report(20)["estimate"]
    assert estimate["startup_cost"] == pytest.approx(2)
    assert estimate["seconds_per_candidate"] == pytest.approx(0.5)
    assert estimate["estimated_batch_time"] == pytest.approx(12)
    assert estimate["recent_throughput"] == pytest.approx(70 / 41)
    assert estimate["last_batch_throughput"] == pytest.approx(40 / 22)
    assert 0 < estimate["confidence"] <= 1
    assert estimate["identifiable"] is True
    assert estimate["confidence_kind"] == "heuristic_quality_not_probability"


def test_recent_window_adapts_to_speed_change_and_restores():
    model = OnlineCostModel(UCBConfig(), window_size=2)
    for duration in (1, 1, 10, 10):
        model.observe(10, duration, complete=True)
    assert model.estimate(10).estimated_batch_time == pytest.approx(10)
    assert model.estimate(10).recent_throughput == 1
    assert model.samples == 4 and len(model.recent) == 2
    saved = json.loads(json.dumps(model.snapshot()))
    restored = OnlineCostModel.from_snapshot(model.config, saved)
    assert restored.estimate(10) == model.estimate(10)
    saved["recent"][0]["tested"] = 100
    with pytest.raises(ValueError):
        OnlineCostModel.from_snapshot(model.config, saved)


def test_partial_unknown_throughput_is_not_fabricated():
    model = OnlineCostModel(UCBConfig())
    model.observe(10, 2, complete=False)
    estimate = model.estimate(10)
    assert estimate.complete_samples == 0
    assert estimate.censored_samples == 1
    assert estimate.recent_throughput is None and estimate.last_batch_throughput is None
    assert estimate.confidence == 0
    model.observe(10, 0, complete=True)
    assert model.estimate(10).last_batch_throughput is None
    assert model.estimate(10).confidence == 0


def test_profiles_separate_algorithm_parameters_devices_target_and_salt_counts():
    p = profile()
    calibrator = CostCalibrator(p)
    variants = [profile(parameters={"iterations": 2}), profile(device="gpu-1"),
                profile(sizes=(2,)), profile("independent", (1, 1, 1, 1))]
    for i, other in enumerate(variants):
        assert other.key != p.key
        with pytest.raises(ValueError, match="different verification profile"):
            calibrator.observe(sample(other, i, 10, 1))
    assert calibrator.model.samples == 0


def test_import_calibration_does_not_multiply_measured_cost_by_salts():
    p = profile("independent", (1, 1, 1, 1))
    calibrator = CostCalibrator(p)
    for i, size in enumerate((10, 20, 40)):
        calibrator.observe(sample(p, i, size, 1 + size * 0.2))
    report = json.loads(json.dumps(calibrator.report(20)))
    cost = replay_cost_from_calibration(report, p)
    assert cost == pytest.approx({"startup_cost": 1, "seconds_per_candidate": 0.2})
    with pytest.raises(ValueError, match="profile mismatch"):
        replay_cost_from_calibration(report, profile())
    report["estimate"]["estimated_batch_time"] = 0.001
    with pytest.raises(ValueError, match="does not match"):
        replay_cost_from_calibration(report, p)


@pytest.mark.parametrize("mode,sizes", [("unsalted", (1, 1)), ("shared", (2, 2)),
                                       ("independent", (2,)), ("grouped", (4,)),
                                       ("grouped", (1, 1)), ("other", (4,)), ("unsalted", (0,))])
def test_invalid_salt_groups_rejected(mode, sizes):
    with pytest.raises(ValueError):
        SaltCondition(mode, sizes)


def test_salt_modes_model_reuse_without_a_blanket_salted_penalty():
    cost = SaltCostModel(0.01, compare_seconds=0.001)
    conditions = [SaltCondition("unsalted", (4,)), SaltCondition("shared", (4,)),
                  SaltCondition("grouped", (2, 2)), SaltCondition("independent", (1, 1, 1, 1))]
    assert [cost.seconds_per_candidate(c) for c in conditions] == pytest.approx([0.014, 0.014, 0.024, 0.044])
    assert SaltCondition("grouped", (3, 1)).as_dict() == SaltCondition("grouped", (1, 3)).as_dict()


@pytest.mark.parametrize("mode,seconds", [("unsalted", 0.456), ("shared", 0.456), ("grouped", 0.496), ("independent", 0.576)])
def test_salt_replay_cost_log_and_resume(mode, seconds, tmp_path):
    source = ROOT / f"examples/replay/salts-{mode}.json"
    scenario = ReplayScenario.from_dict(json.loads(source.read_text(encoding="utf-8")))
    expected = ReplayEnvironment(scenario, "cost_aware_ucb").run()
    assert expected["simulated_seconds"] == pytest.approx(seconds)
    assert expected["submitted_candidates"] == 4
    assert expected["recovered_targets"] == 3
    store = SqliteResearchLog(tmp_path / "events.sqlite3")
    partial = ReplayEnvironment(scenario, "cost_aware_ucb", research_log=store)
    partial.run(max_steps=1)
    resumed = ReplayEnvironment(scenario, "cost_aware_ucb", research_log=store)
    resumed.restore(json.loads(json.dumps(partial.snapshot())))
    assert resumed.run() == expected
    start = next(store.events())
    assert start["payload"]["verification_profile"]["salts"]["mode"] == mode
    assert '"c1"' not in json.dumps(list(store.events()))
    with pytest.raises(ValueError, match="OR"):
        replace(scenario, cost=ReplayCost(1, 1))


def test_measurement_runner_uses_exhaustion_not_raw_multisalt_progress():
    p = profile("independent", (1, 1, 1, 1))
    jobs = []
    def start(job):
        jobs.append(job)
        result = HashcatResult(TaskStatus.COMPLETED, 1.0, 999, (), 1, "private", "private", "private")
        return SimpleNamespace(wait=lambda: result, stop=lambda: None)
    observations = list(measure_batches(SimpleNamespace(start=start), p, ("h1", "h2", "h3", "h4"),
                                       ("c1", "c2", "c3"), [2, 3], 2, 10))
    assert len(observations) == 4
    assert [row["tested"] for row in observations] == [2, 3, 2, 3]
    assert len({row["measurement_id"] for row in observations}) == 4
    assert "private" not in json.dumps(observations) and "h1" not in json.dumps(observations)


@pytest.mark.parametrize("exit_code,duration,recovered", [(0, 1, (RecoveredCredential("hash", "secret"),)),
                                                        (1, 10, ()), (-1, 1, ())])
def test_incomplete_measurements_keep_time_but_unknown_candidate_count(exit_code, duration, recovered):
    result = HashcatResult(TaskStatus.COMPLETED, duration, 1, recovered, exit_code, "", "", "")
    adapter = SimpleNamespace(start=lambda job: SimpleNamespace(wait=lambda: result, stop=lambda: None))
    row = next(measure_batches(adapter, profile(sizes=(1,)), ("hash",), ("candidate",), [1], 1, 10))
    assert row["complete"] is False and row["tested"] is None
    assert row["duration"] == duration


def test_fit_cli_and_replay_import_end_to_end(tmp_path, monkeypatch):
    output = tmp_path / "calibration.json"
    monkeypatch.setattr("sys.argv", ["calibrate", "fit", "--input", str(ROOT / "examples/calibration/synthetic-measurements.jsonl"),
                                   "--output", str(output), "--batch-size", "20"])
    main()
    fitted = json.loads(output.read_text(encoding="utf-8"))
    assert fitted["profiles"][0]["measurement_sources"] == ["synthetic"]
    from sage_pass.experiments.__main__ import main as replay_main
    report = tmp_path / "replay.json"
    monkeypatch.setattr("sys.argv", ["replay", "--scenario", str(ROOT / "examples/replay/salts-unsalted.json"),
                                   "--output", str(report), "--policy", "fixed", "--calibration", str(output)])
    replay_main()
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["cost_scope"] == "whole_target_group"
    assert result["simulated_seconds"] == pytest.approx(0.44)
