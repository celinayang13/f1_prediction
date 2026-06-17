"""
APScheduler live poller for OpenF1 race data (Plan 03, DATA-03).

Polls OpenF1 every 30 seconds for an active race session, dedupes incoming
laps by (session_key, driver_number, lap_number), and inserts new rows into
the same raw_* tables as the backfill pipeline.

Architecture decisions:
  - BackgroundScheduler (threaded) — runs in-process without blocking ASGI.
  - max_instances=1 + misfire_grace_time=15 (T-03-02: no concurrent polls).
  - asyncio.run() at top level of poll_once (RESEARCH anti-pattern note):
    not nested await — creates a fresh event loop per call in the scheduler thread.
  - EVENT_JOB_ERROR listener logs all job exceptions (T-03-07 mitigation).
  - is_session_active check at start of every poll (Pitfall 7 mitigation).
  - Stints are replaced per poll (DELETE-then-INSERT); lap/position/pit are
    deduped in Python before INSERT.

Security: no f-string SQL — all SQL uses text() with bound :param (T-03-05).
         --session-key is cast to int by argparse (T-03-04 accepted).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_STOPPED
from sqlalchemy import text
from sqlalchemy.engine import Engine

from f1_prediction.db.engine import get_engine
from f1_prediction.db.upsert import bulk_insert_rows

logger = logging.getLogger(__name__)

__all__ = ["start_poller", "stop_poller", "poll_once"]

# Fields to extract from OpenF1 laps response (per-field whitelist T-03-01)
_LAP_FIELDS = [
    "session_key", "driver_number", "lap_number", "lap_duration",
    "duration_sector_1", "duration_sector_2", "duration_sector_3",
    "is_pit_out_lap", "is_pit_in_lap", "i1_speed", "i2_speed", "st_speed",
    "date_start", "tyre_compound_observed",
    "segments_sector_1", "segments_sector_2", "segments_sector_3",
]

_STINT_FIELDS = [
    "session_key", "driver_number", "stint_number",
    "lap_start", "lap_end", "compound", "tyre_age_at_start",
]

_POSITION_FIELDS = [
    "session_key", "driver_number", "lap_number", "position", "date_recorded",
]

_PIT_FIELDS = [
    "session_key", "driver_number", "lap_number", "lane_duration", "stop_duration",
    "date_recorded",
]


def _extract(record: dict, fields: list[str]) -> dict:
    """Extract only the whitelisted fields from an API response record."""
    return {f: record.get(f) for f in fields}


def poll_once(
    session_key: int,
    *,
    engine: Optional[Engine] = None,
    _is_active: Optional[Callable] = None,
    _client=None,
) -> dict:
    """
    Perform one poll cycle: fetch new data from OpenF1 and insert into raw tables.

    Steps:
      1. Check if the session is within its active window (is_session_active).
         If not, return {skipped: True, reason: 'outside_window'}.
      2. Find max lap_number already stored for this session (dedupe gate).
      3. Fetch laps from OpenF1, filter to laps > max_lap_number, insert new rows.
      4. Replace stints (DELETE + INSERT for session_key — small data, cleaner than diff).
      5. Fetch positions and pit stops, dedupe in Python, insert new rows.
      6. Return a summary dict.

    Args:
        session_key: OpenF1 session_key to poll.
        engine: SQLAlchemy engine (defaults to get_engine()).
        _is_active: Override for is_session_active (for testing).
        _client: Override for OpenF1Client instance (for testing). May be an
                 already-instantiated mock or a context-manager client.

    Returns:
        Dict with keys: session_key, skipped, laps_inserted, stints_replaced,
        positions_inserted, pit_stops_inserted.

    Security: catches all exceptions and logs them — never crashes the scheduler
    thread (Pitfall 7 + T-03-07).
    """
    if engine is None:
        engine = get_engine()

    # ------------------------------------------------------------------
    # Step 1: Session window check
    # ------------------------------------------------------------------
    if _is_active is None:
        from f1_prediction.live.session_window import is_session_active as _is_active_fn
        _is_active = _is_active_fn

    try:
        active = _is_active(session_key)
    except Exception as exc:
        logger.error("poll_once: is_session_active failed for session %d: %s", session_key, exc)
        active = False

    if not active:
        logger.debug("poll_once: session %d outside active window — skipping", session_key)
        return {
            "session_key": session_key,
            "skipped": True,
            "reason": "outside_window",
            "laps_inserted": 0,
            "stints_replaced": 0,
            "positions_inserted": 0,
            "pit_stops_inserted": 0,
        }

    # ------------------------------------------------------------------
    # Step 2: Find the highest lap_number already stored
    # ------------------------------------------------------------------
    try:
        with engine.connect() as conn:
            max_lap_row = conn.execute(
                text(
                    "SELECT COALESCE(MAX(lap_number), 0) FROM raw_laps "
                    "WHERE session_key = :sk"
                ),
                {"sk": session_key},
            ).fetchone()
            max_lap = int(max_lap_row[0]) if max_lap_row else 0
    except Exception as exc:
        logger.error("poll_once: DB read failed for session %d: %s", session_key, exc)
        return {
            "session_key": session_key,
            "skipped": False,
            "laps_inserted": 0,
            "stints_replaced": 0,
            "positions_inserted": 0,
            "pit_stops_inserted": 0,
            "error": str(exc),
        }

    # ------------------------------------------------------------------
    # Step 3–5: Fetch from OpenF1 and insert
    # ------------------------------------------------------------------
    try:
        if _client is not None:
            # Test mode: use the injected client directly
            raw_client = _client
            laps_inserted = _fetch_and_insert_laps(session_key, raw_client, max_lap, engine)
            stints_replaced = _replace_stints(session_key, raw_client, engine)
            positions_inserted = _fetch_and_insert_positions(session_key, raw_client, engine)
            pit_stops_inserted = _fetch_and_insert_pits(session_key, raw_client, engine)
        else:
            # Production mode: open a fresh OpenF1Client
            from f1_prediction.ingest.openf1_client import OpenF1Client

            async def _run_all():
                async with OpenF1Client() as c:
                    li = await _async_fetch_laps(session_key, c, max_lap, engine)
                    sr = await _async_replace_stints(session_key, c, engine)
                    pi = await _async_fetch_positions(session_key, c, engine)
                    psi = await _async_fetch_pits(session_key, c, engine)
                    return li, sr, pi, psi

            laps_inserted, stints_replaced, positions_inserted, pit_stops_inserted = asyncio.run(_run_all())

    except Exception as exc:
        logger.error(
            "poll_once: fetch/insert failed for session %d: %s", session_key, exc, exc_info=True
        )
        return {
            "session_key": session_key,
            "skipped": False,
            "laps_inserted": 0,
            "stints_replaced": 0,
            "positions_inserted": 0,
            "pit_stops_inserted": 0,
            "error": str(exc),
        }

    result = {
        "session_key": session_key,
        "skipped": False,
        "laps_inserted": laps_inserted,
        "stints_replaced": stints_replaced,
        "positions_inserted": positions_inserted,
        "pit_stops_inserted": pit_stops_inserted,
    }
    logger.info("poll_once: session %d — %s", session_key, result)
    return result


# ---------------------------------------------------------------------------
# Synchronous helpers (used when _client is injected in tests)
# ---------------------------------------------------------------------------

def _fetch_and_insert_laps(session_key: int, client, max_lap: int, engine: Engine) -> int:
    """Fetch laps from the injected client, filter new, insert."""
    laps_raw = asyncio.run(client.laps(session_key))
    new_laps = [
        _extract(lap, _LAP_FIELDS)
        for lap in laps_raw
        if lap.get("lap_number") is not None and int(lap["lap_number"]) > max_lap
    ]
    return bulk_insert_rows(engine, "raw_laps", new_laps)


def _replace_stints(session_key: int, client, engine: Engine) -> int:
    """Replace all stints for this session: DELETE then INSERT."""
    stints_raw = asyncio.run(client.stints(session_key))
    stints = [_extract(s, _STINT_FIELDS) for s in stints_raw]
    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM raw_stints WHERE session_key = :sk"),
            {"sk": session_key},
        )
        conn.commit()
    return bulk_insert_rows(engine, "raw_stints", stints)


def _fetch_and_insert_positions(session_key: int, client, engine: Engine) -> int:
    """Fetch positions, dedupe in Python, insert new rows."""
    positions_raw = asyncio.run(client.position(session_key))
    # Find existing (driver_number, lap_number) pairs
    with engine.connect() as conn:
        existing = set(
            conn.execute(
                text(
                    "SELECT driver_number, lap_number FROM raw_positions "
                    "WHERE session_key = :sk"
                ),
                {"sk": session_key},
            ).fetchall()
        )
    new_positions = [
        _extract(p, _POSITION_FIELDS)
        for p in positions_raw
        if (p.get("driver_number"), p.get("lap_number")) not in existing
        and p.get("lap_number") is not None
    ]
    return bulk_insert_rows(engine, "raw_positions", new_positions)


def _fetch_and_insert_pits(session_key: int, client, engine: Engine) -> int:
    """Fetch pit stops, dedupe in Python, insert new rows."""
    pits_raw = asyncio.run(client.pit(session_key))
    with engine.connect() as conn:
        existing = set(
            conn.execute(
                text(
                    "SELECT driver_number, lap_number FROM raw_pit_stops "
                    "WHERE session_key = :sk"
                ),
                {"sk": session_key},
            ).fetchall()
        )
    new_pits = [
        _extract(p, _PIT_FIELDS)
        for p in pits_raw
        if (p.get("driver_number"), p.get("lap_number")) not in existing
        and p.get("lap_number") is not None
    ]
    return bulk_insert_rows(engine, "raw_pit_stops", new_pits)


# ---------------------------------------------------------------------------
# Async helpers (used in production mode with real OpenF1Client)
# ---------------------------------------------------------------------------

async def _async_fetch_laps(session_key: int, client, max_lap: int, engine: Engine) -> int:
    laps_raw = await client.laps(session_key)
    new_laps = [
        _extract(lap, _LAP_FIELDS)
        for lap in laps_raw
        if lap.get("lap_number") is not None and int(lap["lap_number"]) > max_lap
    ]
    return bulk_insert_rows(engine, "raw_laps", new_laps)


async def _async_replace_stints(session_key: int, client, engine: Engine) -> int:
    stints_raw = await client.stints(session_key)
    stints = [_extract(s, _STINT_FIELDS) for s in stints_raw]
    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM raw_stints WHERE session_key = :sk"),
            {"sk": session_key},
        )
        conn.commit()
    return bulk_insert_rows(engine, "raw_stints", stints)


async def _async_fetch_positions(session_key: int, client, engine: Engine) -> int:
    positions_raw = await client.position(session_key)
    with engine.connect() as conn:
        existing = set(
            conn.execute(
                text(
                    "SELECT driver_number, lap_number FROM raw_positions "
                    "WHERE session_key = :sk"
                ),
                {"sk": session_key},
            ).fetchall()
        )
    new_positions = [
        _extract(p, _POSITION_FIELDS)
        for p in positions_raw
        if (p.get("driver_number"), p.get("lap_number")) not in existing
        and p.get("lap_number") is not None
    ]
    return bulk_insert_rows(engine, "raw_positions", new_positions)


async def _async_fetch_pits(session_key: int, client, engine: Engine) -> int:
    pits_raw = await client.pit(session_key)
    with engine.connect() as conn:
        existing = set(
            conn.execute(
                text(
                    "SELECT driver_number, lap_number FROM raw_pit_stops "
                    "WHERE session_key = :sk"
                ),
                {"sk": session_key},
            ).fetchall()
        )
    new_pits = [
        _extract(p, _PIT_FIELDS)
        for p in pits_raw
        if (p.get("driver_number"), p.get("lap_number")) not in existing
        and p.get("lap_number") is not None
    ]
    return bulk_insert_rows(engine, "raw_pit_stops", new_pits)


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

def start_poller(
    session_key: int,
    *,
    engine: Optional[Engine] = None,
    interval_seconds: int = 30,
    scheduler: Optional[BackgroundScheduler] = None,
    _is_active: Optional[Callable] = None,
    _client_factory: Optional[Callable] = None,
) -> BackgroundScheduler:
    """
    Start an APScheduler BackgroundScheduler that calls poll_once every
    interval_seconds seconds.

    Args:
        session_key: OpenF1 session_key to poll.
        engine: SQLAlchemy engine (defaults to get_engine()).
        interval_seconds: Poll interval in seconds (default 30).
        scheduler: Existing BackgroundScheduler to use (default: create new).
        _is_active: Override for is_session_active (for testing).
        _client_factory: Callable returning a mock client (for testing).
                         If provided, poll_once will use client=_client_factory().

    Returns:
        Running BackgroundScheduler.
    """
    if engine is None:
        engine = get_engine()

    if scheduler is None:
        scheduler = BackgroundScheduler()

    def _job():
        client = _client_factory() if _client_factory is not None else None
        try:
            poll_once(session_key, engine=engine, _is_active=_is_active, _client=client)
        except Exception as exc:
            # Should not reach here (poll_once catches internally), but be safe
            logger.error("start_poller job: uncaught exception: %s", exc, exc_info=True)

    def _on_error(event):
        logger.error("poll job failed: %s", event.exception)

    scheduler.add_listener(_on_error, EVENT_JOB_ERROR)
    scheduler.add_job(
        _job,
        "interval",
        seconds=interval_seconds,
        id="live_poll",
        max_instances=1,
        misfire_grace_time=15,
    )
    scheduler.start()
    logger.info(
        "start_poller: scheduler started for session %d (interval=%ds)",
        session_key, interval_seconds,
    )
    return scheduler


def stop_poller(scheduler: BackgroundScheduler) -> None:
    """
    Stop the scheduler. Idempotent — safe to call multiple times.

    Args:
        scheduler: BackgroundScheduler to stop.
    """
    try:
        if scheduler.state != STATE_STOPPED:
            scheduler.shutdown(wait=False)
            logger.info("stop_poller: scheduler stopped")
        else:
            logger.debug("stop_poller: scheduler already stopped — no-op")
    except Exception as exc:
        logger.warning("stop_poller: caught exception during shutdown: %s", exc)
