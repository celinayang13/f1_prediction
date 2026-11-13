"""
Session status shown on the dashboard.

Decides from the session's scheduled window and the age of the newest
prediction whether a race is upcoming, genuinely live, stalled, or over —
so stored predictions for a past race are never presented as live.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

PRE_RACE, LIVE, STALE, FINISHED = "PRE-RACE", "LIVE", "STALE", "FINISHED"

# Predictions older than this during a race mean collection has stalled.
STALE_AFTER = timedelta(seconds=90)
# Races overrun their scheduled end (red flags, safety cars).
OVERRUN_GRACE = timedelta(hours=2)

STATUS_COLOURS = {PRE_RACE: "#1f77b4", LIVE: "#E10600", STALE: "#b8860b", FINISHED: "#555555"}


def parse_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp as UTC (a timestamp without an offset is taken as UTC)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def session_status(
    date_start: Optional[str],
    date_end: Optional[str],
    latest_prediction_at: Optional[str],
    now: Optional[datetime] = None,
) -> str:
    """
    Status of a race session.

    Args:
        date_start, date_end: The session's window (ISO 8601 strings from raw_sessions).
        latest_prediction_at: computed_at of the newest in-race prediction, if any.
        now: Current time (defaults to the real clock; injectable for tests and replay).

    Returns:
        PRE_RACE before the start; FINISHED after the end (plus an overrun
        allowance while predictions are still arriving); otherwise LIVE if
        the newest prediction is fresh, STALE if it is not.
    """
    now = now or datetime.now(timezone.utc)
    start, end, latest = parse_utc(date_start), parse_utc(date_end), parse_utc(latest_prediction_at)
    fresh = latest is not None and now - latest <= STALE_AFTER

    if start is None or end is None:
        # without a schedule, only fresh data can justify calling it live
        return LIVE if fresh else FINISHED
    if now < start:
        return PRE_RACE
    if now <= end or (fresh and now <= end + OVERRUN_GRACE):
        return LIVE if fresh else STALE
    return FINISHED
