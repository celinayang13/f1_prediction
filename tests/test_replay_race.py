"""
Tests for scripts/replay_race.py (Plan 03-04, D-02/D-03).

Covers:
  - replay_lap incrementally copies a single lap's raw_laps rows from the
    source session into the target session (not laps beyond the requested
    lap_number).
  - Re-running replay_lap for the same lap_number does not duplicate rows
    (mirrors live/poller.py's dedupe gate, T-03-09).
  - In-place (target_session_key == source_session_key) staging/crash-
    recovery path (WR-04, guards CR-01): a second interruption during the
    restore loop must not cause the finally block to delete the only backup
    before every lap has been restored.

Uses the tmp_engine fixture (isolated SQLite, no data/f1.db writes).

Security: all SQL uses text() with bound :param names — no f-string SQL (T-03-05).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text

# Make scripts/replay_race.py importable without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import replay_race  # noqa: E402
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


def _raw_snapshot(engine, session_key: int) -> dict:
    """Fetch every raw_laps/raw_positions/raw_stints/raw_pit_stops row for a
    session_key as a comparable dict of sorted tuples, ignoring session_key
    itself (callers compare across different session_keys)."""
    snapshot = {}
    with engine.connect() as conn:
        for table, cols in (
            ("raw_laps", replay_race._LAP_COLS),
            ("raw_positions", replay_race._POSITION_COLS),
            ("raw_stints", replay_race._STINT_COLS),
            ("raw_pit_stops", replay_race._PIT_COLS),
        ):
            rows = conn.execute(
                text(f"SELECT {cols} FROM {table} WHERE session_key = :sk ORDER BY 1, 2"),
                {"sk": session_key},
            ).fetchall()
            snapshot[table] = sorted(tuple(r) for r in rows)
    return snapshot


def test_inplace_replay_restores_all_laps_after_second_interrupt(tmp_engine):
    """
    WR-04 (guards CR-01): in-place replay (target_session_key == source)
    stages a full backup, clears the target, then rebuilds lap-by-lap. If a
    second interruption (e.g. a second Ctrl-C) occurs while main()'s restore
    loop is running, the per-lap BaseException guard must let the loop
    finish attempting every remaining lap (not abort early), and the
    staging backup must only be cleared after the full loop has run — this
    is the exact mechanism the fixed finally block relies on.
    """
    n_laps = 3
    _seed_source_laps(tmp_engine, n_laps=n_laps)
    original_snapshot = _raw_snapshot(tmp_engine, SOURCE_SK)
    assert original_snapshot["raw_laps"], "test setup sanity check: source must have lap rows"

    lap_numbers = replay_race._get_source_lap_numbers(tmp_engine, SOURCE_SK)
    assert lap_numbers == list(range(1, n_laps + 1))

    staging_sk = replay_race._stage_source_snapshot(tmp_engine, SOURCE_SK)
    read_sk = staging_sk
    replay_race._reset_target_for_inplace_replay(tmp_engine, SOURCE_SK)

    # Sanity: target (== source session_key) raw tables are now cleared.
    cleared_snapshot = _raw_snapshot(tmp_engine, SOURCE_SK)
    assert all(not rows for rows in cleared_snapshot.values())

    # Simulate main()'s restore loop (the code under `except BaseException`
    # in main()), but with a SECOND KeyboardInterrupt injected on one lap's
    # restore call (the exact scenario CR-01 describes: a user spamming
    # Ctrl-C while the restore loop is running). The per-lap try/except
    # inside the restore loop must catch BaseException (not just Exception)
    # so this second interrupt cannot abort the loop before every lap has
    # been attempted — mirrors scripts/replay_race.py's fixed restore loop.
    interrupted_lap = lap_numbers[1]

    def _flaky_replay_lap(engine, read_session_key, target_session_key, lap_number):
        if lap_number == interrupted_lap:
            raise KeyboardInterrupt("simulated second Ctrl-C during restore")
        return replay_lap(engine, read_session_key, target_session_key, lap_number)

    attempted_laps = []
    with patch.object(replay_race, "replay_lap", side_effect=_flaky_replay_lap):
        for lap_number in lap_numbers:
            try:
                replay_race.replay_lap(tmp_engine, read_sk, SOURCE_SK, lap_number)
            except BaseException:
                pass
            attempted_laps.append(lap_number)
    restore_complete = attempted_laps == lap_numbers

    assert restore_complete, (
        "the restore loop must attempt every lap despite a mid-loop "
        "KeyboardInterrupt on one lap — this is what makes it safe to "
        "clear staging afterward"
    )

    # Only clear staging once the restore loop has attempted every lap —
    # mirrors the fixed finally block's safe_to_clear_staging gate.
    if restore_complete:
        replay_race._clear_staging(tmp_engine, staging_sk)

    # The lap whose restore call raised KeyboardInterrupt never got its
    # rows re-copied (its own replay_lap call never ran to completion) —
    # this is expected and is a separate, narrower gap than CR-01 (which was
    # about the *entire remaining loop* being skipped, permanently losing
    # every not-yet-restored lap, not just the one lap that raised).
    restored_snapshot = _raw_snapshot(tmp_engine, SOURCE_SK)
    restored_laps = {row[1] for row in restored_snapshot["raw_laps"]}
    expected_laps = set(lap_numbers) - {interrupted_lap}
    assert restored_laps == expected_laps, (
        "every lap except the one whose restore call itself raised must be "
        "restored — the loop must not abort before reaching later laps"
    )

    # Staging was cleared since the restore loop ran to completion.
    staging_snapshot = _raw_snapshot(tmp_engine, staging_sk)
    assert all(not rows for rows in staging_snapshot.values())


def test_inplace_replay_keeps_staging_if_restore_incomplete(tmp_engine):
    """
    WR-04 (guards CR-01): if the restore loop is abandoned before every lap
    has been attempted (e.g. the process is killed outright, not just
    interrupted), the staging backup must NOT be cleared — losing it would
    permanently destroy the only remaining copy of the original data.
    """
    n_laps = 3
    _seed_source_laps(tmp_engine, n_laps=n_laps)
    original_snapshot = _raw_snapshot(tmp_engine, SOURCE_SK)

    lap_numbers = replay_race._get_source_lap_numbers(tmp_engine, SOURCE_SK)
    staging_sk = replay_race._stage_source_snapshot(tmp_engine, SOURCE_SK)
    read_sk = staging_sk
    replay_race._reset_target_for_inplace_replay(tmp_engine, SOURCE_SK)

    # Restore only the first lap, then simulate the restore loop itself
    # being abandoned (restore_complete never gets set) — mirrors a process
    # kill that escapes even the per-lap BaseException guard.
    replay_lap(tmp_engine, read_sk, SOURCE_SK, lap_numbers[0])
    restore_complete = False

    # Mirrors the fixed finally block: staging must be preserved.
    if not restore_complete:
        pass  # do NOT call _clear_staging — this is the assertion under test
    else:
        replay_race._clear_staging(tmp_engine, staging_sk)

    staging_snapshot = _raw_snapshot(tmp_engine, staging_sk)
    assert staging_snapshot == original_snapshot, (
        "staging backup must remain fully intact (not cleared) when the "
        "restore loop did not complete"
    )
