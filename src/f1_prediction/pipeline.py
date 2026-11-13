"""
Pipeline entrypoint (D-01, D-06, D-09, D-11).

`python -m f1_prediction.pipeline [--session-key N]`:
  1. Picks the race to follow from the OpenF1 schedule: the one in progress,
     otherwise the next one. If the schedule cannot be fetched (or the season
     is over) it falls back to the latest race stored in the database.
     --session-key overrides the choice (operation and replay).
  2. Prepares the weekend: stores the race session, loads the weekend's
     finished qualifying/practice/sprint sessions, the starting grid and
     driver metadata, rebuilds pre-race features and recomputes pre-race
     predictions. Repeated every 10 minutes until the race starts, so the
     prediction follows qualifying and grid changes.
  3. Starts the in-race poll loop (reusing live/poller.py's BackgroundScheduler
     pattern) with a post-poll inference hook: after each poll_once() call,
     run_inference_after_poll() triggers in-race inference only when laps
     arrived or changed (D-09).

OpenF1 serves data from 30 minutes before a session until 30 minutes after
it only to paid subscribers (set OPENF1_USERNAME / OPENF1_PASSWORD). Without
an account the poll loop collects nothing during the race and catches up
once the data becomes free.

Security: all SQL uses text() with bound :param names — no f-string SQL (T-03-05).
Driver fields are whitelist-extracted before insert (T-03-01) — the raw
OpenF1 response dict is never inserted directly.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text
from sqlalchemy.engine import Engine

from f1_prediction.db.engine import get_engine, init_db
from f1_prediction.db.queries import get_latest_race_session_key
from f1_prediction.db.upsert import bulk_insert_rows, insert_or_skip_session, session_exists
from f1_prediction.features.builder import build_features_for_session
from f1_prediction.inference.inrace import compute_and_store_inrace_latest_lap
from f1_prediction.inference.prerace import compute_and_store_prerace
from f1_prediction.ingest.openf1_loader import (
    _canonical_session_type,
    _normalize_openf1_session,
    load_openf1_session,
)
from f1_prediction.live.poller import poll_once

logger = logging.getLogger(__name__)

__all__ = [
    "get_latest_race_session_key",
    "prerace_already_computed",
    "sync_drivers_for_session",
    "run_inference_after_poll",
    "pick_target_race",
    "prepare_weekend",
    "main",
]

# OpenF1 serves a session for free from this long after it ends
_FREE_AFTER = timedelta(minutes=30)
# A race still counts as in progress this long after its scheduled end
_RACE_OVERRUN = timedelta(hours=2)
# Weekend sessions that feed the pre-race features
_WEEKEND_SESSION_TYPES = {"Qualifying", "Practice 2", "Practice 3", "Sprint", "Sprint Shootout"}


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _races(schedule: list[dict]) -> list[dict]:
    """Non-cancelled Grand Prix sessions of a season schedule, in date order."""
    return sorted(
        (
            s for s in schedule
            if _canonical_session_type(s) == "Race" and not s.get("is_cancelled")
            and s.get("date_start") and s.get("date_end")
        ),
        key=lambda s: s["date_start"],
    )


def pick_target_race(schedule: list[dict], now: datetime) -> Optional[dict]:
    """
    The race to follow: the one in progress, otherwise the next to start.

    A race counts as in progress from 30 minutes before its start until two
    hours after its scheduled end, so a later race on the calendar never
    displaces one that is running (or overrunning).

    Args:
        schedule: OpenF1 /v1/sessions rows for the season.
        now: Current time (timezone-aware).

    Returns:
        The chosen session dict, or None if the season has no race left.
    """
    races = _races(schedule)
    for race in races:
        if _utc(race["date_start"]) - _FREE_AFTER <= now <= _utc(race["date_end"]) + _RACE_OVERRUN:
            return race
    return next((race for race in races if _utc(race["date_start"]) > now), None)


def prepare_weekend(engine: Engine, race: dict, schedule: list[dict], now: datetime) -> int:
    """
    Bring everything the pre-race prediction needs up to date for one race,
    then recompute that prediction. Safe to call repeatedly.

    Args:
        engine: SQLAlchemy Engine.
        race: The race's OpenF1 /v1/sessions row.
        schedule: The season's OpenF1 /v1/sessions rows.
        now: Current time (timezone-aware).

    Returns:
        Number of pre-race prediction rows written (0 if qualifying has not
        been loaded yet).
    """
    race_key, meeting_key, year = int(race["session_key"]), race.get("meeting_key"), int(race["year"])
    round_number = [int(r["session_key"]) for r in _races(schedule)].index(race_key) + 1

    insert_or_skip_session(engine, _normalize_openf1_session(race))

    # weekend sessions that have finished long enough ago to be freely available
    qualifying_key = None
    for sess in schedule:
        if sess.get("meeting_key") != meeting_key or sess.get("is_cancelled"):
            continue
        session_type = _canonical_session_type(sess)
        if session_type == "Qualifying":
            qualifying_key = int(sess["session_key"])
        if session_type not in _WEEKEND_SESSION_TYPES or not sess.get("date_end"):
            continue
        if _utc(sess["date_end"]) + _FREE_AFTER > now or session_exists(engine, int(sess["session_key"])):
            continue
        try:
            load_openf1_session(year, int(sess["session_key"]), engine=engine)
        except Exception as exc:
            logger.warning("prepare_weekend: could not load %s session %s: %s",
                           session_type, sess.get("session_key"), exc)

    # the feature builder finds a weekend's sessions by (year, round_number)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE raw_sessions SET round_number = :rn WHERE meeting_key = :mk"),
            {"rn": round_number, "mk": meeting_key},
        )

    if qualifying_key is not None and session_exists(engine, qualifying_key):
        try:
            from f1_prediction.ingest.openf1_client import fetch_starting_grid

            grid = [
                {"session_key": race_key, "driver_number": int(g["driver_number"]), "position": int(g["position"])}
                for g in fetch_starting_grid(qualifying_key)
                if g.get("driver_number") is not None and g.get("position") is not None
            ]
            if grid:
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM raw_starting_grid WHERE session_key = :sk"), {"sk": race_key})
                    conn.execute(
                        text(
                            "INSERT INTO raw_starting_grid (session_key, driver_number, position) "
                            "VALUES (:session_key, :driver_number, :position)"
                        ),
                        grid,
                    )
        except Exception as exc:
            logger.warning("prepare_weekend: could not load the starting grid: %s", exc)

    sync_drivers_for_session(engine, race_key)
    build_features_for_session(race_key, engine=engine, kind="prerace")
    written = compute_and_store_prerace(race_key, engine=engine)
    logger.info("prepare_weekend: session %d (round %d) — %d pre-race predictions", race_key, round_number, written)
    return written


# Whitelist for OpenF1 /v1/drivers fields (T-03-01) — mirrors _LAP_FIELDS/
# _STINT_FIELDS in live/poller.py.
_DRIVER_FIELDS = [
    "session_key", "driver_number", "full_name", "name_acronym",
    "team_name", "team_colour",
]


def prerace_already_computed(engine: Engine, session_key: int) -> bool:
    """
    Return True if 'prerace' predictions already exist for session_key (D-11).

    Mirrors the check-then-skip resumability pattern in scripts/backfill.py
    and db/upsert.py::session_exists.

    Args:
        engine: SQLAlchemy Engine.
        session_key: Race session_key to check.

    Returns:
        True if at least one prerace prediction row exists; False otherwise.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM predictions "
                "WHERE session_key = :sk AND prediction_type = 'prerace' LIMIT 1"
            ),
            {"sk": session_key},
        ).fetchone()
    return row is not None


