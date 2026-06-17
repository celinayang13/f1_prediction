---
phase: 01-data-features
plan: "03"
subsystem: feature-engineering + live-polling
status: complete
tags:
  - python
  - sqlite
  - sqlalchemy
  - features
  - apscheduler
  - live-poller
  - asyncio
  - tdd

dependency_graph:
  requires:
    - f1_prediction.db.engine.get_engine
    - f1_prediction.db.engine.init_db
    - f1_prediction.db.models.Base
    - f1_prediction.db.models.RawSession
    - f1_prediction.db.models.RawLap
    - f1_prediction.db.models.RawStint
    - f1_prediction.db.models.RawPitStop
    - f1_prediction.db.models.RawPosition
    - f1_prediction.db.upsert.bulk_insert_rows
    - f1_prediction.ingest.openf1_client.OpenF1Client
    - scripts/backfill.py CLI (--skip-features flag)
  provides:
    - f1_prediction.db.models.FeaturesPrerace (features_prerace table)
    - f1_prediction.db.models.FeaturesInrace (features_inrace table)
    - f1_prediction.features.prerace.compute_qualifying_delta
    - f1_prediction.features.prerace.compute_practice_pace
    - f1_prediction.features.prerace.compute_circuit_win_rate
    - f1_prediction.features.prerace.compute_circuit_avg_finish
    - f1_prediction.features.prerace.compute_constructor_pace_delta
    - f1_prediction.features.prerace.compute_championship_position
    - f1_prediction.features.prerace.compute_grid_position
    - f1_prediction.features.prerace.compute_driver_seasons
    - f1_prediction.features.prerace.compute_finish_position
    - f1_prediction.features.inrace.compute_inrace_features_for_session
    - f1_prediction.features.builder.build_features_for_session
    - f1_prediction.features.builder.build_all_features
    - f1_prediction.live.session_window.is_session_active
    - f1_prediction.live.session_window.find_current_session
    - f1_prediction.live.poller.poll_once
    - f1_prediction.live.poller.start_poller
    - f1_prediction.live.poller.stop_poller
    - scripts/build_features.py CLI
    - scripts/live_poller.py CLI
  affects:
    - Phase 2 (reads features_prerace + features_inrace for model training)
    - Phase 3 (wires start_poller into FastAPI lifespan for race-day inference)

tech_stack:
  added:
    - apscheduler==3.11.2 (BackgroundScheduler for live polling; installed during plan execution)
  patterns:
    - Pure functions per feature column (compute_* in prerace.py) — auditable by Phase 2
    - Builder processes sessions in chronological (year, round_number) order so cross-session aggregates see prior data
    - DELETE-then-INSERT for idempotent feature rebuilds (clean re-run support D-13)
    - asyncio.run() at top of poll_once (not nested) — one event loop per APScheduler job
    - max_instances=1 + misfire_grace_time=15 prevent concurrent poll overrun (T-03-02)
    - EVENT_JOB_ERROR listener logs all scheduler job exceptions (T-03-07)
    - ±30-minute session window absorbs clock skew (Pitfall 7)
    - TDD RED-GREEN cycle for Task 2 (test commit before implementation commit)

key_files:
  created:
    - src/f1_prediction/features/__init__.py: Empty package init
    - src/f1_prediction/features/prerace.py: 9 pure feature functions for features_prerace
    - src/f1_prediction/features/inrace.py: compute_inrace_features_for_session joining 4 raw tables
    - src/f1_prediction/features/builder.py: build_features_for_session, build_all_features
    - src/f1_prediction/live/__init__.py: Empty package init
    - src/f1_prediction/live/session_window.py: is_session_active (±30min), find_current_session
    - src/f1_prediction/live/poller.py: poll_once, start_poller, stop_poller
    - scripts/build_features.py: Standalone feature rebuild CLI with --only-years
    - scripts/live_poller.py: Live polling CLI with --session-key (required) and --interval
    - tests/test_features.py: 10 tests for feature builder
    - tests/test_live_poller.py: 6 tests for live poller (TDD)
  modified:
    - src/f1_prediction/db/models.py: Added FeaturesPrerace, FeaturesInrace ORM models (already in Plan 02 stub — fully implemented here)
    - scripts/backfill.py: Wired build_all_features call at end (--skip-features honored)

