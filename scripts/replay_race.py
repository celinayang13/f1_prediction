"""
Replay dev tool — re-insert a completed historical race's raw_* rows into
the raw_* tables incrementally on a timer, simulating live lap arrival
(Plan 03-04, D-02/D-03).

Purpose: pipeline.py's poll -> inference -> dashboard chain cannot be
exercised against a real live OpenF1 session until Phase 4. This script
drives the same chain against a completed historical race instead, so the
in-race inference + dashboard auto-refresh (Plan 03-03) can be watched
updating lap-by-lap before Phase 4.

Usage:
    # Fast replay (5s/lap default) in place, into the same session:
    python scripts/replay_race.py --source-session-key 9159

    # Realistic pacing, into a distinct scratch target session:
    python scripts/replay_race.py --source-session-key 9159 \\
        --target-session-key 999999999 --interval 90

    # Replay against a scratch copy of the database (never touches the
    # real project DB at all):
    python scripts/replay_race.py --source-session-key 9159 --db /tmp/scratch.db

Important — how in-race inference gets triggered:
  pipeline.py's own BackgroundScheduler poll loop calls live/poller.py's
  poll_once(), which gates every cycle on
  live/session_window.py::is_session_active(). That check always queries
  the REAL OpenF1 API for the session's actual date_start/date_end and
  compares against wall-clock "now" — for any already-completed historical
  race this is always False, so pipeline.py's poll loop can never observe
  a replayed race and will never fire in-race inference on its own.
  This script therefore calls inference.inrace.compute_and_store_inrace_
  latest_lap() directly after inserting each lap's rows, so the dashboard's
  In-Race tab (which only reads the predictions table) updates regardless
  of whether pipeline.py's own poll loop is running. Running
  `python -m src.f1_prediction.pipeline` alongside this script is still
  useful — it computes the one-time pre-race predictions the Pre-Race tab
  needs — but its poll loop is a no-op during replay.

Important — in-place replay (default --target-session-key = --source-session-key):
  T-03-09: the default replays "in place", directly into the same session
  the source data comes from. Because a completed historical race already
  has every lap in raw_laps/raw_positions/raw_stints/raw_pit_stops, an
  in-place replay first clears that session's raw_* rows (and any stale
  'inrace' predictions) and incrementally rebuilds them lap by lap — the
  only way to make an already-complete race "arrive" over time again. Every
  lap is restored by the time the replay completes normally, and *all*
  remaining laps are restored immediately if the replay is interrupted
  (Ctrl-C or any exception), so the source race's data is never left
  permanently incomplete. Pass a distinct --target-session-key, or --db
  pointing at a scratch copy of the database, to avoid touching this
  session's real data at all (note: a distinct --target-session-key alone
  still shares the same live project DB with the dashboard/pipeline
  processes -- only --db gives full isolation, at the cost of the
  dashboard/pipeline no longer seeing the replayed data, since they always
  open the project's default DB).

Security: no f-string SQL — all SQL uses text() with bound :param names
(T-03-05). Reuses get_engine() (never a second engine factory) and
bulk_insert_rows() (never a hand-rolled chunked insert).
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

# Ensure src/ is on the path for non-installed runs
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from f1_prediction.db.engine import get_engine  # noqa: E402
from f1_prediction.db.upsert import bulk_insert_rows  # noqa: E402
from f1_prediction.inference.inrace import compute_and_store_inrace_latest_lap  # noqa: E402

logger = logging.getLogger(__name__)

__all__ = ["replay_lap", "main"]

# Whitelisted columns re-inserted per table (mirrors live/poller.py's
# per-field whitelist convention; excludes each table's autoincrement id).
_LAP_COLS = (
    "driver_number, lap_number, lap_duration, duration_sector_1, "
    "duration_sector_2, duration_sector_3, is_pit_out_lap, i1_speed, "
    "i2_speed, st_speed, date_start, is_pit_in_lap, tyre_compound_observed, "
    "segments_sector_1, segments_sector_2, segments_sector_3"
)
_POSITION_COLS = "driver_number, lap_number, position, date_recorded"
_STINT_COLS = "driver_number, stint_number, lap_start, lap_end, compound, tyre_age_at_start"
_PIT_COLS = "driver_number, lap_number, lane_duration, stop_duration, date_recorded"


def _get_source_lap_numbers(engine: Engine, source_session_key: int) -> list[int]:
    """Return the DISTINCT ordered lap_number values present in raw_laps for the source session."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT lap_number FROM raw_laps "
                "WHERE session_key = :sk ORDER BY lap_number"
            ),
            {"sk": source_session_key},
        ).fetchall()
    return [int(r[0]) for r in rows]


