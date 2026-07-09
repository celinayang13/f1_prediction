---
phase: 01-data-features
plan: "02"
subsystem: data-ingest
status: complete
tags:
  - python
  - sqlite
  - sqlalchemy
  - fastf1
  - openf1
  - httpx
  - tenacity
  - tdd

dependency_graph:
  requires:
    - f1_prediction.db.engine.get_engine
    - f1_prediction.db.engine.init_db
    - f1_prediction.db.models.Base
    - f1_prediction.db.models.RawSession
    - f1_prediction.db.models.RawLap
    - f1_prediction.db.upsert.insert_or_skip_session
    - f1_prediction.db.upsert.bulk_insert_rows
    - f1_prediction.ingest.normalizer.normalize_fastf1_session
    - f1_prediction.ingest.normalizer.normalize_fastf1_laps
    - f1_prediction.ingest.fastf1_loader.load_session
  provides:
    - f1_prediction.db.models.RawStint
    - f1_prediction.db.models.RawPitStop
    - f1_prediction.db.models.RawPosition
    - f1_prediction.db.models.RawWeather
    - f1_prediction.db.upsert.session_exists
    - f1_prediction.ingest.normalizer.classify_weather
    - f1_prediction.ingest.normalizer.normalize_fastf1_stints
    - f1_prediction.ingest.normalizer.normalize_fastf1_pits
    - f1_prediction.ingest.normalizer.normalize_fastf1_positions
    - f1_prediction.ingest.normalizer.normalize_fastf1_weather
    - f1_prediction.ingest.openf1_client.OpenF1Client
    - f1_prediction.ingest.openf1_client.fetch_sessions
    - f1_prediction.ingest.openf1_client.fetch_laps
    - f1_prediction.ingest.openf1_client.fetch_stints
    - f1_prediction.ingest.openf1_client.fetch_pit_stops
    - f1_prediction.ingest.openf1_client.fetch_positions
    - f1_prediction.ingest.openf1_client.fetch_weather
    - f1_prediction.ingest.openf1_client.fetch_intervals
    - f1_prediction.ingest.openf1_loader.load_openf1_session
    - f1_prediction.ingest.openf1_loader.load_openf1_year
    - f1_prediction.ingest.fastf1_loader.load_year
    - scripts/backfill.py CLI (--years --rounds --identifiers --skip-features)
  affects:
    - Plan 03 (feature builder reads all 6 raw tables produced here)

tech_stack:
  added:
    - tenacity retry pattern with @retry decorator on OpenF1Client._fetch
    - httpx.AsyncBaseTransport pattern for test mocking (MockTransport)
  patterns:
    - TDD: RED commit (test), GREEN commit (implementation)
    - asyncio.run() wrapping async OpenF1 client in synchronous loader
    - classify_weather rainfall rule: 0% dry, 0–50% mixed, >=50% wet
    - FastF1 stint derivation: group consecutive same-Compound laps per driver
    - Sprint detection via EventFormat in fastf1.get_event_schedule()
    - Per-field whitelist extraction from API responses (T-02-01)

key_files:
  created:
    - src/f1_prediction/ingest/openf1_client.py: OpenF1Client async context manager + 7 fetch_* functions
    - src/f1_prediction/ingest/openf1_loader.py: load_openf1_session() + load_openf1_year()
    - tests/test_ingest_full.py: 15 tests covering all ingest paths + weather + retry
    - tests/fixtures/openf1_session_sample.json: 1 session record (2024 Bahrain Race)
    - tests/fixtures/openf1_laps_sample.json: 4 lap records (2 drivers x 2 laps)
    - tests/fixtures/openf1_stints_sample.json: 2 stints records
    - tests/fixtures/openf1_pits_sample.json: 1 pit stop record
    - tests/fixtures/openf1_positions_sample.json: 4 position records
    - tests/fixtures/openf1_weather_sample.json: 5 weather records (3 dry + 2 wet = 40% = mixed)
  modified:
    - src/f1_prediction/db/models.py: added RawStint, RawPitStop, RawPosition, RawWeather; widened RawLap, RawSession
    - src/f1_prediction/db/upsert.py: added session_exists() helper
    - src/f1_prediction/ingest/normalizer.py: added classify_weather + 4 FastF1 normalize functions
    - src/f1_prediction/ingest/fastf1_loader.py: extended load_session() + added load_year()
    - scripts/backfill.py: widened CLI (--years --rounds --identifiers --skip-features)