decisions:
  - "D-02/D-03: Feature table columns exactly match CONTEXT.md — non-negotiable contract to Phase 2"
  - "D-04: circuit_win_rate, circuit_avg_finish, constructor_pace_delta pre-computed and stored in features_prerace; chronological session ordering ensures prior data is available"
  - "D-08: 107% rule applied in compute_practice_pace (not at ingest); all laps remain in raw_laps"
  - "D-13: build_all_features triggered from backfill.py unless --skip-features; standalone build_features.py also provided"
  - "grid_position approximation: lap-1 raw_positions used (FastF1 GridPosition not stored in Plan 02); documented in compute_grid_position docstring"
  - "gap_to_leader approximation: cumulative sum of lap_duration per driver vs minimum (standard F1 timing-screen method for historical data)"
  - "team_name stored in features_prerace to avoid runtime driver→team join; looked up from prior features_prerace rows when not in raw schema"
  - "poll_once accepts _is_active and _client injection parameters for test determinism without monkeypatching"
  - "stints replaced per poll (DELETE+INSERT); laps/positions/pits deduped in Python by natural key before insert"

metrics:
  duration_minutes: 30
  completed_date: "2026-06-17"
  tasks_completed: 2
  files_created: 11
---

# Phase 1 Plan 3: Feature Engineering + Live Poller Summary

**One-liner:** Two feature tables populated from raw SQL joins (qualifying delta, 107% practice pace, circuit win rates, in-race gap/tyre/pit features) plus APScheduler live poller with ±30-min session window guard and 34 total tests passing across Phase 1.

## What Was Built

Plan 03 completes Phase 1 by adding the two feature tables and the live polling layer:

1. **ORM models** (`FeaturesPrerace`, `FeaturesInrace`) with composite PKs matching D-02/D-03 exactly — already stubbed in models.py, fully implemented here.
2. **Pre-race feature builder** (`features/prerace.py`) with 9 pure functions, one per column, each reading from SQLAlchemy connections with parameterized SQL.
3. **In-race feature builder** (`features/inrace.py`) joining `raw_laps + raw_positions + raw_stints + raw_pit_stops` via pandas merge operations.
4. **Builder orchestrator** (`features/builder.py`) processing sessions in chronological order so cross-session aggregates read prior data correctly.
5. **Standalone CLI** (`scripts/build_features.py`) with `--only-years` for partial rebuilds.
6. **Backfill wiring** (`scripts/backfill.py`) now calls `build_all_features` at end unless `--skip-features`.
7. **Session window guard** (`live/session_window.py`) with `is_session_active` (±30-min window) and `find_current_session`.
8. **Live poller** (`live/poller.py`) using APScheduler `BackgroundScheduler` with `max_instances=1`, `misfire_grace_time=15`, `EVENT_JOB_ERROR` listener, and `asyncio.run()` at top of `poll_once`.
9. **Live poller CLI** (`scripts/live_poller.py`) with `--session-key` (required int) and `--interval` (default 30).

## Feature Table Schemas

### features_prerace (D-02)

| Column | Type | Notes |
|--------|------|-------|
| `session_key` | INT (PK) | Race session key |
| `driver_number` | INT (PK) | Driver number |
| `qualifying_delta_to_pole` | FLOAT NULL | Gap in seconds to P1 qualifier |
| `practice_pace_fp2` | FLOAT NULL | Driver median FP2 vs field median (negative=faster) |
| `practice_pace_fp3` | FLOAT NULL | Driver median FP3 vs field median; NULL on sprint weekends |
| `circuit_win_rate` | FLOAT NULL | wins/starts at this circuit; NULL for circuit debut |
| `circuit_avg_finish` | FLOAT NULL | Constructor avg finish at circuit, last 2 seasons |
| `constructor_pace_delta` | FLOAT NULL | Constructor practice pace vs field, last 2 seasons |
| `championship_position` | INT NULL | Standings rank before this race; NULL for season opener |
| `grid_position` | INT NULL | Starting grid (approximated from lap-1 raw_positions) |
| `driver_seasons_in_f1` | INT | Distinct seasons with raw_laps data |
| `finish_position` | INT NULL | Target label (1-based); NULL if no position data |
| `team_name` | TEXT NULL | Constructor name (avoids runtime driver→team join) |

