"""
Pipeline entrypoint (D-01, D-06, D-09, D-11).

`python -m src.f1_prediction.pipeline`:
  1. Resolves the latest Race-type session_key in the DB (D-01, no manual
     --session-key flag).
  2. Best-effort syncs driver metadata (raw_drivers) from OpenF1 /v1/drivers
     for that session (D-06) — never crashes startup if this fails.
  3. Computes pre-race win probabilities once per session, skipping if
     'prerace' predictions already exist (D-11 idempotent restart).
  4. Starts the in-race poll loop (reusing live/poller.py's BackgroundScheduler
     pattern) with a post-poll inference hook: after each poll_once() call,
     run_inference_after_poll() triggers in-race inference only when new laps
     arrived (D-09).

Security: all SQL uses text() with bound :param names — no f-string SQL (T-03-05).
Driver fields are whitelist-extracted before insert (T-03-01) — the raw
OpenF1 response dict is never inserted directly.
"""
from __future__ import annotations

import logging
import sys
import time
from typing import Optional

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text
from sqlalchemy.engine import Engine

from f1_prediction.db.engine import get_engine
from f1_prediction.db.queries import get_latest_race_session_key
from f1_prediction.db.upsert import bulk_insert_rows
from f1_prediction.inference.inrace import compute_and_store_inrace_latest_lap
from f1_prediction.inference.prerace import compute_and_store_prerace
from f1_prediction.live.poller import poll_once

logger = logging.getLogger(__name__)

__all__ = [
    "get_latest_race_session_key",
    "prerace_already_computed",
    "sync_drivers_for_session",
    "run_inference_after_poll",
    "main",
]

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


def main(*, engine: Optional[Engine] = None) -> None:
    """CLI entrypoint: `python -m src.f1_prediction.pipeline`."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    if engine is None:
        engine = get_engine()

    session_key = get_latest_race_session_key(engine)
    if session_key is None:
        logger.error(
            "No race session found in the database. Run scripts/backfill.py "
            "or start the live poller, then relaunch the pipeline."
        )
        sys.exit(1)

    logger.info("pipeline: latest race session_key=%d", session_key)

    sync_drivers_for_session(engine, session_key)

    if not prerace_already_computed(engine, session_key):
        compute_and_store_prerace(session_key, engine=engine)
    else:
        logger.info(
            "pipeline: prerace predictions already computed for session %d — skipping (D-11)",
            session_key,
        )

    # In-race poll loop (D-09): reuse live/poller.py's BackgroundScheduler
    # pattern (max_instances=1, misfire_grace_time=15, EVENT_JOB_ERROR
    # listener). After each poll_once() call, run_inference_after_poll()
    # triggers in-race inference only when new laps arrived.
    scheduler = BackgroundScheduler()

    def _poll_and_infer_job():
        result = poll_once(session_key, engine=engine)
        run_inference_after_poll(engine, session_key, result)

    def _on_error(event):
        logger.error("pipeline poll job failed: %s", event.exception)

    scheduler.add_listener(_on_error, EVENT_JOB_ERROR)
    scheduler.add_job(
        _poll_and_infer_job,
        "interval",
        seconds=30,
        id="live_poll_and_infer",
        max_instances=1,
        misfire_grace_time=15,
    )
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
