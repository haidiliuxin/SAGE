"""Export an existing research log without running an experiment."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from ..decision.research_log import SqliteResearchLog


def main() -> None:
    parser = argparse.ArgumentParser(description="Export SAGE research events to JSONL")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("research database does not exist")
    try:
        count = SqliteResearchLog(args.database, read_only=True).export_jsonl(args.output, run_id=args.run_id)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(2, f"export failed: {exc}\n")
    print(f"Exported {count} events to {args.output}")


if __name__ == "__main__":
    main()