def _ensure_target_session_row(engine: Engine, source_session_key: int, target_session_key: int) -> None:
    """
    Copy the source's raw_sessions row to the target session_key if the
    target session_key differs and has no row yet.

    build_features_for_session (called inside
    compute_and_store_inrace_latest_lap) looks up session_type via
    raw_sessions and silently returns 0 rows if none is found for the given
    session_key — without this, a distinct --target-session-key would never
    produce any in-race predictions.

    No-op if target_session_key == source_session_key (row already exists)
    or a target row is already present.
    """
    if target_session_key == source_session_key:
        return
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM raw_sessions WHERE session_key = :sk"),
            {"sk": target_session_key},
        ).fetchone()
        if existing is not None:
            return
        source_row = conn.execute(
            text(
                "SELECT year, round_number, country_name, circuit_short_name, "
                "session_type, session_name, date_start, date_end, total_laps, "
                "source, location, meeting_key, circuit_key, gmt_offset "
                "FROM raw_sessions WHERE session_key = :sk"
            ),
            {"sk": source_session_key},
        ).mappings().fetchone()

    if source_row is None:
        logger.warning(
            "replay_race: source session %d not found in raw_sessions — cannot "
            "copy a session row for target %d",
            source_session_key, target_session_key,
        )
        return

    row = dict(source_row)
    row["session_key"] = target_session_key
    bulk_insert_rows(engine, "raw_sessions", [row])
    logger.info(
        "replay_race: copied raw_sessions row from source %d to target %d",
        source_session_key, target_session_key,
    )


# Offset added to a source session_key to build a staging session_key for
# in-place replay (T-03-09). Real OpenF1/FastF1 session_keys are small
# integers (low thousands) — this offset is astronomically unlikely to
# collide with any real session_key, and SQLite's dynamically-typed
# INTEGER column comfortably holds the result (64-bit signed range).
_STAGING_OFFSET = 900_000_000_000


def _stage_source_snapshot(engine: Engine, source_session_key: int) -> int:
    """
    Copy source_session_key's full raw_laps/raw_positions/raw_stints/
    raw_pit_stops rows into a staging session_key
    (source_session_key + _STAGING_OFFSET) *before* any destructive delete.

    In-place replay (target == source) must clear the original session's
    raw_* rows so they can be rebuilt incrementally — but replay_lap reads
    "source" data from the very same table it is about to rewrite for
    "target" when the two keys are identical. Staging a full copy first
    means the rebuild loop always reads from the untouched staging copy
    (read_session_key = staging key), never from the row set it is
    currently deleting/rebuilding, so no data is ever lost even if a crash
    happens between staging and the first lap of the rebuild.

    Any stale staging rows from a previous interrupted run are cleared
    first (idempotent).

    Returns:
        The staging session_key to pass as `source_session_key` to
        replay_lap for the remainder of the in-place replay.
    """
    staging_sk = source_session_key + _STAGING_OFFSET
    with engine.connect() as conn:
        laps = conn.execute(
            text(f"SELECT {_LAP_COLS} FROM raw_laps WHERE session_key = :sk"),
            {"sk": source_session_key},
        ).mappings().fetchall()
        positions = conn.execute(
            text(f"SELECT {_POSITION_COLS} FROM raw_positions WHERE session_key = :sk"),
            {"sk": source_session_key},
        ).mappings().fetchall()
        stints = conn.execute(
            text(f"SELECT {_STINT_COLS} FROM raw_stints WHERE session_key = :sk"),
            {"sk": source_session_key},
        ).mappings().fetchall()
        pits = conn.execute(
            text(f"SELECT {_PIT_COLS} FROM raw_pit_stops WHERE session_key = :sk"),
            {"sk": source_session_key},
        ).mappings().fetchall()

        # Clear any stale staging rows from a previously interrupted run.
        conn.execute(text("DELETE FROM raw_laps WHERE session_key = :sk"), {"sk": staging_sk})
        conn.execute(text("DELETE FROM raw_positions WHERE session_key = :sk"), {"sk": staging_sk})
        conn.execute(text("DELETE FROM raw_stints WHERE session_key = :sk"), {"sk": staging_sk})
        conn.execute(text("DELETE FROM raw_pit_stops WHERE session_key = :sk"), {"sk": staging_sk})
        conn.commit()

    bulk_insert_rows(engine, "raw_laps", [{**dict(r), "session_key": staging_sk} for r in laps])
    bulk_insert_rows(engine, "raw_positions", [{**dict(r), "session_key": staging_sk} for r in positions])
    bulk_insert_rows(engine, "raw_stints", [{**dict(r), "session_key": staging_sk} for r in stints])
    bulk_insert_rows(engine, "raw_pit_stops", [{**dict(r), "session_key": staging_sk} for r in pits])
    logger.info(
        "replay_race: staged %d lap rows for source session %d under staging "
        "session %d before clearing the original for in-place replay",
        len(laps), source_session_key, staging_sk,
    )
    return staging_sk