### features_inrace (D-03)

| Column | Type | Notes |
|--------|------|-------|
| `session_key` | INT (PK) | Race session key |
| `driver_number` | INT (PK) | Driver number |
| `lap_number` | INT (PK) | Current lap number |
| `gap_to_leader` | FLOAT NULL | cumsum(lap_duration) minus leader cumsum at this lap |
| `tyre_compound` | TEXT NULL | SOFT/MEDIUM/HARD/INTERMEDIATE/WET from raw_stints |
| `tyre_age_laps` | INT NULL | (lap - stint.lap_start) + tyre_age_at_start |
| `pit_stop_count` | INT (0) | Cumulative pits as of this lap |
| `position` | INT NULL | Race position (1–20) from raw_positions |
| `interval_ahead` | FLOAT NULL | gap_to_leader delta to car at position-1 |
| `interval_behind` | FLOAT NULL | gap_to_leader delta to car at position+1 |
| `pit_stop_delta` | FLOAT NULL | lane_duration of pit stop on this lap |
| `laps_remaining` | INT NULL | total_laps - lap_number |

## Per-Feature Computation Rules

### qualifying_delta_to_pole
Source: `raw_laps` for the Qualifying session matching (year, round_number).
Formula: `MIN(lap_duration) per driver (excl pit-out laps)` — delta vs minimum best lap.
Sprint Shootout fallback: if no Qualifying session, use Sprint Shootout for the same round.

### practice_pace_fp2 / practice_pace_fp3
Source: `raw_laps` for the FP2 or FP3 session matching (year, round_number).
Filter (D-08): exclude pit-out laps; apply 107% rule (`lap_duration <= field_median * 1.07`).
Formula: `driver_median - field_median` (recalculated post-filter).
Returns NULL if session absent (sprint weekends without FP3).

### circuit_win_rate
Source: `features_prerace` rows where `finish_position = 1` at this circuit, years < current.
Requires chronological processing (D-04) so prior year rows exist.
Returns NULL for circuit debut.

### circuit_avg_finish
Source: `features_prerace` rows for same team and circuit, years `[year-2, year-1]`.
Returns NULL if no constructor data available.

### constructor_pace_delta
Source: `features_prerace.practice_pace_fp2/fp3` for same team and circuit, last 2 seasons.
Averages available practice paces when only one of FP2/FP3 exists.

### championship_position
Source: `features_prerace.finish_position` for all races in this year up to round_number-1.
FIA 2022+ scoring: 25-18-15-12-10-8-6-4-2-1 for positions 1-10.
Returns NULL for season opener (no prior races).

### grid_position
Approximation: `raw_positions WHERE lap_number = 1 AND driver_number = ? ORDER BY date_recorded ASC LIMIT 1`.
More accurate than nothing but may reflect formation lap order, not official grid.
Phase 4 may add `raw_race_results` table with FastF1's `GridPosition` if accuracy is critical.

### gap_to_leader (in-race)
Approximation: `cumsum(lap_duration)` per driver, subtracted from minimum cumsum per lap.
Accurate for historical data; live races get actual gap from OpenF1 `/v1/intervals`.

### interval_ahead / interval_behind (in-race)
Derived from `gap_to_leader` differences between adjacent race positions.
NULL for leader (no car ahead) and last car (no car behind).

## Live Poller Lifecycle

| Parameter | Value | Source |
|-----------|-------|--------|
| Poll interval | 30 seconds (default) | RESEARCH Pattern 5 |
| max_instances | 1 | T-03-02 (prevents concurrent polls) |
| misfire_grace_time | 15 seconds | T-03-02 (skip if >15s late) |
| EVENT_JOB_ERROR listener | Yes | T-03-07 (logs exceptions without crashing) |
| asyncio.run() position | Top of poll_once | RESEARCH anti-pattern (not nested) |

