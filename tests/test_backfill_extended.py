"""
Integration tests verifying Plan 02-01's backfill extension.

Reads real data/f1.db — NEVER writes to it.
All tests are skipped if data/f1.db has fewer than 100 rows total in
features_prerace (i.e. Plan 02-01 backfill has not been run yet).

These tests provide a permanent regression gate so future changes to the
backfill or feature builder are caught before breaking model training.

Usage:
    PYTHONPATH=src python3.11 -m pytest tests/test_backfill_extended.py -v
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from f1_prediction.db.engine import get_engine


# ---------------------------------------------------------------------------
# Module-scoped fixture — uses the real project database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_engine():
    """Return a SQLAlchemy Engine connected to the real data/f1.db."""
    return get_engine()


# ---------------------------------------------------------------------------
# Skip helper
# ---------------------------------------------------------------------------


def _skip_if_no_data(engine) -> None:
    """
    Skip the calling test if features_prerace has fewer than 100 rows.

    This guard prevents false failures when the Plan 02-01 backfill has not
    been run yet.  Tests call this at the top of their body.
    """
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM features_prerace")
        ).scalar()
    if count < 100:
        pytest.skip(
            f"data/f1.db not populated — run Plan 02-01 backfill "
            f"(features_prerace has {count} rows, need >= 100)"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_round_numbers_populated(real_engine):
    """
    All raw_sessions rows where year >= 2023 must have a non-NULL,
    non-zero round_number.

    Failure here means fix_round_numbers() was not run or did not complete
    successfully.
    """
    _skip_if_no_data(real_engine)

    with real_engine.connect() as conn:
        bad_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM raw_sessions "
                "WHERE year >= 2023 AND (round_number IS NULL OR round_number = 0)"
            )
        ).scalar()

    assert bad_count == 0, (
        f"{bad_count} raw_sessions rows have NULL/0 round_number for year >= 2023. "
        "Run fix_round_numbers() via scripts/backfill.py --years 2023 2024 2025."
    )


def test_prerace_rows_2023(real_engine):
    """
    features_prerace must have > 100 rows for 2023 race sessions.

    Minimum 100 = ~5 races × 20 drivers.  A full 2023 season (23 races)
    should produce ~460 rows.
    """
    _skip_if_no_data(real_engine)

    with real_engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) "
                "FROM features_prerace fp "
                "JOIN raw_sessions rs ON fp.session_key = rs.session_key "
                "WHERE rs.year = :yr AND rs.session_type = 'Race'"
            ),
            {"yr": 2023},
        ).scalar()

    assert count > 100, (
        f"Expected > 100 features_prerace rows for 2023 race sessions, got {count}. "
        "Run scripts/backfill.py --years 2023 to populate 2023 data."
    )


def test_prerace_rows_2024(real_engine):
    """
    features_prerace must have > 100 rows for 2024 race sessions.

    Same threshold as 2023 — a full 2024 season (24 races) should produce
    ~480 rows.
    """
    _skip_if_no_data(real_engine)

    with real_engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) "
                "FROM features_prerace fp "
                "JOIN raw_sessions rs ON fp.session_key = rs.session_key "
                "WHERE rs.year = :yr AND rs.session_type = 'Race'"
            ),
            {"yr": 2024},
        ).scalar()

    assert count > 100, (
        f"Expected > 100 features_prerace rows for 2024 race sessions, got {count}. "
        "Run scripts/backfill.py --years 2024 to populate 2024 data."
    )


def test_championship_position_populated_for_round_2_plus(real_engine):
    """
    championship_position NULL rate must be < 10% for year >= 2023 AND
    round_number >= 2 race sessions.

    Round 1 of each season is always NULL (no prior rounds exist).  All
    subsequent rounds should have a valid championship_position.
    """
    _skip_if_no_data(real_engine)

    with real_engine.connect() as conn:
        null_rate = conn.execute(
            text(
                "SELECT "
                "  1.0 * SUM(CASE WHEN fp.championship_position IS NULL THEN 1 ELSE 0 END) "
                "  / COUNT(*) "
                "FROM features_prerace fp "
                "JOIN raw_sessions rs ON fp.session_key = rs.session_key "
                "WHERE rs.year >= 2023 "
                "  AND rs.round_number >= 2 "
                "  AND rs.session_type = 'Race'"
            )
        ).scalar()

    # NULL rate may be None if no qualifying rows exist — treat as 0
    null_rate = null_rate or 0.0

    assert null_rate < 0.10, (
        f"championship_position null rate for round >= 2 races in 2023+ is "
        f"{null_rate:.2%}, expected < 10%. "
        "This usually means fix_round_numbers() did not run before the feature rebuild."
    )


def test_round_numbers_unique_per_year_meeting(real_engine):
    """
    Within each (year, round_number) group for year >= 2023, all sessions
    must share a single meeting_key (Q + R + FP2 + FP3 for the same Grand
    Prix weekend all get the same round_number).

    Failure means fix_round_numbers() assigned different round_numbers to
    sessions from the same meeting weekend.
    """
    _skip_if_no_data(real_engine)

    with real_engine.connect() as conn:
        bad_rows = conn.execute(
            text(
                "SELECT year, round_number, COUNT(DISTINCT meeting_key) AS mk_count "
                "FROM raw_sessions "
                "WHERE year >= 2023 AND round_number > 0 "
                "GROUP BY year, round_number "
                "HAVING mk_count > 1"
            )
        ).fetchall()

    assert len(bad_rows) == 0, (
        f"Found {len(bad_rows)} (year, round_number) group(s) with multiple distinct "
        f"meeting_keys in raw_sessions for year >= 2023: {bad_rows}. "
        "Sessions from the same race weekend must share one meeting_key and one round_number."
    )


def test_features_inrace_has_2023_data(real_engine):
    """
    features_inrace must have > 5000 rows for 2023 sessions.

    Minimum 5000 = ~4 races × 60 laps × 20 drivers.  A full 2023 season
    should produce ~27,600 rows.
    """
    _skip_if_no_data(real_engine)

    with real_engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) "
                "FROM features_inrace fi "
                "JOIN raw_sessions rs ON fi.session_key = rs.session_key "
                "WHERE rs.year = :yr"
            ),
            {"yr": 2023},
        ).scalar()

    assert count > 5000, (
        f"Expected > 5000 features_inrace rows for 2023 sessions, got {count}. "
        "Run scripts/backfill.py --years 2023 followed by the feature builder."
    )
