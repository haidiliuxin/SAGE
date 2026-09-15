"""python -m sage_pass.experiments --scenario ... --output ..."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .replay import ReplayEnvironment, ReplayScenario, compare_baselines


def main() -> None:
    parser = argparse.ArgumentParser(description="SAGE-Pass B offline baseline replay")
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy", choices=("all", "fixed", "round_robin", "heuristic_bandit"), default="all")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--checkpoint", type=Path, help="write a batch-boundary checkpoint")
    parser.add_argument("--resume", type=Path, help="restore a checkpoint using the same scenario/policy")
    args = parser.parse_args()
    if args.policy == "all" and any(value is not None for value in (args.max_steps, args.checkpoint, args.resume)):
        parser.error("checkpoint/resume/max-steps require a single --policy")
    paths = [args.scenario, args.output, args.checkpoint, args.resume]
    resolved = [path.resolve() for path in paths if path is not None]
    if len(set(resolved)) != len(resolved):
        parser.error("scenario, output, checkpoint and resume must use different paths")
    try:
        scenario = ReplayScenario.from_dict(json.loads(args.scenario.read_text(encoding="utf-8-sig")))
        if args.policy == "all":
            report = compare_baselines(scenario)
        else:
            environment = ReplayEnvironment(scenario, args.policy)
            if args.resume:
                environment.restore(json.loads(args.resume.read_text(encoding="utf-8-sig")))
            report = environment.run(max_steps=args.max_steps)
            if args.checkpoint:
                args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
                args.checkpoint.write_text(json.dumps(environment.snapshot(), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.exit(2, f"replay failed: {exc}\n")
    print(f"Replay report written to {args.output}")


if __name__ == "__main__":
    main()
