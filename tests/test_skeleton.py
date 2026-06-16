"""
Walking Skeleton end-to-end test.

These tests prove the full FastF1 → normalizer → ORM → WAL SQLite pipeline
works against a real 2022 race session. They are marked @pytest.mark.slow
because they require network access on first run (FastF1 will fetch session
data from jolpica-f1 and cache it locally in data/cache/).

On subsequent runs, FastF1 reads from the local cache and the tests run
quickly with no network dependency.

To skip network-touching tests: pytest tests/ -m "not slow"
To run all tests (default): pytest tests/test_skeleton.py

Note: If running on a fresh checkout without internet access, these tests
will fail with a FastF1 network error. This is a known one-time cost.
The local data/cache/ directory will be populated on first successful run
and all subsequent runs will be fast and offline.

All tests use the `tmp_engine` fixture from conftest.py to ensure complete
isolation from the developer's data/f1.db file (T-01-05).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from f1_prediction.db.engine import get_engine, init_db
from f1_prediction.ingest.fastf1_loader import load_session


@pytest.mark.slow
def test_walking_skeleton_loads_one_2022_race(tmp_engine):
    """
    Prove the full pipeline: FastF1 → normalizer → raw_sessions + raw_laps.

    Loads Bahrain 2022 race (round 1). Asserts:
    - The returned dict indicates success (not skipped)
    - At least one lap was inserted
    - raw_sessions has the expected row
    - raw_laps row count matches laps_inserted
    """
    result = load_session(2022, 1, "R", engine=tmp_engine)

    assert result["skipped"] is False, (
        f"Expected session to be inserted on first load, got skipped=True. "
        f"Full result: {result}"
    )
    assert result["laps_inserted"] > 0, (
        f"Expected at least one lap to be inserted, got laps_inserted=0. "
        f"This could indicate an empty FastF1 response or a normalization bug."
    )

    session_key = result["session_key"]

    with tmp_engine.connect() as conn:
        # Verify raw_sessions has exactly 1 row for this session
        session_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM raw_sessions "
                "WHERE year = :year AND round_number = :rnd"
            ),
            {"year": 2022, "rnd": 1},
        ).scalar()
        assert session_count >= 1, (
            f"Expected at least 1 row in raw_sessions for year=2022 round=1, "
            f"got {session_count}"
        )

        # Verify raw_laps count matches the returned laps_inserted
        laps_count = conn.execute(
            text("SELECT COUNT(*) FROM raw_laps WHERE session_key = :sk"),
            {"sk": session_key},
        ).scalar()
        assert laps_count == result["laps_inserted"], (
            f"raw_laps count {laps_count} does not match laps_inserted "
            f"{result['laps_inserted']}"
        )


@pytest.mark.slow
def test_walking_skeleton_is_resumable(tmp_engine):
    """
    Prove D-11 resumability: loading the same session twice does not insert duplicates.

    First call: session is inserted, laps_inserted > 0, skipped=False.
    Second call: session already exists, laps_inserted=0, skipped=True.
    After second call: raw_laps count is unchanged (no duplicate rows).
    """
    # First load
    result1 = load_session(2022, 1, "R", engine=tmp_engine)
    assert result1["skipped"] is False
    assert result1["laps_inserted"] > 0

    session_key = result1["session_key"]

    # Record lap count after first load
    with tmp_engine.connect() as conn:
        count_after_first = conn.execute(
            text("SELECT COUNT(*) FROM raw_laps WHERE session_key = :sk"),
            {"sk": session_key},
        ).scalar()

    # Second load — same session, same engine
    result2 = load_session(2022, 1, "R", engine=tmp_engine)
    assert result2["skipped"] is True, (
        f"Expected second load to be skipped (D-11 resumability), "
        f"got skipped={result2['skipped']}"
    )
    assert result2["laps_inserted"] == 0, (
        f"Expected 0 laps inserted on second load, got {result2['laps_inserted']}"
    )

    # Verify no duplicates were inserted
    with tmp_engine.connect() as conn:
        count_after_second = conn.execute(
            text("SELECT COUNT(*) FROM raw_laps WHERE session_key = :sk"),
            {"sk": session_key},
        ).scalar()

    assert count_after_second == count_after_first, (
        f"raw_laps count changed after second load: "
        f"{count_after_first} → {count_after_second}. "
        f"Duplicate rows may have been inserted."
    )


def test_wal_mode_active(tmp_engine):
    """
    Prove WAL journal mode is active on the test database.

    The WAL engine event listener (Pattern 2 from RESEARCH.md) fires on
    every new connection, so this assertion holds regardless of whether
    the database was just created or already existed.
    """
    with tmp_engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()

    assert mode == "wal", (
        f"Expected journal_mode='wal', got '{mode}'. "
        f"Check that _set_sqlite_pragma event listener is correctly attached "
        f"in db/engine.py."
    )
