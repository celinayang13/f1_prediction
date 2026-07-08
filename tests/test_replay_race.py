"""
Tests for scripts/replay_race.py (Plan 03-04, D-02/D-03).

Covers:
  - replay_lap incrementally copies a single lap's raw_laps rows from the
    source session into the target session (not laps beyond the requested
    lap_number).
  - Re-running replay_lap for the same lap_number does not duplicate rows
    (mirrors live/poller.py's dedupe gate, T-03-09).

Uses the tmp_engine fixture (isolated SQLite, no data/f1.db writes).

Security: all SQL uses text() with bound :param names — no f-string SQL (T-03-05).
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

# Make scripts/replay_race.py importable without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from replay_race import replay_lap  # noqa: E402

SOURCE_SK = 5001
TARGET_SK = 5002
DRIVERS = [1, 11]


def _seed_source_laps(engine, n_laps: int) -> None:
    """Seed raw_laps for SOURCE_SK across laps 1..n_laps for two drivers."""
    with engine.begin() as conn:
        for lap in range(1, n_laps + 1):
            for dn in DRIVERS:
                conn.execute(
                    text(
                        "INSERT INTO raw_laps "
                        "(session_key, driver_number, lap_number, lap_duration, date_start) "
                        "VALUES (:sk, :dn, :ln, :dur, :ds)"
                    ),
                    {
                        "sk": SOURCE_SK,
                        "dn": dn,
                        "ln": lap,
                        "dur": 90.0 + lap,
                        "ds": f"2024-01-01T00:{lap:02d}:00Z",
                    },
                )


def test_replay_lap_inserts_incrementally(tmp_engine):
    """replay_lap copies exactly one lap's rows from source into target, not future laps."""
    _seed_source_laps(tmp_engine, n_laps=3)

    result = replay_lap(tmp_engine, SOURCE_SK, TARGET_SK, lap_number=1)
    assert result["laps_inserted"] == len(DRIVERS)

    with tmp_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT driver_number, lap_number FROM raw_laps WHERE session_key = :sk"),
            {"sk": TARGET_SK},
        ).fetchall()
    assert len(rows) == len(DRIVERS)
    assert all(r[1] == 1 for r in rows), "only lap 1 should be present in the target so far"

    # Lap 2 arrives — target now has laps 1 and 2 only (lap 3 has not "arrived" yet)
    replay_lap(tmp_engine, SOURCE_SK, TARGET_SK, lap_number=2)
    with tmp_engine.connect() as conn:
        lap_numbers = {
            r[0]
            for r in conn.execute(
                text("SELECT DISTINCT lap_number FROM raw_laps WHERE session_key = :sk"),
                {"sk": TARGET_SK},
            ).fetchall()
        }
    assert lap_numbers == {1, 2}


def test_replay_lap_rerun_does_not_duplicate(tmp_engine):
    """Re-running replay_lap for the same lap_number must not duplicate rows (T-03-09)."""
    _seed_source_laps(tmp_engine, n_laps=2)

    replay_lap(tmp_engine, SOURCE_SK, TARGET_SK, lap_number=1)
    with tmp_engine.connect() as conn:
        count_first = conn.execute(
            text("SELECT COUNT(*) FROM raw_laps WHERE session_key = :sk"),
            {"sk": TARGET_SK},
        ).scalar()

    # Re-run the exact same lap
    second_result = replay_lap(tmp_engine, SOURCE_SK, TARGET_SK, lap_number=1)
    assert second_result["laps_inserted"] == 0, "re-running a lap must not insert duplicate rows"

    with tmp_engine.connect() as conn:
        count_second = conn.execute(
            text("SELECT COUNT(*) FROM raw_laps WHERE session_key = :sk"),
            {"sk": TARGET_SK},
        ).scalar()

    assert count_first == count_second == len(DRIVERS)