def sync_drivers_for_session(engine: Engine, session_key: int) -> int:
    """
    Best-effort fetch of driver metadata (D-06) from OpenF1 /v1/drivers.

    Only fetches if no raw_drivers rows exist yet for this session (fetch
    once per session_key, mirroring D-11's check-then-skip convention —
    Open Question 2). Never raises — a 2022 FastF1-sourced session has no
    OpenF1 drivers data, and a failure here must not crash pipeline startup.

    Args:
        engine: SQLAlchemy Engine.
        session_key: Race session_key to sync driver metadata for.

    Returns:
        Number of raw_drivers rows inserted (0 if already populated, or if
        the fetch failed).
    """
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM raw_drivers WHERE session_key = :sk LIMIT 1"),
            {"sk": session_key},
        ).fetchone()
    if existing is not None:
        logger.debug(
            "sync_drivers_for_session: raw_drivers already populated for session %d",
            session_key,
        )
        return 0

    try:
        from f1_prediction.ingest.openf1_client import fetch_drivers

        drivers_raw = fetch_drivers(session_key)
        rows = [{f: d.get(f) for f in _DRIVER_FIELDS} for d in drivers_raw]
        inserted = bulk_insert_rows(engine, "raw_drivers", rows)
        logger.info(
            "sync_drivers_for_session: inserted %d raw_drivers rows for session %d",
            inserted, session_key,
        )
        return inserted
    except Exception as exc:
        logger.warning(
            "sync_drivers_for_session: failed to fetch/insert drivers for session %d: %s",
            session_key, exc,
        )
        return 0


