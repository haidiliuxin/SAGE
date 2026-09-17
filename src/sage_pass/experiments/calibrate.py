"""Measure local batches or fit a portable calibration report. No plaintext output."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from ..decision.calibration import CostCalibrator, VerificationProfile
from ..decision.types import require_count, require_seconds
from ..hashcat_adapter import HashcatAdapter, HashcatJob


def measure_batches(adapter, profile, targets, candidates, sizes, repeats, timeout):
    """Yield sanitized observations; only exhausted, non-recovering batches train.

    A partial multi-salt progress counter cannot safely identify how many whole
    candidates were verified. It is deliberately recorded as unknown.
    """
    require_count(repeats, "repeats", positive=True)
    require_seconds(timeout, "timeout", positive=True)
    if len(targets) != profile.target_count or len(set(targets)) != len(targets):
        raise ValueError("target file must match the declared distinct target count")
    if len(set(candidates)) != len(candidates):
        raise ValueError("calibration candidates must be unique")
    for size in sizes:
        require_count(size, "batch size", positive=True)
        if size > len(candidates):
            raise ValueError("candidate file is too short for a requested batch")
    for _ in range(repeats):
        for size in sizes:
            identity = uuid4().hex
            handle = adapter.start(HashcatJob("calibration-" + identity, tuple(targets),
                                             profile.hash_mode, tuple(candidates[:size]), timeout, size))
            try:
                result = handle.wait()
            except BaseException:
                handle.stop()
                raise
            complete = (result.status == "completed" and result.exit_code == 1
                        and not result.recovered and result.duration < timeout)
            yield {"version": 1, "measurement_id": identity, "source": "measured", "profile": profile.as_dict(),
                   "candidates": size, "tested": size if complete else None,
                   "duration": result.duration, "complete": complete,
                   "throughput": size / result.duration if complete and result.duration > 0 else None}


def main():
    parser = argparse.ArgumentParser(description="SAGE cost calibration")
    sub = parser.add_subparsers(dest="operation", required=True)
    fit = sub.add_parser("fit")
    fit.add_argument("--input", type=Path, required=True)
    fit.add_argument("--output", type=Path, required=True)
    fit.add_argument("--batch-size", type=int, required=True)
    fit.add_argument("--window-size", type=int, default=20)
    measure = sub.add_parser("measure")
    measure.add_argument("--profile", type=Path, required=True)
    measure.add_argument("--targets", type=Path, required=True)
    measure.add_argument("--candidates", type=Path, required=True)
    measure.add_argument("--output", type=Path, required=True)
    measure.add_argument("--hashcat", default="hashcat")
    measure.add_argument("--batch-sizes", type=int, nargs="+", default=[32, 128, 512])
    measure.add_argument("--repeats", type=int, default=3)
    measure.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    try:
        # Exclusive output prevents replacing sources or earlier measurements.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.operation == "fit":
            calibrators = {}
            with args.input.open(encoding="utf-8-sig") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    sample = json.loads(line)
                    profile = VerificationProfile.from_dict(sample["profile"])
                    if profile.key not in calibrators:
                        calibrators[profile.key] = CostCalibrator(profile, window_size=args.window_size)
                    calibrators[profile.key].observe(sample)
            if not calibrators:
                raise ValueError("no calibration measurements")
            reports = [calibrators[key].report(args.batch_size) for key in sorted(calibrators)]
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump({"version": 1, "profiles": reports}, stream, ensure_ascii=False, indent=2, allow_nan=False)
        else:
            profile = VerificationProfile.from_dict(json.loads(args.profile.read_text(encoding="utf-8-sig")))
            targets = args.targets.read_text(encoding="utf-8-sig").splitlines()
            candidates = args.candidates.read_text(encoding="utf-8-sig").splitlines()
            if any(not value or "\x00" in value for value in targets + candidates):
                raise ValueError("input lines must be nonempty and NUL-free")
            adapter = HashcatAdapter(args.hashcat)
            with args.output.open("x", encoding="utf-8") as stream:
                for sample in measure_batches(adapter, profile, targets, candidates, args.batch_sizes, args.repeats, args.timeout):
                    stream.write(json.dumps(sample, ensure_ascii=False, allow_nan=False) + "\n")
                    stream.flush()
        print(f"Calibration output written to {args.output}")
    except Exception as exc:
        # Adapter errors can embed tool output/targets; avoid echoing them here.
        parser.exit(2, f"calibration failed ({type(exc).__name__}); check inputs, tool and output path\n")


if __name__ == "__main__":
    main()
