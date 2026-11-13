"""
APScheduler live poller for OpenF1 race data (Plan 03, DATA-03).

Every poll fetches the session's laps, stints, positions and pit stops in
full and replaces the stored rows for that session, using the same
normalizers as the historical backfill (ingest/openf1_loader.py). Replacing
rather than appending means a lap that arrives late for one driver, a lap
whose time is filled in after it was first seen, and any corrected record
are all picked up without per-record bookkeeping.

Safety rules:
  - Each table is swapped inside one transaction, so readers never see a
    half-written session.
  - An endpoint that fails or returns less than is already stored leaves
    that table untouched — a bad poll never erases good data.

Architecture decisions:
  - BackgroundScheduler (threaded) — runs in-process without blocking ASGI.
  - max_instances=1 + misfire_grace_time=15 (T-03-02: no concurrent polls).
  - asyncio.run() at top level of poll_once (RESEARCH anti-pattern note):
    not nested await — creates a fresh event loop per call in the scheduler thread.
  - EVENT_JOB_ERROR listener logs all job exceptions (T-03-07 mitigation).
  - is_session_active check at start of every poll (Pitfall 7 mitigation).

Security: no f-string SQL — table names come from a fixed whitelist below
         and all values use bound :param (T-03-05).
         --session-key is cast to int by argparse (T-03-04 accepted).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

import pandas as pd
from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_STOPPED
from sqlalchemy import text
from sqlalchemy.engine import Engine

from f1_prediction.db.engine import get_engine
from f1_prediction.ingest.openf1_loader import (
    _normalize_openf1_laps,
    _normalize_openf1_pits,
    _normalize_openf1_positions,
    _normalize_openf1_stints,
)

logger = logging.getLogger(__name__)

__all__ = ["start_poller", "stop_poller", "poll_once"]

# table -> (client method, normalizer, result key, COUNT / DELETE statements)
_TABLES = {
    "raw_laps": ("laps", _normalize_openf1_laps, "laps_fetched"),
    "raw_stints": ("stints", _normalize_openf1_stints, "stints_replaced"),
    "raw_positions": ("position", _normalize_openf1_positions, "positions_inserted"),
    "raw_pit_stops": ("pit", _normalize_openf1_pits, "pit_stops_inserted"),
}
_COUNT_SQL = {t: text(f"SELECT COUNT(*) FROM {t} WHERE session_key = :sk") for t in _TABLES}  # noqa: S608
_DELETE_SQL = {t: text(f"DELETE FROM {t} WHERE session_key = :sk") for t in _TABLES}  # noqa: S608
_LAP_STATE_SQL = text(
    "SELECT driver_number, lap_number, lap_duration FROM raw_laps WHERE session_key = :sk"
)


def _empty_result(session_key: int, **extra) -> dict:
    return {
        "session_key": session_key,
        "skipped": False,
        "laps_inserted": 0,
        "stints_replaced": 0,
        "positions_inserted": 0,
        "pit_stops_inserted": 0,
        **extra,
    }


async def _fetch_all(client, session_key: int) -> dict:
    """Fetch every endpoint; one that fails maps to None so the others still apply."""
    fetched = {}
    for table, (method, _normalize, _key) in _TABLES.items():
        try:
            fetched[table] = await getattr(client, method)(session_key)
        except Exception as exc:
            logger.error("poll_once: %s fetch failed for session %d: %s", method, session_key, exc)
            fetched[table] = None
    return fetched


def poll_once(
    session_key: int,
    *,
    engine: Optional[Engine] = None,
    _is_active: Optional[Callable] = None,
    _client=None,
) -> dict:
    """
    Perform one poll cycle: fetch the session from OpenF1 and replace its raw rows.

    Steps:
      1. Check if the session is within its active window (is_session_active).
         If not, return {skipped: True, reason: 'outside_window'}.
      2. Fetch laps, stints, positions and pit stops (each independently).
      3. In one transaction, replace each table whose fetch succeeded and
         returned at least as many rows as are stored.

    Args:
        session_key: OpenF1 session_key to poll.
        engine: SQLAlchemy engine (defaults to get_engine()).
        _is_active: Override for is_session_active (for testing).
        _client: Override for OpenF1Client instance (for testing).

    Returns:
        Dict with keys: session_key, skipped, laps_inserted (laps that are
        new or whose time changed — the trigger for re-running inference),
        stints_replaced, positions_inserted, pit_stops_inserted, and
        degraded (list of tables left untouched this cycle, if any).

    Security: catches all exceptions and logs them — never crashes the scheduler
    thread (Pitfall 7 + T-03-07).
    """
    if engine is None:
        engine = get_engine()

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
        return _empty_result(session_key, skipped=True, reason="outside_window")

    try:
        async def _run():
            if _client is not None:
                return await _fetch_all(_client, session_key)
            from f1_prediction.ingest.openf1_client import OpenF1Client
            async with OpenF1Client() as client:
                return await _fetch_all(client, session_key)

        fetched = asyncio.run(_run())

        result = _empty_result(session_key)
        degraded = []
        with engine.begin() as conn:
            laps_before = set(conn.execute(_LAP_STATE_SQL, {"sk": session_key}).fetchall())
            for table, (_method, normalize, key) in _TABLES.items():
                raw = fetched[table]
                rows = normalize(raw, session_key) if raw is not None else []
                stored = conn.execute(_COUNT_SQL[table], {"sk": session_key}).scalar()
                # laps, positions and pit stops only ever grow during a session;
                # fewer rows than stored means a partial response, not a correction
                if raw is None or (table != "raw_stints" and len(rows) < stored):
                    degraded.append(table)
                    continue
                if not rows:
                    continue
                conn.execute(_DELETE_SQL[table], {"sk": session_key})
                pd.DataFrame(rows).to_sql(
                    table, conn, if_exists="append", index=False, method="multi", chunksize=500
                )
                result[key] = len(rows) if table == "raw_stints" else len(rows) - stored
            laps_after = set(conn.execute(_LAP_STATE_SQL, {"sk": session_key}).fetchall())

        result.pop("laps_fetched", None)
        result["laps_inserted"] = len(laps_after - laps_before)
        if degraded:
            result["degraded"] = degraded
            logger.warning("poll_once: session %d kept stored rows for %s", session_key, degraded)
    except Exception as exc:
        logger.error(
            "poll_once: fetch/insert failed for session %d: %s", session_key, exc, exc_info=True
        )
        return _empty_result(session_key, error=str(exc))

    logger.info("poll_once: session %d — %s", session_key, result)
    return result


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
