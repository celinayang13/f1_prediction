"""
Session window detection for the live poller.

Provides:
  - is_session_active: returns True if 'now' is within [date_start - 30min, date_end + 30min]
  - find_current_session: convenience helper to discover the in-progress session

Pitfall 7 mitigation: the ±30-minute window absorbs clock skew and ensures the
poller starts slightly before the session and continues briefly after it ends.

Security: no user-supplied input passed to SQL — session_key is an int (T-03-05).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _parse_dt(s: str) -> datetime:
    """
    Parse an ISO 8601 datetime string and ensure it is timezone-aware (UTC).

    OpenF1 returns UTC datetimes (Assumption A3). If the string is naive
    (no timezone info), it is assumed UTC and given the UTC tzinfo.

    Args:
        s: ISO 8601 datetime string (may or may not have 'Z' suffix).

    Returns:
        timezone-aware datetime in UTC.
    """
    # Normalize 'Z' suffix
    s_clean = s.replace("Z", "+00:00") if s.endswith("Z") else s
    dt = datetime.fromisoformat(s_clean)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_session_active(
    session_key: int,
    *,
    now: Optional[datetime] = None,
    client=None,
) -> bool:
    """
    Return True if 'now' is within the session's active window.

    The active window is [date_start - 30min, date_end + 30min].
    The ±30-minute padding (Pitfall 7 mitigation) ensures:
      - The poller can start warming up before the session begins.
      - The poller continues to collect final laps after the checkered flag.

    Args:
        session_key: OpenF1 session_key to check.
        now: Reference time (defaults to datetime.now(timezone.utc)).
        client: Optional OpenF1Client instance or compatible object. If None,
                a fresh OpenF1Client is opened and closed for this call.

    Returns:
        True if 'now' is within the session window; False if the session is
        not found or 'now' is outside the window.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    async def _fetch():
        if client is None:
            from f1_prediction.ingest.openf1_client import OpenF1Client
            async with OpenF1Client() as c:
                return await c.sessions(session_key=session_key)
        # Support both context-manager style and direct call style
        if hasattr(client, '__aenter__'):
            async with client as c:
                return await c.sessions(session_key=session_key)
        else:
            return await client.sessions(session_key=session_key)

    try:
        sessions = asyncio.run(_fetch())
    except Exception as exc:
        logger.error("is_session_active: failed to fetch session %d: %s", session_key, exc)
        return False

    if not sessions:
        logger.info("is_session_active: no session found for session_key=%d", session_key)
        return False

    sess = sessions[0]
    date_start_str = sess.get("date_start")
    date_end_str = sess.get("date_end")

    if not date_start_str or not date_end_str:
        logger.warning(
            "is_session_active: session %d missing date_start or date_end", session_key
        )
        return False

    try:
        date_start = _parse_dt(date_start_str)
        date_end = _parse_dt(date_end_str)
    except (ValueError, TypeError) as exc:
        logger.error("is_session_active: failed to parse session dates: %s", exc)
        return False

    window_start = date_start - timedelta(minutes=30)
    window_end = date_end + timedelta(minutes=30)

    in_window = window_start <= now <= window_end
    logger.debug(
        "is_session_active: session=%d now=%s window=[%s, %s] active=%s",
        session_key, now, window_start, window_end, in_window,
    )
    return in_window


def find_current_session(
    *,
    year: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """
    Find the in-progress session for the given year (or current year).

    Fetches all sessions for the year from OpenF1 and returns the first one
    whose active window contains 'now'. Returns None if no session is active.

    Args:
        year: Season year (defaults to current year).
        now: Reference time (defaults to datetime.now(timezone.utc)).

    Returns:
        Session dict from OpenF1, or None if no active session found.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if year is None:
        year = now.year

    async def _fetch_all():
        from f1_prediction.ingest.openf1_client import OpenF1Client
        async with OpenF1Client() as c:
            return await c.sessions(year=year)

    try:
        sessions = asyncio.run(_fetch_all())
    except Exception as exc:
        logger.error("find_current_session: failed to fetch sessions for year=%d: %s", year, exc)
        return None

    for sess in sessions:
        date_start_str = sess.get("date_start")
        date_end_str = sess.get("date_end")
        if not date_start_str or not date_end_str:
            continue
        try:
            date_start = _parse_dt(date_start_str)
            date_end = _parse_dt(date_end_str)
        except (ValueError, TypeError):
            continue

        window_start = date_start - timedelta(minutes=30)
        window_end = date_end + timedelta(minutes=30)
        if window_start <= now <= window_end:
            logger.info(
                "find_current_session: found active session %s (%s)",
                sess.get("session_key"), sess.get("session_name"),
            )
            return sess

    logger.info("find_current_session: no active session for year=%d at %s", year, now)
    return None