### Session Window (±30 min)
`is_session_active` returns True iff:
```
date_start - 30min <= now <= date_end + 30min
```
Both UTC. Clock skew of ±minutes is absorbed by the window (T-03-06 accepted).

### Poll Flow
1. `is_session_active` check — no-op if outside window
2. `SELECT MAX(lap_number)` from `raw_laps` — dedupe gate
3. Fetch laps from OpenF1, filter `lap_number > max_lap`, insert new rows
4. Replace stints (DELETE + INSERT — small, cleaner than diffing)
5. Fetch positions and pit stops, dedupe in Python by natural key, insert

## Chronological Ordering Requirement

`build_all_features` orders sessions by `(year, round_number) ASC` so:
- `circuit_win_rate` for Bahrain 2024 can read 2022 and 2023 Bahrain finish data
- `circuit_avg_finish` and `constructor_pace_delta` similarly see prior season entries

**If sessions are processed out of order, cross-session aggregates return NULL unexpectedly.**

## Test Results

```
tests/test_features.py::test_feature_table_schema_and_pk PASSED
tests/test_features.py::test_build_prerace_features_row_count PASSED
tests/test_features.py::test_build_inrace_features_row_count PASSED
tests/test_features.py::test_qualifying_delta_to_pole_values PASSED
tests/test_features.py::test_rebuild_is_idempotent_no_duplicates PASSED
tests/test_features.py::test_null_handling_when_fp3_absent PASSED
tests/test_features.py::test_practice_pace_fp2_values PASSED
tests/test_features.py::test_build_all_features_returns_dict PASSED
tests/test_features.py::test_non_race_session_is_skipped PASSED
tests/test_features.py::test_inrace_tyre_and_laps_remaining PASSED

tests/test_live_poller.py::test_session_window_active PASSED
tests/test_live_poller.py::test_session_window_inactive PASSED
tests/test_live_poller.py::test_poll_once_inserts_only_new_laps PASSED
tests/test_live_poller.py::test_poll_once_noop_when_inactive PASSED
tests/test_live_poller.py::test_poller_runs_3_intervals PASSED
tests/test_live_poller.py::test_poller_lifecycle PASSED

tests/test_skeleton.py: 3 PASSED  (Plan 01)
tests/test_ingest_full.py: 15 PASSED  (Plan 02)

Total: 34 passed in ~11s
```

### Test Breakdown

| Suite | Plan | Tests |
|-------|------|-------|
| test_skeleton.py | 01 | 3 |
| test_ingest_full.py | 02 | 15 |
| test_features.py | 03 (Task 1) | 10 |
| test_live_poller.py | 03 (Task 2 TDD) | 6 |
| **Total** | | **34** |

## TDD Gate Compliance

Task 2 followed RED-GREEN cycle:

1. **RED gate:** commit `7b566e3` — `test(01-03): add failing tests for live poller — session window, poll_once dedupe, scheduler lifecycle`
2. **GREEN gate:** commit `e504cb1` — `feat(01-03): APScheduler live poller, session window guard, and live_poller CLI`

Both gates satisfied.

## Deviations from Plan

### Auto-fixed Issues

None — plan executed as specified.

### Implementation Notes

**1. Task 1 and build_features.py already present**
- Plan 03 found that `features/` module and `scripts/build_features.py` had already been partially created (likely from a prior interrupted session). Verified they matched the plan specifications exactly and all 10 feature tests passed. No re-implementation needed.

**2. APScheduler not installed**
- `apscheduler==3.11.2` was not present in the Python environment. Installed via `pip3.11 install apscheduler==3.11.2`. This was not a new package choice (it was audited in Plans 01-02); just not yet installed. No checkpoint needed.

