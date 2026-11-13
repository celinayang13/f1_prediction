"""Tests for the dashboard's session status logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from f1_prediction.dashboard_lib.status import FINISHED, LIVE, PRE_RACE, STALE, session_status

START, END = "2026-11-22T04:00:00+00:00", "2026-11-22T06:00:00+00:00"


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 22, hour, minute, tzinfo=timezone.utc)


def test_before_the_start_is_pre_race():
    assert session_status(START, END, None, now=_at(3)) == PRE_RACE


def test_fresh_predictions_during_the_race_are_live():
    now = _at(5)
    assert session_status(START, END, (now - timedelta(seconds=30)).isoformat(), now=now) == LIVE


def test_old_or_missing_predictions_during_the_race_are_stale():
    now = _at(5)
    assert session_status(START, END, (now - timedelta(minutes=5)).isoformat(), now=now) == STALE
    assert session_status(START, END, None, now=now) == STALE


def test_a_past_race_is_never_live_even_with_recently_computed_predictions():
    # predictions recomputed just now for a race that ended months ago
    now = datetime(2027, 1, 10, tzinfo=timezone.utc)
    assert session_status(START, END, (now - timedelta(seconds=5)).isoformat(), now=now) == FINISHED


def test_an_overrunning_race_stays_live_while_data_arrives():
    now = _at(6, 40)
    assert session_status(START, END, (now - timedelta(seconds=20)).isoformat(), now=now) == LIVE
    assert session_status(START, END, (now - timedelta(minutes=10)).isoformat(), now=now) == FINISHED


def test_naive_timestamps_are_read_as_utc():
    assert session_status("2026-11-22 04:00:00", "2026-11-22 06:00:00", None, now=_at(3)) == PRE_RACE
