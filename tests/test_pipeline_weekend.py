"""Tests for choosing the race to follow and preparing its weekend."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

import f1_prediction.pipeline as pipeline
from f1_prediction.db.engine import get_engine, init_db


def _session(key, name, start, end, meeting=1, coarse=None, cancelled=False):
    return {
        "session_key": key, "session_name": name, "session_type": coarse or name, "meeting_key": meeting,
        "date_start": start, "date_end": end, "year": 2026, "country_name": "Testland",
        "location": "Testville", "is_cancelled": cancelled,
    }


SCHEDULE = [
    _session(10, "Qualifying", "2026-03-07T05:00:00+00:00", "2026-03-07T06:00:00+00:00"),
    _session(11, "Race", "2026-03-08T04:00:00+00:00", "2026-03-08T06:00:00+00:00"),
    _session(20, "Sprint", "2026-03-14T03:00:00+00:00", "2026-03-14T04:00:00+00:00", meeting=2, coarse="Race"),
    _session(21, "Race", "2026-03-15T07:00:00+00:00", "2026-03-15T09:00:00+00:00", meeting=2),
    _session(31, "Race", "2026-03-29T05:00:00+00:00", "2026-03-29T07:00:00+00:00", meeting=3, cancelled=True),
]


def _at(day, hour, minute=0):
    return datetime(2026, 3, day, hour, minute, tzinfo=timezone.utc)


def test_pick_target_race_prefers_the_race_in_progress():
    assert pipeline.pick_target_race(SCHEDULE, _at(8, 5))["session_key"] == 11
    assert pipeline.pick_target_race(SCHEDULE, _at(8, 7, 30))["session_key"] == 11   # overrunning


def test_pick_target_race_falls_forward_to_the_next_race_and_skips_sprints_and_cancelled():
    assert pipeline.pick_target_race(SCHEDULE, _at(1, 0))["session_key"] == 11
    assert pipeline.pick_target_race(SCHEDULE, _at(14, 3, 30))["session_key"] == 21  # a sprint is not the race
    assert pipeline.pick_target_race(SCHEDULE, _at(20, 0)) is None                    # only a cancelled race left


def test_prepare_weekend_stores_the_race_and_the_grid_before_any_race_data(tmp_path, monkeypatch):
    engine = get_engine(tmp_path / "weekend.db")
    init_db(engine)
    loaded = []

    def _fake_load(year, session_key, *, engine):
        loaded.append(session_key)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO raw_sessions (session_key, year, round_number, session_type, session_name, "
                "source, meeting_key) VALUES (:sk, 2026, 0, 'Qualifying', 'Qualifying', 'openf1', 1)"
            ), {"sk": session_key})

    monkeypatch.setattr(pipeline, "load_openf1_session", _fake_load)
    monkeypatch.setattr(pipeline, "sync_drivers_for_session", lambda engine, sk: 0)
    monkeypatch.setattr(pipeline, "compute_and_store_prerace", lambda sk, engine: 0)
    import f1_prediction.ingest.openf1_client as client
    monkeypatch.setattr(client, "fetch_starting_grid", lambda sk: [
        {"driver_number": 44, "position": 1}, {"driver_number": 16, "position": 2},
    ])

    # Saturday evening: qualifying is over and free, the race has not started
    pipeline.prepare_weekend(engine, SCHEDULE[1], SCHEDULE, _at(7, 12))
    pipeline.prepare_weekend(engine, SCHEDULE[1], SCHEDULE, _at(7, 13))   # repeat: no duplicates, no reload

    assert loaded == [10]
    with engine.connect() as conn:
        rounds = dict(conn.execute(text("SELECT session_key, round_number FROM raw_sessions")).fetchall())
        grid = conn.execute(text(
            "SELECT driver_number, position FROM raw_starting_grid WHERE session_key = 11 ORDER BY position"
        )).fetchall()
    assert rounds == {10: 1, 11: 1}
    assert [tuple(r) for r in grid] == [(44, 1), (16, 2)]


def test_openf1_login_is_used_only_when_credentials_are_set(monkeypatch):
    import asyncio

    import httpx

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("Authorization")))
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 3600})
        return httpx.Response(200, json=[])

    async def _call():
        async with client.OpenF1Client(_transport=httpx.MockTransport(handler)) as c:
            await c.laps(1)

    import f1_prediction.ingest.openf1_client as client
    monkeypatch.setattr(client, "_token_cache", None)
    monkeypatch.delenv("OPENF1_USERNAME", raising=False)
    monkeypatch.delenv("OPENF1_PASSWORD", raising=False)
    asyncio.run(_call())
    assert seen == [("GET", "/v1/laps", None)]

    seen.clear()
    monkeypatch.setenv("OPENF1_USERNAME", "me")
    monkeypatch.setenv("OPENF1_PASSWORD", "secret")
    asyncio.run(_call())
    asyncio.run(_call())                                   # second client reuses the cached token
    assert seen == [("POST", "/token", None), ("GET", "/v1/laps", "Bearer tok-123"), ("GET", "/v1/laps", "Bearer tok-123")]