**3. poll_once dependency injection**
- The plan specified `poll_once` should accept an optional `client` parameter. Extended this to also accept `_is_active` for cleaner test isolation without requiring `monkeypatch`. The `_client` and `_is_active` parameters are marked with underscore convention to indicate they are test-facing, not part of the public API. This is a minor deviation that improves testability without altering the plan's intent.

## Known Stubs

None — all feature columns are wired. The following legitimately NULL values exist:

- `circuit_win_rate`: NULL for circuit debut (no prior data — correct behavior)
- `circuit_avg_finish`, `constructor_pace_delta`: NULL if no prior constructor data at this circuit (correct)
- `championship_position`: NULL for season opener (no prior races in year — correct)
- `practice_pace_fp3`: NULL on sprint weekends without FP3 (correct per D-07)
- `team_name`: NULL for a driver's very first race where no prior `features_prerace` exists (lookup falls back to None; Phase 2 can handle with categorical encoding)

## Deferred Items

| Item | Phase | Notes |
|------|-------|-------|
| grid_position accuracy | Phase 4 | Current approximation uses lap-1 raw_positions; FastF1 GridPosition from session.results would be more accurate but requires a new `raw_race_results` table |
| Sprint weekend FP3 substitute | Phase 2/4 | Sprint sessions used as race-pace proxy per D-07; practice_pace_fp3 is NULL on sprint weekends — Phase 2 should handle NULL in model training |
| team_name for debut races | Phase 2 | NULL for drivers' first race — Phase 2 categorical encoder must handle None as a valid category |
| OpenF1 intervals for live gap_to_leader | Phase 3 | Historical: computed from cumulative lap times. Live: OpenF1 /v1/intervals provides actual value — Phase 3 inference loop should use intervals for live races |

## Threat Surface Scan

No new network endpoints introduced. All threat mitigations from the plan implemented:

| Threat | Status |
|--------|--------|
| T-03-01: Per-field whitelist in poll_once (_LAP_FIELDS, etc.) | Implemented — only whitelisted fields extracted from API response |
| T-03-02: max_instances=1 + misfire_grace_time=15 in add_job | Verified — `grep -E "max_instances=1|misfire_grace_time=15" poller.py` shows both lines |
| T-03-03: tenacity stop_after_attempt(6) from Plan 02 reused | Implemented — OpenF1Client retry policy unchanged |
| T-03-04: --session-key cast to int by argparse | Implemented — `type=int` in argparse |
| T-03-05: No f-string SQL in features/ | Verified — `grep -rn "f\"SELECT\|f'SELECT" src/` returns no matches |
| T-03-06: Clock skew accepted | ±30-min window absorbs reasonable skew |
| T-03-07: EVENT_JOB_ERROR listener | Implemented — `grep -c "EVENT_JOB_ERROR" poller.py` = 3 |
| T-03-SC: No new packages (except APScheduler install) | APScheduler was pre-approved in Plans 01-02 audit; just not yet installed |

## Self-Check: PASSED

### Files verified present:

- `src/f1_prediction/features/__init__.py` — FOUND
- `src/f1_prediction/features/prerace.py` — FOUND (9 compute_* functions)
- `src/f1_prediction/features/inrace.py` — FOUND (compute_inrace_features_for_session)
- `src/f1_prediction/features/builder.py` — FOUND (build_features_for_session, build_all_features)
- `src/f1_prediction/live/__init__.py` — FOUND
- `src/f1_prediction/live/session_window.py` — FOUND (is_session_active, find_current_session)
- `src/f1_prediction/live/poller.py` — FOUND (poll_once, start_poller, stop_poller)
- `scripts/build_features.py` — FOUND (--only-years arg)
- `scripts/live_poller.py` — FOUND (--session-key, --interval args)
- `tests/test_features.py` — FOUND (10 tests)
- `tests/test_live_poller.py` — FOUND (6 tests)

### Commits verified:

- `d8c1791` — feat(01-03): feature tables ORM, builder, prerace/inrace functions, CLI integration — FOUND
- `7b566e3` — test(01-03): add failing tests for live poller (RED) — FOUND
- `e504cb1` — feat(01-03): APScheduler live poller, session window guard, live_poller CLI (GREEN) — FOUND