decisions:
  - "D-11: session_exists() extracted from insert_or_skip_session for early-exit before API fetch"
  - "D-12: tenacity @retry on OpenF1Client._fetch: RateLimitedError on 429, stop_after_attempt(6), wait_exponential(min=2, max=60)"
  - "RateLimitedError raised on HTTP 429 inside _fetch body (cleaner than retry_if_result pattern)"
  - "FastF1 stints derived from consecutive same-Compound laps grouping; tyre_age_at_start = TyreLife-1 (TyreLife is 1-based)"
  - "FastF1 pits: stop_duration always NULL (not available from FastF1); lane_duration computed from PitInTime/PitOutTime timedelta"
  - "Stints/pits/positions ingested only for race-pace sessions (R, S); Q/FP2/FP3 get laps+weather only"
  - "MockTransport uses httpx.AsyncBaseTransport to support async OpenF1Client in tests"

metrics:
  duration_minutes: 8
  completed_date: "2026-06-16"
  tasks_completed: 2
  files_created: 10
---

# Phase 1 Plan 2: Full Ingest Pipeline Summary

**One-liner:** Complete raw-data substrate: 6 SQLite tables populated from FastF1 (2022) and OpenF1 (2023+) via tenacity-retried async client, with weather classification, stint derivation, and resumable year-loop backfill CLI.

## What Was Built

Plan 02 widens the walking skeleton from Plan 01 into a complete historical backfill pipeline. Two loaders (FastF1 for year <= 2022, OpenF1 for year >= 2023) now populate all 6 raw tables (`raw_sessions`, `raw_laps`, `raw_stints`, `raw_pit_stops`, `raw_positions`, `raw_weather`) for each session. The backfill CLI accepts multiple years, rounds, and session identifiers, routing by year. All OpenF1 network calls are covered by offline tests using recorded JSON fixtures.

## New Tables and Columns

### New Tables

| Table | Primary Key | Key Columns |
|-------|-------------|-------------|
| `raw_stints` | `id` (autoincrement) | `session_key`, `driver_number`, `stint_number`, `lap_start`, `lap_end`, `compound`, `tyre_age_at_start` |
| `raw_pit_stops` | `id` (autoincrement) | `session_key`, `driver_number`, `lap_number`, `lane_duration` (float), `stop_duration` (float, NULL pre-2024-US-GP) |
| `raw_positions` | `id` (autoincrement) | `session_key`, `driver_number`, `lap_number`, `position` (1–20) |
| `raw_weather` | `session_key` (one row per session) | `wet_dry_mixed` (TEXT CHECK IN wet/dry/mixed), `air_temp_c`, `track_temp_c`, `humidity_pct`, `rainfall_flag_count`, `total_observation_count` |

### Widened Tables

**`raw_laps` additions:**
- `is_pit_in_lap` (BOOL NULL) — OpenF1 field; True if driver pitted at lap end
- `tyre_compound_observed` (TEXT NULL) — compound on lap row (D-09 full mirror)
- `segments_sector_1/2/3` (TEXT NULL) — OpenF1 mini-sector JSON arrays stored as text

**`raw_sessions` additions:**
- `location` (TEXT NULL) — circuit location name
- `meeting_key` (INT NULL) — OpenF1 meeting key (NULL for FastF1 rows)
- `circuit_key` (INT NULL) — OpenF1 circuit key (NULL for FastF1 rows)
- `gmt_offset` (TEXT NULL) — UTC offset string
- `UniqueConstraint(year, round_number, session_type)` — DB-level duplicate guard (D-11)

## OpenF1 Endpoint → Field Name Mapping

