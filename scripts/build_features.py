"""
Standalone feature builder CLI (D-13).

Reads all raw_* tables and writes to features_prerace and features_inrace.
Sessions are processed in chronological order so cross-session aggregates
(circuit_win_rate, circuit_avg_finish, constructor_pace_delta) are computed
correctly.

Usage:
    # Rebuild features for all available raw data:
    python scripts/build_features.py

    # Rebuild features for specific years only:
    python scripts/build_features.py --only-years 2023 2024

Exit codes:
    0: success
    1: exception raised during feature build
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure src/ is on the path for non-installed runs
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from f1_prediction.features.builder import build_all_features  # noqa: E402


def main() -> None:
    """CLI entrypoint for the standalone feature builder."""
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild features_prerace and features_inrace from raw_* tables. "
            "Sessions are processed in chronological order. "
            "Existing feature rows for each session are replaced."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--only-years",
        type=int,
        nargs="+",
        default=None,
        metavar="YEAR",
        help=(
            "Restrict feature build to specific year(s). "
            "Example: --only-years 2023 2024. "
            "If omitted, all years with raw data are processed."
        ),
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    try:
        result = build_all_features(only_years=args.only_years)
        print(json.dumps(result, indent=2))
        sys.exit(0)
    except Exception as exc:
        logging.getLogger(__name__).error("Feature build failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
