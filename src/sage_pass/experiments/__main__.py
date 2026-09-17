"""python -m sage_pass.experiments --scenario ... --output ..."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from dataclasses import replace

from ..decision.research_log import SqliteResearchLog
from ..decision.rewards import RewardWeights
from ..decision.costs import UCBConfig
from ..decision.calibration import replay_cost_from_calibration
from .replay import ReplayEnvironment, ReplayScenario, ReplayCost, compare_baselines


def main() -> None:
    parser = argparse.ArgumentParser(description="SAGE-Pass B offline baseline replay")
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", choices=("all", "fixed", "round_robin", "heuristic_bandit", "ucb", "cost_aware_ucb"), default="all")
    parser.add_argument("--ucb-config", type=Path, help="JSON UCB coefficient and cost priors")
    parser.add_argument("--calibration", type=Path, help="fit report matching the scenario verification profile")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--checkpoint", type=Path, help="write a batch-boundary checkpoint")
    parser.add_argument("--resume", type=Path, help="restore a checkpoint using the same scenario/policy")
    parser.add_argument("--research-log", type=Path, help="append durable events; default: OUTPUT.research.sqlite3")
    parser.add_argument("--export-research", type=Path, help="export the research database to a new JSONL file")
    parser.add_argument("--reward-weights", type=Path, help="JSON object with recovery/time/candidates/duplicates weights")
    args = parser.parse_args()
    if args.policy == "all" and any(value is not None for value in (args.max_steps, args.checkpoint, args.resume)):
        parser.error("checkpoint/resume/max-steps require a single --policy")
    log_path = args.research_log or args.output.with_suffix(".research.sqlite3")
    paths = [args.scenario, args.output, args.checkpoint, args.resume, log_path, args.export_research, args.reward_weights, args.ucb_config, args.calibration]
    resolved = [path.resolve() for path in paths if path is not None]
    if len(set(resolved)) != len(resolved):
        parser.error("all input, output, checkpoint and research paths must differ")
    if args.export_research and args.export_research.exists():
        parser.error("research export must use a new path")
    try:
        scenario = ReplayScenario.from_dict(json.loads(args.scenario.read_text(encoding="utf-8-sig")))
        if args.calibration:
            if scenario.verification_profile is None or scenario.arm_costs:
                raise ValueError("calibration needs a verification profile and no per-arm cost overrides")
            calibration = json.loads(args.calibration.read_text(encoding="utf-8-sig"))
            if calibration.get("version") != 1:
                raise ValueError("unsupported calibration file")
            matches = [row for row in calibration["profiles"] if row.get("profile_key") == scenario.verification_profile.key]
            if len(matches) != 1:
                raise ValueError("expected exactly one matching calibration profile")
            scenario = replace(scenario, salt_cost_model=None,
                               cost=ReplayCost(**replay_cost_from_calibration(matches[0], scenario.verification_profile)))
        weights = RewardWeights(**json.loads(args.reward_weights.read_text(encoding="utf-8-sig"))) if args.reward_weights else RewardWeights()
        store = SqliteResearchLog(log_path)
        ucb_config = UCBConfig(**json.loads(args.ucb_config.read_text(encoding="utf-8-sig"))) if args.ucb_config else UCBConfig()
        if args.policy == "all":
            report = compare_baselines(scenario, reward_weights=weights, research_log=store, include_ucb=True, ucb_config=ucb_config)
        else:
            environment = ReplayEnvironment(scenario, args.policy, reward_weights=weights, research_log=store, ucb_config=ucb_config)
            if args.resume:
                environment.restore(json.loads(args.resume.read_text(encoding="utf-8-sig")))
            report = environment.run(max_steps=args.max_steps)
            if args.checkpoint:
                args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
                args.checkpoint.write_text(json.dumps(environment.snapshot(), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
                environment.mark_checkpoint()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        if args.export_research:
            store.export_jsonl(args.export_research)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f"replay failed: {exc}\n")
    print(f"Replay report written to {args.output}")
    print(f"Research events appended to {log_path}")


if __name__ == "__main__":
    main()
