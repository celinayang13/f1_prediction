"""
Backfill script — load F1 sessions from FastF1 (2022) or OpenF1 (2023+)
into the local SQLite database.

Usage:
    # Backfill 2022 Round 1 Race via FastF1:
    python scripts/backfill.py --years 2022 --rounds 1 --identifiers R

    # Backfill 2024 Round 1 Race via OpenF1:
    python scripts/backfill.py --years 2024 --rounds 1 --identifiers R

    # Multi-year, multiple rounds:
    python scripts/backfill.py --years 2022 2023 --rounds 1 2 3

    # Full season (all rounds, all default identifiers):
    python scripts/backfill.py --years 2022

    # Skip feature builder at end (for raw data only):
    python scripts/backfill.py --years 2022 --skip-features

Routing:
  year <= 2022  →  FastF1 loader (fastf1_loader.load_year)
  year >= 2023  →  OpenF1 loader (openf1_loader.load_openf1_year)

Resumability (D-11):
  Sessions already in raw_sessions are skipped automatically by both loaders.
  Re-running backfill after interruption is safe.

Exit codes:
  0: all sessions loaded without exception
  1: one or more sessions raised an exception (logged; backfill continues)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

# Ensure src/ is on the path for non-installed runs
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402

from f1_prediction.db.engine import get_engine  # noqa: E402
from f1_prediction.features.builder import build_all_features  # noqa: E402
from f1_prediction.ingest import fastf1_loader, openf1_loader  # noqa: E402

logger = logging.getLogger(__name__)


def migrate_remove_year_round_type_constraint(engine) -> None:
    """
    One-time migration: drop the UNIQUE(year, round_number, session_type)
    constraint from raw_sessions.

    This constraint was added in Phase 1 as a defence against duplicate inserts
    (D-11).  It is harmful for the OpenF1 backfill path because OpenF1's
    /v1/sessions endpoint does not return a sequential round_number — the
    normalizer falls back to 0, so every session from the same year and type
    would collide on INSERT (only the first succeeds, all subsequent sessions
    are silently skipped).

    The session_key PRIMARY KEY already guarantees row uniqueness.
    fix_round_numbers() corrects round_number values after all sessions for the
    year are loaded.

    SQLite does not support ALTER TABLE DROP CONSTRAINT.  The migration
    recreates raw_sessions without the constraint.  The operation is atomic
    (single transaction) and idempotent — if the constraint is already absent,
    the function is a no-op.

    Security: all SQL uses sqlalchemy.text() with no user-supplied parameters
    (table/column names are hard-coded literals; T-03-05).
    """
    with engine.connect() as conn:
        # Check whether the constraint still exists
        sql_text = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='raw_sessions'")
        ).scalar()
        if sql_text is None or "uq_raw_sessions_year_round_type" not in sql_text:
            logger.debug(
                "migrate_remove_year_round_type_constraint: constraint already absent — skipping"
            )
            return

    logger.info(
        "migrate_remove_year_round_type_constraint: recreating raw_sessions without "
        "UNIQUE(year, round_number, session_type)"
    )
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE raw_sessions_new (
                session_key INTEGER NOT NULL,
                year INTEGER NOT NULL,
                round_number INTEGER NOT NULL,
                country_name VARCHAR,
                circuit_short_name VARCHAR,
                session_type VARCHAR NOT NULL,
                session_name VARCHAR,
                date_start VARCHAR,
                date_end VARCHAR,
                total_laps INTEGER,
                source VARCHAR NOT NULL,
                location VARCHAR,
                meeting_key INTEGER,
                circuit_key INTEGER,
                gmt_offset VARCHAR,
                PRIMARY KEY (session_key)
            )
        """))
        conn.execute(text("INSERT INTO raw_sessions_new SELECT * FROM raw_sessions"))
        conn.execute(text("DROP TABLE raw_sessions"))
        conn.execute(text("ALTER TABLE raw_sessions_new RENAME TO raw_sessions"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_raw_sessions_year ON raw_sessions (year)"))

    logger.info("migrate_remove_year_round_type_constraint: migration complete")


def fix_round_numbers(engine) -> None:
    """
    Derive round_number for OpenF1 sessions (year >= 2023) from meeting_key
    rank within year.

    OpenF1 /v1/sessions does not return a round_number field; the normalizer
    falls back to 0.  This function ranks each unique meeting_key ascending
    within its year (starting at 1) and writes the result back to raw_sessions.

    Sessions sharing the same meeting_key (e.g. Q, FP2, FP3, R from the same
    Grand Prix weekend) receive the same round_number.

    This function is idempotent — re-running it always produces the same
    round_number assignments because meeting_key order within a year is stable.

    Security: all SQL uses sqlalchemy.text() with bound :param names (T-03-05).
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT session_key, year, meeting_key
                FROM raw_sessions
                WHERE year >= 2023 AND meeting_key IS NOT NULL
                ORDER BY year, meeting_key
                """
            )
        ).fetchall()

        if not rows:
            logger.info("fix_round_numbers: no 2023+ sessions with meeting_key found; nothing to update")
            return

        # Collect unique meeting_keys per year (preserving order from ORDER BY meeting_key)
        year_meeting_keys: dict = defaultdict(list)
        for _sk, year, mk in rows:
            if mk not in year_meeting_keys[year]:
                year_meeting_keys[year].append(mk)

        # Build (year, meeting_key) → rank mapping
        year_meeting_rank: dict = {}
        for year, mks in year_meeting_keys.items():
            for rank, mk in enumerate(sorted(set(mks)), start=1):
                year_meeting_rank[(year, mk)] = rank

        # Write round_number for each session
        updated = 0
        for sk, year, mk in rows:
            rn = year_meeting_rank.get((year, mk), 0)
            conn.execute(
                text("UPDATE raw_sessions SET round_number = :rn WHERE session_key = :sk"),
                {"rn": rn, "sk": sk},
            )
            updated += 1

        n_years = len(year_meeting_keys)
        logger.info("round_number fix applied to %d sessions across %d years", updated, n_years)