The contract consumed by Plan 03's feature builder:

| OpenF1 Endpoint | Key Fields in raw table |
|-----------------|------------------------|
| `/v1/sessions` | `session_key, year, meeting_key, circuit_key, session_type, session_name, date_start, date_end, location, gmt_offset` |
| `/v1/laps` | `session_key, driver_number, lap_number, lap_duration, duration_sector_1/2/3, is_pit_out_lap, is_pit_in_lap, i1_speed, i2_speed, st_speed, date_start, tyre_compound_observed, segments_sector_1/2/3` |
| `/v1/stints` | `session_key, driver_number, stint_number, lap_start, lap_end, compound, tyre_age_at_start` |
| `/v1/pit` | `session_key, driver_number, lap_number, lane_duration, stop_duration` (not `pit_duration` — deprecated) |
| `/v1/position` | `session_key, driver_number, lap_number, position, date_recorded` |
| `/v1/weather` | Used to compute `raw_weather` classification (not stored per-row) |

## classify_weather Rule

```
total_observations == 0             → 'dry'
rainfall_flag_count == 0            → 'dry'
rainfall_flag_count / total >= 0.5  → 'wet'
0 < rainfall_flag_count / total < 0.5  → 'mixed'
```

Medians of `air_temperature`, `track_temperature`, `humidity` across observations are stored as `air_temp_c`, `track_temp_c`, `humidity_pct` in `raw_weather`.

## Year-Routing Logic

```
backfill.py --years [YEARS...]
  for year in YEARS:
    if year <= 2022:
      fastf1_loader.load_year(year, rounds, identifiers)
    else:  # year >= 2023
      openf1_loader.load_openf1_year(year, rounds, identifiers)
```

Default identifiers: `["FP2", "FP3", "Q", "R", "S", "SS"]` — FP1 excluded per D-06.

FastF1 sprint detection: `EventFormat == "conventional"` → FP2/FP3/Q/R; `EventFormat in ("sprint", "sprint_shootout", "sprint_qualifying")` → S/Q/R (no FP2/FP3 on sprint weekends).

## Resumability Gating

**Gate point:** `session_exists(engine, session_key)` check in both loaders before any API call.

- FastF1 path: `insert_or_skip_session()` returns False if row exists → return `skipped=True` immediately
- OpenF1 path: `session_exists()` checked first → if True, return `skipped=True` before `asyncio.run(fetch_all())`

This means re-running backfill against already-ingested sessions costs only one SQL SELECT per session, never a network call.

## Test Results

