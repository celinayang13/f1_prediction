"""
Tests for the live poller (Plan 03, Task 2).

TDD RED phase — all tests written before implementation.

Covers:
  - session_window.is_session_active: active and inactive cases
  - poller.poll_once: dedupe (no duplicate laps), no-op outside window
  - poller.poll_once: inserts only new laps returned by OpenF1
  - scheduler lifecycle: start_poller / stop_poller (idempotent)
  - 3-interval poll execution in real time (interval_seconds=1)

Design notes:
  - is_session_active accepts 'now' kwarg for deterministic testing (no freezegun needed)
  - poll_once accepts 'client' kwarg for dependency injection
  - _FixtureOpenF1Client is a drop-in mock for OpenF1Client using pre-canned dicts
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest
from apscheduler.schedulers.base import STATE_RUNNING, STATE_STOPPED

from f1_prediction.db.engine import get_engine, init_db
from f1_prediction.db.upsert import bulk_insert_rows
from f1_prediction.live.poller import poll_once, start_poller, stop_poller
from f1_prediction.live.session_window import find_current_session, is_session_active


# ---------------------------------------------------------------------------
# Helpers / Mock OpenF1 Client
# ---------------------------------------------------------------------------

def _make_session_dict(
    session_key: int = 9999,
    date_start: str = "2024-03-02T14:00:00",
    date_end: str = "2024-03-02T16:00:00",
) -> dict:
    """Return a canned OpenF1 session dict."""
    return {
        "session_key": session_key,
        "year": 2024,
        "meeting_key": 100,
        "circuit_key": 50,
        "session_type": "Race",
        "session_name": "Race",
        "date_start": date_start,
        "date_end": date_end,
        "country_name": "Bahrain",
        "circuit_short_name": "Bahrain",
        "location": "Sakhir",
        "gmt_offset": "+03:00",
    }


class _FakeSyncClient:
    """
    Synchronous-callable mock for is_session_active.

    is_session_active uses asyncio.run(...) internally to call client.sessions().
    We fake this by providing a synchronous 'sessions' method that returns
    canned data, and wrapping in an async context manager as the real client does.
    """

    def __init__(self, sessions_data: list[dict]):
        self._sessions_data = sessions_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def sessions(self, session_key=None, **filters):
        if session_key is not None:
            return [s for s in self._sessions_data if s.get("session_key") == session_key]
        return self._sessions_data


class _FakeSyncClientFactory:
    """
    Factory that produces _FakeSyncClient instances. Passed as 'client' to
    is_session_active. The function expects an already-opened client
    (context-manager style) — we need to match the real client interface.
    """

    def __init__(self, sessions_data: list[dict]):
        self._data = sessions_data

    async def __aenter__(self):
        return _FakeSyncClient(self._data)

    async def __aexit__(self, *args):
        pass

    async def sessions(self, session_key=None, **filters):
        """Support calling sessions() directly (when client is passed as an instance)."""
        if session_key is not None:
            return [s for s in self._data if s.get("session_key") == session_key]
        return self._data


class _MockPollerClient:
    """
    Mock for poll_once's internal client. Returns pre-configured lap/stint/position/pit data.
    call_count incremented on each fetch_laps call.
    """

    def __init__(
        self,
        laps: list[dict] | None = None,
        stints: list[dict] | None = None,
        positions: list[dict] | None = None,
        pit_stops: list[dict] | None = None,
        call_counter: list | None = None,
    ):
        self._laps = laps or []
        self._stints = stints or []
        self._positions = positions or []
        self._pit_stops = pit_stops or []
        self._call_counter = call_counter

    async def laps(self, session_key: int, **filters) -> list[dict]:
        if self._call_counter is not None:
            self._call_counter.append(1)
        return self._laps

    async def stints(self, session_key: int) -> list[dict]:
        return self._stints

    async def position(self, session_key: int) -> list[dict]:
        return self._positions

    async def pit(self, session_key: int) -> list[dict]:
        return self._pit_stops

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_lap_dict(session_key: int, driver_number: int, lap_number: int) -> dict:
    """Return a minimal lap dict matching the OpenF1 laps schema."""
    return {
        "session_key": session_key,
        "driver_number": driver_number,
        "lap_number": lap_number,
        "lap_duration": 90.0 + lap_number * 0.1,
        "duration_sector_1": None,
        "duration_sector_2": None,
        "duration_sector_3": None,
        "is_pit_out_lap": False,
        "is_pit_in_lap": False,
        "i1_speed": None,
        "i2_speed": None,
        "st_speed": None,
        "date_start": None,
        "tyre_compound_observed": None,
        "segments_sector_1": None,
        "segments_sector_2": None,
        "segments_sector_3": None,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def poll_engine(tmp_path):
    """In-memory SQLite engine for poller tests."""
    db_path = tmp_path / "poll_test.db"
    engine = get_engine(db_path)
    init_db(engine)
    return engine


@pytest.fixture()
def seeded_poll_engine(poll_engine):
    """Engine pre-seeded with laps 1–5 for driver 16 in session 9999."""
    sk = 9999
    from sqlalchemy import text
    with poll_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO raw_sessions "
                "(session_key, year, round_number, country_name, circuit_short_name, "
                "session_type, session_name, date_start, date_end, total_laps, source) "
                "VALUES (9999, 2024, 1, 'Bahrain', 'Bahrain', 'Race', 'Race', "
                "'2024-03-02T14:00:00Z', '2024-03-02T16:00:00Z', 57, 'openf1')"
            )
        )
        conn.commit()
    for lap_num in range(1, 6):
        row = _make_lap_dict(sk, 16, lap_num)
        bulk_insert_rows(poll_engine, "raw_laps", [row])
    return poll_engine


# ---------------------------------------------------------------------------
# Test 1: session_window — active case
# ---------------------------------------------------------------------------

def test_session_window_active():
    """
    is_session_active returns True when 'now' is within [date_start - 30min, date_end + 30min].
    """
    sess = _make_session_dict(
        session_key=9999,
        date_start="2024-03-02T14:00:00",
        date_end="2024-03-02T16:00:00",
    )
    fake_client = _FakeSyncClientFactory([sess])

    # 'now' is midway through the session window
    mid_session = datetime(2024, 3, 2, 15, 0, 0, tzinfo=timezone.utc)
    result = is_session_active(9999, now=mid_session, client=fake_client)
    assert result is True, f"Expected True for mid-session 'now', got {result}"


# ---------------------------------------------------------------------------
# Test 2: session_window — inactive case (well after session end)
# ---------------------------------------------------------------------------

def test_session_window_inactive():
    """
    is_session_active returns False when 'now' is well outside the ±30-min window.
    """
    sess = _make_session_dict(
        session_key=9999,
        date_start="2024-03-02T14:00:00",
        date_end="2024-03-02T16:00:00",
    )
    fake_client = _FakeSyncClientFactory([sess])

    # 'now' is 2 hours after date_end + 30min → definitely inactive
    late = datetime(2024, 3, 2, 18, 45, 0, tzinfo=timezone.utc)
    result = is_session_active(9999, now=late, client=fake_client)
    assert result is False, f"Expected False for late 'now', got {result}"


# ---------------------------------------------------------------------------
# Test 3: poll_once inserts only new laps (dedupe by lap_number)
# ---------------------------------------------------------------------------

def test_poll_once_inserts_only_new_laps(seeded_poll_engine):
    """
    DB has laps 1–5 for driver 16. OpenF1 returns laps 1–7.
    poll_once should insert only laps 6 and 7 → total driver 16 rows = 7.
    """
    sk = 9999

    # Build mock laps 1–7
    all_laps = [_make_lap_dict(sk, 16, lap_num) for lap_num in range(1, 8)]

    # Patch is_session_active to return True
    mock_client = _MockPollerClient(laps=all_laps)

    def _fake_is_active(session_key, *, now=None, client=None):
        return True

    result = poll_once(sk, engine=seeded_poll_engine, _is_active=_fake_is_active, _client=mock_client)

    assert result.get("skipped") is False, f"Expected not skipped: {result}"
    assert result.get("laps_inserted") == 2, f"Expected 2 new laps, got {result}"

    # Verify DB has exactly 7 rows for driver 16
    from sqlalchemy import text
    with seeded_poll_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM raw_laps WHERE session_key = :sk AND driver_number = 16"),
            {"sk": sk},
        ).scalar()
    assert count == 7, f"Expected 7 rows for driver 16, got {count}"


# ---------------------------------------------------------------------------
# Test 4: poll_once no-ops when session is inactive
# ---------------------------------------------------------------------------

def test_poll_once_noop_when_inactive(seeded_poll_engine):
    """
    When is_session_active returns False, poll_once returns {skipped: True}
    and inserts 0 rows.
    """
    sk = 9999

    def _fake_is_active(session_key, *, now=None, client=None):
        return False

    result = poll_once(sk, engine=seeded_poll_engine, _is_active=_fake_is_active, _client=None)

    assert result.get("skipped") is True, f"Expected skipped=True: {result}"

    # Row count unchanged (still 5)
    from sqlalchemy import text
    with seeded_poll_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM raw_laps WHERE session_key = :sk AND driver_number = 16"),
            {"sk": sk},
        ).scalar()
    assert count == 5, f"Expected count still 5, got {count}"


# ---------------------------------------------------------------------------
# Test 5: poller fires at least 3 polls in 3.5 seconds (interval=1s)
# ---------------------------------------------------------------------------

def test_poller_runs_3_intervals(poll_engine):
    """
    With interval_seconds=1, start_poller fires the job at least 3 times in 3.5 seconds.
    Uses a call counter to verify the job executed.
    """
    sk = 9999
    call_counter: list[int] = []

    # Insert the session so poll_once can look it up
    from sqlalchemy import text
    with poll_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO raw_sessions "
                "(session_key, year, round_number, country_name, circuit_short_name, "
                "session_type, session_name, date_start, date_end, total_laps, source) "
                "VALUES (9999, 2024, 1, 'Bahrain', 'Bahrain', 'Race', 'Race', "
                "'2024-03-02T14:00:00Z', '2024-03-02T16:00:00Z', 57, 'openf1')"
            )
        )
        conn.commit()

    mock_client = _MockPollerClient(laps=[], call_counter=call_counter)

    def _fake_is_active(session_key, *, now=None, client=None):
        return True

    scheduler = start_poller(
        sk,
        engine=poll_engine,
        interval_seconds=1,
        _is_active=_fake_is_active,
        _client_factory=lambda: mock_client,
    )

    try:
        time.sleep(3.5)
    finally:
        stop_poller(scheduler)

    assert len(call_counter) >= 3, (
        f"Expected at least 3 polls in 3.5s, got {len(call_counter)}"
    )


# ---------------------------------------------------------------------------
# Test 6: scheduler lifecycle — start/stop including idempotent stop
# ---------------------------------------------------------------------------

def test_poller_lifecycle(poll_engine):
    """
    start_poller returns a running BackgroundScheduler.
    stop_poller transitions it to STATE_STOPPED.
    Calling stop_poller twice is idempotent (no exception).
    """
    sk = 9999
    call_counter: list[int] = []

    from sqlalchemy import text
    with poll_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO raw_sessions "
                "(session_key, year, round_number, country_name, circuit_short_name, "
                "session_type, session_name, date_start, date_end, total_laps, source) "
                "VALUES (9999, 2024, 1, 'Bahrain', 'Bahrain', 'Race', 'Race', "
                "'2024-03-02T14:00:00Z', '2024-03-02T16:00:00Z', 57, 'openf1')"
            )
        )
        conn.commit()

    mock_client = _MockPollerClient(laps=[], call_counter=call_counter)

    def _fake_is_active(session_key, *, now=None, client=None):
        return False  # No-op polls to avoid side effects

    scheduler = start_poller(
        sk,
        engine=poll_engine,
        interval_seconds=60,  # Long interval — just testing lifecycle
        _is_active=_fake_is_active,
        _client_factory=lambda: mock_client,
    )

    assert scheduler.state == STATE_RUNNING, (
        f"Scheduler should be running after start_poller, state={scheduler.state}"
    )

    stop_poller(scheduler)
    assert scheduler.state == STATE_STOPPED, (
        f"Scheduler should be stopped after stop_poller, state={scheduler.state}"
    )

    # Second stop is idempotent — must not raise
    stop_poller(scheduler)
    assert scheduler.state == STATE_STOPPED
