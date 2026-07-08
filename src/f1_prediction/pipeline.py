"""
Pipeline entrypoint (D-01, D-06, D-09, D-11).

`python -m src.f1_prediction.pipeline`:
  1. Resolves the latest Race-type session_key in the DB (D-01, no manual
     --session-key flag).
  2. Best-effort syncs driver metadata (raw_drivers) from OpenF1 /v1/drivers
     for that session (D-06) — never crashes startup if this fails.
  3. Computes pre-race win probabilities once per session, skipping if
     'prerace' predictions already exist (D-11 idempotent restart), before
     the in-race poll loop begins (documented TODO hook for a later plan).

Security: all SQL uses text() with bound :param names — no f-string SQL (T-03-05).
Driver fields are whitelist-extracted before insert (T-03-01) — the raw
OpenF1 response dict is never inserted directly.
"""
from __future__ import annotations

import logging
import sys
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from f1_prediction.db.engine import get_engine
from f1_prediction.db.upsert import bulk_insert_rows
from f1_prediction.inference.prerace import compute_and_store_prerace

logger = logging.getLogger(__name__)

__all__ = [
    "get_latest_race_session_key",
    "prerace_already_computed",
    "sync_drivers_for_session",
    "main",
]

# Whitelist for OpenF1 /v1/drivers fields (T-03-01) — mirrors _LAP_FIELDS/
# _STINT_FIELDS in live/poller.py.
_DRIVER_FIELDS = [
    "session_key", "driver_number", "full_name", "name_acronym",
    "team_name", "team_colour",
]


def get_latest_race_session_key(engine: Engine) -> "int | None":
    """
    Resolve the most recent Race-type session_key (D-01).

    Orders by session_key DESC alone — OpenF1 session_keys are server-assigned
    and monotonically increasing, while round_number is only reliably
    populated after backfill.py's fix_round_numbers() step runs (RESEARCH A4),
    which may not have happened yet for a freshly-polled session.

    Args:
        engine: SQLAlchemy Engine.

    Returns:
        The latest Race session_key, or None if no Race session exists.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT session_key FROM raw_sessions "
                "WHERE session_type = 'Race' "
                "ORDER BY session_key DESC LIMIT 1"
            )
        ).fetchone()
    return int(row[0]) if row else None


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

    # TODO(Plan 03): start the in-race poll loop here — reuse live/poller.py's
    # BackgroundScheduler, hook post-poll inference off poll_once()'s
    # laps_inserted return value (Pattern 1: rebuild-then-slice via
    # build_features_for_session(session_key, kind="inrace") + filter to
    # MAX(lap_number), per D-09's startup-then-poll sequence).


if __name__ == "__main__":
    main()
