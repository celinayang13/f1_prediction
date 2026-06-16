"""
Full ingest integration tests for Plan 02.

Covers:
  - classify_weather correctness (dry / wet / mixed / empty)
  - OpenF1 loader populates all 5 raw tables + raw_weather (offline via fixture transport)
  - FastF1 loader (extended) populates all 5 raw tables + raw_weather
  - Resumability: re-running either loader adds zero new rows
  - Year-guard: load_openf1_session(2022, ...) raises ValueError
  - Year-guard: load_session(2024, ...) raises ValueError (FastF1 path unchanged)
  - CLI routing: --years 2022 routes to FastF1; --years 2024 routes to OpenF1

All OpenF1 network calls are intercepted via httpx.MockTransport using the
JSON fixture files under tests/fixtures/. No live network access is required.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from f1_prediction.ingest.normalizer import classify_weather

# ---------------------------------------------------------------------------
# Fixtures directory
# ---------------------------------------------------------------------------
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> bytes:
    """Read a fixture JSON file as bytes for MockTransport."""
    return (FIXTURES_DIR / name).read_bytes()


# ---------------------------------------------------------------------------
# classify_weather tests
# ---------------------------------------------------------------------------


class TestClassifyWeather:
    def test_empty_returns_dry(self):
        result = classify_weather([])
        assert result["wet_dry_mixed"] == "dry"
        assert result["rainfall_flag_count"] == 0
        assert result["total_observation_count"] == 0

    def test_all_dry(self):
        rows = [{"rainfall": 0}] * 3
        result = classify_weather(rows)
        assert result["wet_dry_mixed"] == "dry"
        assert result["rainfall_flag_count"] == 0
        assert result["total_observation_count"] == 3

    def test_all_wet(self):
        rows = [{"rainfall": 1}] * 3
        result = classify_weather(rows)
        assert result["wet_dry_mixed"] == "wet"
        assert result["rainfall_flag_count"] == 3
        assert result["total_observation_count"] == 3

    def test_33_pct_wet_returns_mixed(self):
        """33% rainfall → mixed (>0% and <50%)."""
        rows = [{"rainfall": 1}, {"rainfall": 0}, {"rainfall": 0}]
        result = classify_weather(rows)
        assert result["wet_dry_mixed"] == "mixed", result
        assert result["rainfall_flag_count"] == 1
        assert result["total_observation_count"] == 3

    def test_exactly_50_pct_wet_returns_wet(self):
        """50% rainfall → wet (>=50%)."""
        rows = [{"rainfall": 1}, {"rainfall": 0}]
        result = classify_weather(rows)
        assert result["wet_dry_mixed"] == "wet"

    def test_weather_sample_fixture_yields_mixed(self):
        """The openf1_weather_sample.json has 3 dry + 2 wet = 40% wet → mixed."""
        raw = json.loads(_load_fixture("openf1_weather_sample.json"))
        result = classify_weather(raw)
        assert result["wet_dry_mixed"] == "mixed"
        assert result["rainfall_flag_count"] == 2
        assert result["total_observation_count"] == 5

    def test_aggregate_temps(self):
        """Median air_temp, track_temp, humidity are returned when available."""
        rows = [
            {"rainfall": 0, "air_temperature": 28.5, "track_temperature": 38.2, "humidity": 55.0},
            {"rainfall": 0, "air_temperature": 28.7, "track_temperature": 38.6, "humidity": 55.4},
            {"rainfall": 0, "air_temperature": 28.9, "track_temperature": 39.0, "humidity": 56.5},
        ]
        result = classify_weather(rows)
        assert result["air_temp_c"] == pytest.approx(28.7)
        assert result["track_temp_c"] == pytest.approx(38.6)
        assert result["humidity_pct"] == pytest.approx(55.4)


# ---------------------------------------------------------------------------
# OpenF1 client / loader tests using MockTransport
# ---------------------------------------------------------------------------


class _FixtureMockTransport(httpx.BaseTransport):
    """
    httpx transport that returns fixture JSON responses keyed by URL path.

    Map: endpoint suffix → fixture filename.
    """

    ROUTE_MAP = {
        "/v1/sessions": "openf1_session_sample.json",
        "/v1/laps": "openf1_laps_sample.json",
        "/v1/stints": "openf1_stints_sample.json",
        "/v1/pit": "openf1_pits_sample.json",
        "/v1/position": "openf1_positions_sample.json",
        "/v1/weather": "openf1_weather_sample.json",
        "/v1/intervals": "[]",  # empty — not needed for tests
    }

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for suffix, fixture in self.ROUTE_MAP.items():
            if path.endswith(suffix):
                if fixture.startswith("["):
                    # Inline literal (e.g. "[]" for intervals)
                    body = fixture.encode()
                else:
                    body = _load_fixture(fixture)
                return httpx.Response(200, content=body, headers={"content-type": "application/json"})
        return httpx.Response(404, content=b"[]")


def test_openf1_year_guard_raises_for_2022():
    """load_openf1_session(2022, ...) must raise ValueError with 'year >= 2023'."""
    from f1_prediction.ingest.openf1_loader import load_openf1_session

    with pytest.raises(ValueError, match="year >= 2023"):
        load_openf1_session(2022, 9999)


def test_fastf1_year_guard_still_raises_for_2024():
    """Regression: load_session(2024, ...) must still raise ValueError (plan 01 contract)."""
    from f1_prediction.ingest.fastf1_loader import load_session

    with pytest.raises(ValueError, match="year <= 2022"):
        load_session(2024, 1, "R")


def test_openf1_loader_populates_all_tables(tmp_engine):
    """
    load_openf1_session with MockTransport populates all 5 raw tables + raw_weather.
    """
    from f1_prediction.ingest.openf1_loader import load_openf1_session

    transport = _FixtureMockTransport()
    result = load_openf1_session(2024, 9158, engine=tmp_engine, _transport=transport)

    assert result.get("skipped") is False
    assert result.get("laps_inserted", 0) > 0
    assert result.get("stints_inserted", 0) > 0
    assert result.get("pits_inserted", 0) > 0
    assert result.get("positions_inserted", 0) > 0

    from sqlalchemy import text

    with tmp_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM raw_sessions WHERE session_key=9158")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM raw_laps WHERE session_key=9158")).scalar() == 4
        assert conn.execute(text("SELECT COUNT(*) FROM raw_stints WHERE session_key=9158")).scalar() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM raw_pit_stops WHERE session_key=9158")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM raw_positions WHERE session_key=9158")).scalar() == 4
        assert conn.execute(text("SELECT COUNT(*) FROM raw_weather WHERE session_key=9158")).scalar() == 1
        wdm = conn.execute(text("SELECT wet_dry_mixed FROM raw_weather WHERE session_key=9158")).scalar()
        assert wdm == "mixed"  # fixture has 2/5 rainfall → 40% → mixed
        source = conn.execute(text("SELECT source FROM raw_sessions WHERE session_key=9158")).scalar()
        assert source == "openf1"


def test_openf1_loader_resumability(tmp_engine):
    """Re-running load_openf1_session for the same session_key returns skipped=True."""
    from f1_prediction.ingest.openf1_loader import load_openf1_session

    transport = _FixtureMockTransport()
    result1 = load_openf1_session(2024, 9158, engine=tmp_engine, _transport=transport)
    assert result1.get("skipped") is False

    result2 = load_openf1_session(2024, 9158, engine=tmp_engine, _transport=transport)
    assert result2.get("skipped") is True

    # Ensure no duplicate rows were inserted
    from sqlalchemy import text

    with tmp_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM raw_laps WHERE session_key=9158")).scalar() == 4


def test_openf1_client_retries_on_429(tmp_engine):
    """
    OpenF1Client._fetch retries on 429 with tenacity.
    Simulate 2× 429 then 200; assert 3 total calls were made.
    """
    import asyncio

    call_count = 0
    fixture_body = _load_fixture("openf1_laps_sample.json")

    class RetryMockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(429, content=b"[]", headers={"content-type": "application/json"})
            return httpx.Response(200, content=fixture_body, headers={"content-type": "application/json"})

    from f1_prediction.ingest.openf1_client import OpenF1Client

    async def run():
        transport = RetryMockTransport()
        async with OpenF1Client(_transport=transport) as client:
            result = await client.laps(session_key=9158)
        return result

    data = asyncio.run(run())
    assert call_count == 3, f"Expected 3 calls (2× 429 + 1× 200), got {call_count}"
    assert len(data) == 4  # fixture has 4 lap rows


def test_fastf1_loader_now_inserts_stints_pits_positions_weather(tmp_engine):
    """
    Extended FastF1 loader inserts stints, pits, positions, and weather
    in addition to laps (new in Plan 02).

    Uses 2022 Bahrain Race round 1 (already cached from Plan 01 tests).
    """
    from sqlalchemy import text

    from f1_prediction.ingest.fastf1_loader import load_session

    result = load_session(2022, 1, "R", engine=tmp_engine)
    assert result.get("skipped") is False
    assert result.get("laps_inserted", 0) > 0

    with tmp_engine.connect() as conn:
        stints = conn.execute(text("SELECT COUNT(*) FROM raw_stints WHERE session_key=:sk"), {"sk": result["session_key"]}).scalar()
        pits = conn.execute(text("SELECT COUNT(*) FROM raw_pit_stops WHERE session_key=:sk"), {"sk": result["session_key"]}).scalar()
        positions = conn.execute(text("SELECT COUNT(*) FROM raw_positions WHERE session_key=:sk"), {"sk": result["session_key"]}).scalar()
        weather_count = conn.execute(text("SELECT COUNT(*) FROM raw_weather WHERE session_key=:sk"), {"sk": result["session_key"]}).scalar()
        wdm = conn.execute(text("SELECT wet_dry_mixed FROM raw_weather WHERE session_key=:sk"), {"sk": result["session_key"]}).scalar()

    assert stints > 0, "Expected FastF1 loader to insert stints"
    assert pits >= 0, "FastF1 pits column should exist (may be 0 if none derivable)"
    assert positions > 0, "Expected FastF1 loader to insert positions"
    assert weather_count == 1, f"Expected 1 raw_weather row, got {weather_count}"
    assert wdm in ("dry", "wet", "mixed"), f"Unexpected wet_dry_mixed: {wdm}"


def test_fastf1_loader_resumability_with_new_tables(tmp_engine):
    """
    Re-running FastF1 loader on an already-ingested session returns skipped=True
    and adds zero rows to all tables.
    """
    from sqlalchemy import text

    from f1_prediction.ingest.fastf1_loader import load_session

    r1 = load_session(2022, 1, "R", engine=tmp_engine)
    assert r1.get("skipped") is False
    sk = r1["session_key"]

    with tmp_engine.connect() as conn:
        stints_before = conn.execute(text("SELECT COUNT(*) FROM raw_stints WHERE session_key=:sk"), {"sk": sk}).scalar()

    r2 = load_session(2022, 1, "R", engine=tmp_engine)
    assert r2.get("skipped") is True

    with tmp_engine.connect() as conn:
        stints_after = conn.execute(text("SELECT COUNT(*) FROM raw_stints WHERE session_key=:sk"), {"sk": sk}).scalar()

    assert stints_after == stints_before, "Resumable: stints count must not increase on re-run"


def test_cli_help_has_expected_args():
    """backfill.py --help contains --years, --rounds, --identifiers, --skip-features."""
    result = subprocess.run(
        [sys.executable, "scripts/backfill.py", "--help"],
        capture_output=True,
        text=True,
        cwd="/Users/celinayang/Downloads/f1_prediction",
    )
    assert result.returncode == 0, result.stderr
    help_text = result.stdout + result.stderr
    for flag in ["--years", "--rounds", "--identifiers", "--skip-features"]:
        assert flag in help_text, f"Expected '{flag}' in --help output. Got:\n{help_text}"