def main() -> None:
    """CLI entrypoint for the backfill script."""
    parser = argparse.ArgumentParser(
        description=(
            "Backfill F1 session data into the local SQLite database. "
            "Routes year <= 2022 → FastF1; year >= 2023 → OpenF1."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        required=True,
        help=(
            "Season year(s) to backfill. "
            "Accepts multiple values: --years 2022 2023 2024. "
            "year <= 2022 uses FastF1; year >= 2023 uses OpenF1."
        ),
    )
    parser.add_argument(
        "--rounds",
        type=int,
        nargs="+",
        default=None,
        help=(
            "Round number(s) to load. If omitted, all rounds in the season are loaded. "
            "For OpenF1 years, rounds are matched against meeting_key."
        ),
    )
    parser.add_argument(
        "--identifiers",
        type=str,
        nargs="+",
        default=["FP2", "FP3", "Q", "R", "S", "SS"],
        help=(
            "Session identifier(s) to load. "
            "Defaults: FP2 FP3 Q R S SS (FP1 is intentionally excluded per D-06). "
            "Example: --identifiers R Q"
        ),
    )
    parser.add_argument(
        "--skip-features",
        action="store_true",
        default=False,
        help=(
            "Skip the feature builder step after backfill. "
            "No-op in Phase 1 (feature builder not yet implemented). "
            "Plan 03 will wire the feature builder call."
        ),
    )

    args = parser.parse_args()

    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # One-time schema migration: remove UNIQUE(year, round_number, session_type)
    # which blocks multi-round OpenF1 backfill (all sessions have round_number=0
    # from the API). Idempotent — no-op if already migrated.
    if any(y >= 2023 for y in args.years):
        migrate_remove_year_round_type_constraint(get_engine())

    had_error = False

    for year in args.years:
        logger.info("Starting backfill for year=%d", year)

        if year <= 2022:
            # FastF1 path
            results = fastf1_loader.load_year(
                year,
                rounds=args.rounds,
                identifiers=args.identifiers,
            )
        else:
            # OpenF1 path (year >= 2023)
            results = openf1_loader.load_openf1_year(
                year,
                rounds=args.rounds,
                identifiers=args.identifiers,
            )

        for r in results:
            if r.get("error"):
                had_error = True

            # Emit one JSON line per session to stdout (greppable)
            output = {
                "year": r.get("year", year),
                "session_key": r.get("session_key"),
                "skipped": r.get("skipped", False),
                "error": r.get("error", False),
                "laps_inserted": r.get("laps_inserted", 0),
                "stints_inserted": r.get("stints_inserted", 0),
                "pits_inserted": r.get("pits_inserted", 0),
                "positions_inserted": r.get("positions_inserted", 0),
                "weather_inserted": r.get("weather_inserted", 0),
            }
            print(json.dumps(output))

    # Fix round_number=0 for 2023+ sessions before feature build (D-01, Pitfall 1)
    if any(y >= 2023 for y in args.years):
        fix_round_numbers(get_engine())
        logger.info("round_number fix applied for 2023+ sessions")

    # Feature builder: run after all sessions are backfilled (D-13)
    if args.skip_features:
        logger.info("--skip-features specified; skipping feature builder")
    else:
        logger.info("Running feature builder (use --skip-features to skip)...")
        try:
            feature_result = build_all_features(only_years=args.years)
            logger.info("Feature builder complete: %s", feature_result)
            print(json.dumps({"feature_build": feature_result}))
        except Exception as exc:
            logger.error("Feature builder failed: %s", exc, exc_info=True)
            had_error = True

    sys.exit(1 if had_error else 0)


if __name__ == "__main__":
    main()