```
tests/test_skeleton.py::test_walking_skeleton_loads_one_2022_race PASSED
tests/test_skeleton.py::test_walking_skeleton_is_resumable PASSED
tests/test_skeleton.py::test_wal_mode_active PASSED

tests/test_ingest_full.py::TestClassifyWeather::test_empty_returns_dry PASSED
tests/test_ingest_full.py::TestClassifyWeather::test_all_dry PASSED
tests/test_ingest_full.py::TestClassifyWeather::test_all_wet PASSED
tests/test_ingest_full.py::TestClassifyWeather::test_33_pct_wet_returns_mixed PASSED
tests/test_ingest_full.py::TestClassifyWeather::test_exactly_50_pct_wet_returns_wet PASSED
tests/test_ingest_full.py::TestClassifyWeather::test_weather_sample_fixture_yields_mixed PASSED
tests/test_ingest_full.py::TestClassifyWeather::test_aggregate_temps PASSED
tests/test_ingest_full.py::test_openf1_year_guard_raises_for_2022 PASSED
tests/test_ingest_full.py::test_fastf1_year_guard_still_raises_for_2024 PASSED
tests/test_ingest_full.py::test_openf1_loader_populates_all_tables PASSED
tests/test_ingest_full.py::test_openf1_loader_resumability PASSED
tests/test_ingest_full.py::test_openf1_client_retries_on_429 PASSED
tests/test_ingest_full.py::test_fastf1_loader_now_inserts_stints_pits_positions_weather PASSED
tests/test_ingest_full.py::test_fastf1_loader_resumability_with_new_tables PASSED
tests/test_ingest_full.py::test_cli_help_has_expected_args PASSED

18 passed in 7.26s
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] _FixtureMockTransport required httpx.AsyncBaseTransport**
- **Found during:** Task 2 verification (test_openf1_loader_populates_all_tables)
- **Issue:** Test initially used `httpx.BaseTransport` (sync) and implemented `handle_request()`. OpenF1Client uses `httpx.AsyncClient` which requires `httpx.AsyncBaseTransport` implementing `handle_async_request()`. The sync transport resulted in `AttributeError: '_FixtureMockTransport' object has no attribute 'handle_async_request'`.
- **Fix:** Changed `_FixtureMockTransport` to inherit from `httpx.AsyncBaseTransport` and renamed `handle_request` → `handle_async_request`.
- **Files modified:** `tests/test_ingest_full.py`
- **Commit:** `5ba7e53`

## Known Stubs

- `stop_duration = None` for all FastF1 2022 pit stops — intentional and correct per RESEARCH State of the Art. The `stop_duration` field is only available from OpenF1 starting with the 2024 US GP. NULL is the correct value for all 2022 data.
- `date_recorded = None` for FastF1 pit stop and position rows — FastF1 does not expose per-event timestamps for these derived records. NULL is correct.

These stubs do not block the plan's goal; Plan 03's feature builder uses `lane_duration` (non-NULL from FastF1 when PitInTime/PitOutTime are available) for pit stop delta features.

## Threat Surface Scan

No new network endpoints introduced beyond the OpenF1 REST API documented in the plan's threat model. All mitigations implemented:

| Threat | Status |
|--------|--------|
| T-02-01: Per-field whitelist in OpenF1 normalizers | Implemented — `_normalize_openf1_laps/stints/pits/positions` only extract documented fields |
| T-02-02: raw_weather CHECK constraint on wet_dry_mixed | Implemented — `CheckConstraint("wet_dry_mixed IN ('wet', 'dry', 'mixed')")` |
| T-02-03: No response bodies in logs | Implemented — `logger.info("GET %s → %d", url, resp.status_code)` only |
| T-02-04: tenacity stop_after_attempt(6) + wait_exponential | Implemented |
| T-02-05: No f-string SQL | Verified — `grep -rn "f\"SELECT..." src/` returns no matches |
| T-02-06: HTTPS by default | Implemented — httpx default TLS verification, BASE_URL is https:// |
| T-02-07: --rounds scopes backfill; per-session try/except | Implemented |

## TDD Gate Compliance

This plan used TDD for Task 2:

1. **RED gate:** commit `9eb9bbb` — `test(01-02): add failing tests for full ingest pipeline + 6 OpenF1 fixture files`
2. **GREEN gate:** commit `5ba7e53` — `feat(01-02): OpenF1 client + loader, extended FastF1 loader, year-loop backfill CLI`

Both gates satisfied.

## Self-Check: PASSED

### Files verified present:

- `src/f1_prediction/db/models.py` — FOUND (contains RawStint, RawPitStop, RawPosition, RawWeather)
- `src/f1_prediction/db/upsert.py` — FOUND (contains session_exists)
- `src/f1_prediction/ingest/normalizer.py` — FOUND (contains classify_weather)
- `src/f1_prediction/ingest/openf1_client.py` — FOUND
- `src/f1_prediction/ingest/openf1_loader.py` — FOUND
- `src/f1_prediction/ingest/fastf1_loader.py` — FOUND (contains load_year)
- `scripts/backfill.py` — FOUND (contains --years --rounds --identifiers --skip-features)
- `tests/test_ingest_full.py` — FOUND (15 tests)
- `tests/fixtures/openf1_*_sample.json` (6 files) — FOUND

### Commits verified:

- `7ba0a0f` — feat(01-02): widen ORM schema — FOUND
- `9eb9bbb` — test(01-02): add failing tests — FOUND
- `5ba7e53` — feat(01-02): OpenF1 client + loader — FOUND
