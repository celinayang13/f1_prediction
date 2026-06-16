"""
Backfill script — load one F1 session from FastF1 into the local SQLite database.

Usage:
    python scripts/backfill.py --year 2022 --round 1
    python scripts/backfill.py --year 2022 --round 1 --identifier Q
    python scripts/backfill.py --year 2022 --round 1 --identifier FP2

This script is the CLI entrypoint for the Phase 1 walking skeleton. It loads
a single session from FastF1 and stores raw lap data in data/f1.db.

Plan 02 will expand this script to iterate over all sessions in a season.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure the src/ directory is on the path so f1_prediction.* imports work
# when running as `python scripts/backfill.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from f1_prediction.ingest.fastf1_loader import load_session  # noqa: E402


def main() -> None:
    """CLI entrypoint for the backfill script."""
    parser = argparse.ArgumentParser(
        description="Load one F1 session from FastF1 into the local SQLite database.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--year",
        type=int,
        required=True,
        help="Season year (must be <= 2022 for FastF1; use OpenF1 for 2023+)",
    )
    parser.add_argument(
        "--round",
        type=int,
        required=True,
        dest="round_number",
        help="Round number in the season (1-based)",
    )
    parser.add_argument(
        "--identifier",
        type=str,
        default="R",
        help="Session identifier: R=Race, Q=Qualifying, FP2, FP3, S=Sprint, SS=Sprint Shootout",
    )

    args = parser.parse_args()

    # Configure root logger to INFO with a simple timestamp format
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    result = load_session(args.year, args.round_number, args.identifier)
    print(json.dumps(result, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