def run_inference_after_poll(engine: Engine, session_key: int, poll_result: dict) -> int:
    """
    Post-poll inference hook (D-09): trigger in-race inference only when new
    laps arrived during the last poll cycle.

    Args:
        engine: SQLAlchemy Engine.
        session_key: Race session_key that was just polled.
        poll_result: The dict returned by live/poller.py::poll_once — must
            contain a "laps_inserted" key.

    Returns:
        Number of 'inrace' prediction rows inserted (0 if laps_inserted is
        not > 0, i.e. a no-op).
    """
    if poll_result.get("laps_inserted", 0) > 0:
        return compute_and_store_inrace_latest_lap(session_key, engine=engine)
    return 0


def _handle_sigterm(signum, frame) -> None:
    """
    Translate SIGTERM into SystemExit (WR-05).

    Python does not raise KeyboardInterrupt on SIGTERM by default (only
    SIGINT/Ctrl-C does). Process managers (systemd, Docker, a supervisor
    script) stop long-running background processes with SIGTERM — without
    this handler, main()'s `except (KeyboardInterrupt, SystemExit)` never
    runs and the BackgroundScheduler's in-flight poll/write job gets killed
    abruptly by the OS instead of via scheduler.shutdown(wait=False).
    """
    raise SystemExit(0)


def main(*, engine: Optional[Engine] = None) -> None:
    """CLI entrypoint: `python -m f1_prediction.pipeline [--session-key N]`."""
    parser = argparse.ArgumentParser(description="Follow one race: pre-race predictions, then lap-by-lap updates.")
    parser.add_argument("--session-key", type=int, default=None,
                        help="Race session to follow (default: the race in progress, else the next one).")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    signal.signal(signal.SIGTERM, _handle_sigterm)

    if engine is None:
        engine = get_engine()
    init_db(engine)

    # Which race? The schedule decides; the database is only a fallback.
    now = datetime.now(timezone.utc)
    schedule, race = [], None
    try:
        from f1_prediction.ingest.openf1_client import fetch_sessions

        schedule = fetch_sessions(year=now.year)
        if args.session_key is not None:
            race = next((s for s in schedule if int(s["session_key"]) == args.session_key), None)
        else:
            race = pick_target_race(schedule, now)
    except Exception as exc:
        logger.warning("pipeline: could not fetch the OpenF1 schedule: %s", exc)

    if race is not None:
        session_key = int(race["session_key"])
        logger.info("pipeline: following %s %s (session_key=%d, starts %s)",
                    race.get("year"), race.get("country_name"), session_key, race.get("date_start"))
        race_start = _utc(race["date_start"])
    else:
        session_key = args.session_key or get_latest_race_session_key(engine)
        if session_key is None:
            logger.error(
                "No race session found in the database. Run scripts/backfill.py "
                "or start the live poller, then relaunch the pipeline."
            )
            sys.exit(1)
        logger.info("pipeline: no scheduled race to follow — using stored session_key=%d", session_key)
        race_start = None

    def _prepare_job():
        # Always recompute: a retrained model or changed qualifying data must
        # replace the stored pre-race numbers, never be skipped as "already done".
        current = datetime.now(timezone.utc)
        if race is not None:
            if race_start is not None and current >= race_start:
                return
            prepare_weekend(engine, race, schedule, current)
        else:
            sync_drivers_for_session(engine, session_key)
            compute_and_store_prerace(session_key, engine=engine)

    try:
        _prepare_job()
    except Exception as exc:
        logger.error("pipeline: weekend preparation failed: %s", exc, exc_info=True)

    # In-race poll loop (D-09): reuse live/poller.py's BackgroundScheduler
    # pattern (max_instances=1, misfire_grace_time=15, EVENT_JOB_ERROR
    # listener). After each poll_once() call, run_inference_after_poll()
    # triggers in-race inference only when laps arrived or changed.
    scheduler = BackgroundScheduler()

    def _poll_and_infer_job():
        result = poll_once(session_key, engine=engine)
        run_inference_after_poll(engine, session_key, result)

    def _on_error(event):
        logger.error("pipeline job failed: %s", event.exception)

    scheduler.add_listener(_on_error, EVENT_JOB_ERROR)
    scheduler.add_job(
        _poll_and_infer_job,
        "interval",
        seconds=30,
        id="live_poll_and_infer",
        max_instances=1,
        misfire_grace_time=15,
    )
    if race_start is not None:
        scheduler.add_job(_prepare_job, "interval", minutes=10, id="prepare_weekend", max_instances=1)
    scheduler.start()
    logger.info(
        "pipeline: in-race poll loop started for session %d (interval=30s)",
        session_key,
    )

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("pipeline: shutting down poll loop")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