def _clear_staging(engine: Engine, staging_session_key: int) -> None:
    """Remove the temporary staging snapshot created by _stage_source_snapshot."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM raw_laps WHERE session_key = :sk"), {"sk": staging_session_key})
        conn.execute(text("DELETE FROM raw_positions WHERE session_key = :sk"), {"sk": staging_session_key})
        conn.execute(text("DELETE FROM raw_stints WHERE session_key = :sk"), {"sk": staging_session_key})
        conn.execute(text("DELETE FROM raw_pit_stops WHERE session_key = :sk"), {"sk": staging_session_key})


def _reset_target_for_inplace_replay(engine: Engine, target_session_key: int) -> None:
    """
    Clear a target session's raw_laps/raw_positions/raw_stints/raw_pit_stops
    rows and any stale 'inrace' predictions, so an in-place replay (target
    == source) can rebuild the race incrementally from lap 1 (T-03-09).

    Only called when --target-session-key defaults to
    --source-session-key, and only *after* _stage_source_snapshot has
    copied the original data to a staging session_key. The cleared data is
    re-created lap by lap (read from staging) as the replay runs; main()'s
    exception handler restores every remaining lap immediately if the
    replay is interrupted, so the session's data is never left permanently
    incomplete.

    Security: no f-string SQL — text() with bound :param only (T-03-05).
    """
    logger.warning(
        "replay_race: in-place replay for session %d — clearing and "
        "incrementally rebuilding raw_laps/raw_positions/raw_stints/"
        "raw_pit_stops (a full backup was already staged). The full race is "
        "restored by the time the replay completes (or immediately on "
        "interruption). Pass a distinct --target-session-key or --db to "
        "avoid touching this session's data at all.",
        target_session_key,
    )
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM raw_laps WHERE session_key = :sk"), {"sk": target_session_key})
        conn.execute(text("DELETE FROM raw_positions WHERE session_key = :sk"), {"sk": target_session_key})
        conn.execute(text("DELETE FROM raw_stints WHERE session_key = :sk"), {"sk": target_session_key})
        conn.execute(text("DELETE FROM raw_pit_stops WHERE session_key = :sk"), {"sk": target_session_key})
        conn.execute(
            text(
                "DELETE FROM predictions WHERE session_key = :sk "
                "AND prediction_type = 'inrace'"
            ),
            {"sk": target_session_key},
        )


def replay_lap(
    engine: Engine, source_session_key: int, target_session_key: int, lap_number: int
) -> dict:
    """
    Replay a single lap: copy that lap's raw_laps + raw_positions rows, and
    refresh the raw_stints/raw_pit_stops snapshot (all stint/pit rows known
    through this lap), from source_session_key into target_session_key.

    Idempotent (mirrors live/poller.py's dedupe gate, T-03-09): raw_laps/
    raw_positions rows are skipped if a matching (driver_number, lap_number)
    row already exists for the target; raw_stints/raw_pit_stops are always
    DELETE-then-INSERT for the up-to-this-lap snapshot (mirrors poller's
    _replace_stints), so re-running the same lap never duplicates a row.

    Args:
        engine: SQLAlchemy Engine.
        source_session_key: Completed historical Race session_key to read from.
        target_session_key: Session_key to insert rows into.
        lap_number: The lap to replay.

    Returns:
        Dict: {lap_number, laps_inserted, positions_inserted, stints_replaced,
        pits_inserted}.
    """
    with engine.connect() as conn:
        # --- raw_laps: dedupe on (driver_number, lap_number) ---
        source_laps = conn.execute(
            text(f"SELECT {_LAP_COLS} FROM raw_laps WHERE session_key = :sk AND lap_number = :ln"),
            {"sk": source_session_key, "ln": lap_number},
        ).mappings().fetchall()
        existing_lap_drivers = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT driver_number FROM raw_laps "
                    "WHERE session_key = :sk AND lap_number = :ln"
                ),
                {"sk": target_session_key, "ln": lap_number},
            ).fetchall()
        }
        new_laps = [
            {**dict(r), "session_key": target_session_key}
            for r in source_laps
            if r["driver_number"] not in existing_lap_drivers
        ]

        # --- raw_positions: dedupe on (driver_number, lap_number) ---
        source_positions = conn.execute(
            text(
                f"SELECT {_POSITION_COLS} FROM raw_positions "
                "WHERE session_key = :sk AND lap_number = :ln"
            ),
            {"sk": source_session_key, "ln": lap_number},
        ).mappings().fetchall()
        existing_position_drivers = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT driver_number FROM raw_positions "
                    "WHERE session_key = :sk AND lap_number = :ln"
                ),
                {"sk": target_session_key, "ln": lap_number},
            ).fetchall()
        }
        new_positions = [
            {**dict(r), "session_key": target_session_key}
            for r in source_positions
            if r["driver_number"] not in existing_position_drivers
        ]

        # --- raw_stints: snapshot of every stint started by this lap,
        # capping lap_end at the current lap (an ongoing stint's true
        # lap_end is not "known yet" as of this point in the replay) ---
        source_stints = conn.execute(
            text(
                f"SELECT {_STINT_COLS} FROM raw_stints "
                "WHERE session_key = :sk AND lap_start <= :ln"
            ),
            {"sk": source_session_key, "ln": lap_number},
        ).mappings().fetchall()
        new_stints = [
            {
                **{k: v for k, v in dict(r).items() if k != "lap_end"},
                "lap_end": min(r["lap_end"], lap_number),
                "session_key": target_session_key,
            }
            for r in source_stints
        ]

        # --- raw_pit_stops: snapshot of pit stops through this lap,
        # dedupe against everything already inserted for the target ---
        source_pits = conn.execute(
            text(f"SELECT {_PIT_COLS} FROM raw_pit_stops WHERE session_key = :sk AND lap_number <= :ln"),
            {"sk": source_session_key, "ln": lap_number},
        ).mappings().fetchall()
        existing_pit_keys = {
            (row[0], row[1])
            for row in conn.execute(
                text("SELECT driver_number, lap_number FROM raw_pit_stops WHERE session_key = :sk"),
                {"sk": target_session_key},
            ).fetchall()
        }
        new_pits = [
            {**dict(r), "session_key": target_session_key}
            for r in source_pits
            if (r["driver_number"], r["lap_number"]) not in existing_pit_keys
        ]

        # Replace the stints snapshot for the target (mirror poller's
        # DELETE-then-INSERT — idempotent by construction, no dedupe needed).
        conn.execute(text("DELETE FROM raw_stints WHERE session_key = :sk"), {"sk": target_session_key})
        conn.commit()

    laps_inserted = bulk_insert_rows(engine, "raw_laps", new_laps)
    positions_inserted = bulk_insert_rows(engine, "raw_positions", new_positions)
    stints_replaced = bulk_insert_rows(engine, "raw_stints", new_stints)
    pits_inserted = bulk_insert_rows(engine, "raw_pit_stops", new_pits)

    return {
        "lap_number": lap_number,
        "laps_inserted": laps_inserted,
        "positions_inserted": positions_inserted,
        "stints_replaced": stints_replaced,
        "pits_inserted": pits_inserted,
    }


def _handle_sigterm(signum, frame) -> None:
    """
    Translate SIGTERM into SystemExit (mirrors pipeline.py's WR-05 fix).

    Python does not raise KeyboardInterrupt on SIGTERM by default (only
    SIGINT/Ctrl-C does). Without this handler, a SIGTERM (e.g. `kill`,
    `pkill`, a process manager, or a closing terminal) kills the process
    immediately with no cleanup — main()'s `except BaseException` restore
    loop never runs, and an in-place replay's staging backup is left
    stranded while the real session's raw_* rows sit cleared/partially
    rebuilt. Raising SystemExit here lets that existing restore path run
    exactly as it does for Ctrl-C.
    """
    raise SystemExit(0)


def main() -> None:
    """CLI entrypoint for the replay dev tool (D-02, D-03)."""
    signal.signal(signal.SIGTERM, _handle_sigterm)

    parser = argparse.ArgumentParser(
        description=(
            "Replay a completed historical race's raw_* rows into the raw_* "
            "tables incrementally on a timer, simulating live lap arrival so "
            "the poll -> inference -> dashboard chain can be watched end-to-"
            "end without a real live OpenF1 session (D-02)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--source-session-key",
        type=int,
        required=True,
        metavar="SK",
        help="The completed historical Race session_key to replay from.",
    )
    parser.add_argument(
        "--target-session-key",
        type=int,
        default=None,
        metavar="SK",
        help=(
            "Session_key to replay into. Defaults to --source-session-key "
            "(in-place replay: this session's raw_laps/raw_positions/"
            "raw_stints/raw_pit_stops are cleared and incrementally "
            "rebuilt, self-restoring by the time the replay completes or is "
            "interrupted). Pass a distinct value to replay into a separate "
            "session instead."
        ),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        metavar="SECONDS",
        help=(
            "Seconds to sleep between laps. Default 5.0 is a fast dev pace "
            "(D-03); use 60-100 for realistic race pacing."
        ),
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to the SQLite database file. Defaults to the project DB via get_engine().",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    engine = get_engine(args.db) if args.db else get_engine()
    source_sk = args.source_session_key
    target_sk = args.target_session_key if args.target_session_key is not None else source_sk

    lap_numbers = _get_source_lap_numbers(engine, source_sk)
    if not lap_numbers:
        logger.error(
            "replay_race: no raw_laps rows found for source session %d — nothing to replay",
            source_sk,
        )
        sys.exit(1)

    _ensure_target_session_row(engine, source_sk, target_sk)

    # In-place replay (target == source): stage a full backup copy under a
    # staging session_key *before* clearing the original, then read from
    # staging for the remainder of this run — replay_lap must never read
    # "source" data from the same row set it is concurrently deleting and
    # rebuilding for "target" when the two keys are identical.
    read_sk = source_sk
    staging_sk = None
    if target_sk == source_sk:
        staging_sk = _stage_source_snapshot(engine, source_sk)
        read_sk = staging_sk
        _reset_target_for_inplace_replay(engine, target_sk)

    logger.info(
        "replay_race: replaying %d laps from session %d into session %d (interval=%.1fs)",
        len(lap_numbers), source_sk, target_sk, args.interval,
    )

    # Tracks whether it is safe to delete the staging backup: either the
    # replay ran to normal completion (nothing to restore), or an interrupt
    # occurred and the restore loop below attempted every lap. The staging
    # backup must never be cleared unless one of these is true, since it is
    # the only copy of the original data during in-place replay.
    safe_to_clear_staging = staging_sk is None
    try:
        try:
            for lap_number in lap_numbers:
                result = replay_lap(engine, read_sk, target_sk, lap_number)
                logger.info("replay_race: lap %d — %s", lap_number, result)
                try:
                    compute_and_store_inrace_latest_lap(target_sk, engine=engine)
                except Exception as exc:
                    logger.warning(
                        "replay_race: in-race inference failed for lap %d: %s",
                        lap_number, exc,
                    )
                time.sleep(args.interval)
            safe_to_clear_staging = True
        except BaseException:
            if staging_sk is not None:
                logger.warning(
                    "replay_race: interrupted — restoring all %d laps for session "
                    "%d before exit",
                    len(lap_numbers), target_sk,
                )
                # Catch BaseException (not just Exception) per lap so a second
                # Ctrl-C (KeyboardInterrupt) during the restore loop cannot
                # abort partway through — every lap must be attempted before
                # the staging backup is allowed to be cleared below.
                for lap_number in lap_numbers:
                    try:
                        replay_lap(engine, read_sk, target_sk, lap_number)
                    except BaseException:
                        logger.exception("replay_race: failed to restore lap %d", lap_number)
                safe_to_clear_staging = True
            raise
    finally:
        if staging_sk is not None:
            if safe_to_clear_staging:
                _clear_staging(engine, staging_sk)
            else:
                logger.error(
                    "replay_race: restore did not complete — leaving staging "
                    "session %d in place so no data is lost; re-run replay_race "
                    "against session %d to finish recovery, or manually inspect "
                    "staging session %d.",
                    staging_sk, target_sk, staging_sk,
                )

    logger.info("replay_race: replay complete for session %d (%d laps)", target_sk, len(lap_numbers))


if __name__ == "__main__":
    main()
