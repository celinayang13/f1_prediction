---
phase: 01-data-features
plan: "01"
subsystem: data-ingest
status: complete
tags:
  - python
  - sqlite
  - sqlalchemy
  - fastf1
  - walking-skeleton
  - wal-mode
  - tdd

dependency_graph:
  requires: []
  provides:
    - f1_prediction.db.engine.get_engine
    - f1_prediction.db.engine.init_db
    - f1_prediction.db.engine.DB_PATH
    - f1_prediction.db.models.Base
    - f1_prediction.db.models.RawSession
    - f1_prediction.db.models.RawLap
    - f1_prediction.db.upsert.insert_or_skip_session
    - f1_prediction.db.upsert.bulk_insert_rows
    - f1_prediction.ingest.normalizer.normalize_fastf1_session
    - f1_prediction.ingest.normalizer.normalize_fastf1_laps
    - f1_prediction.ingest.fastf1_loader.load_session
    - scripts/backfill.py CLI
  affects:
    - Plan 02 (widens raw_sessions + raw_laps; adds raw_stints, raw_pit_stops, etc.)
    - Plan 03 (feature builder reads raw_sessions + raw_laps produced here)

tech_stack:
  added:
    - sqlalchemy==2.0.51 (ORM + schema management)
    - tenacity==9.1.4 (retry with exponential backoff for Plan 02)
    - pytest==8.3.4 (test runner)
    - httpx==0.28.1 (async HTTP client for Plan 02 OpenF1 polling)
    - fastf1==3.8.1 (already in venv; confirmed working with system Python 3.11)
  patterns:
    - SQLAlchemy 2.0 DeclarativeBase + event listener for WAL pragma
    - pandas bulk insert via to_sql(method='multi', chunksize=500)
    - FastF1 cache enabled once per process at data/cache/
    - Parameterized SQL via sqlalchemy text() — no f-string SQL (T-01-02)

key_files:
  created:
    - pyproject.toml: PEP 621 project metadata, dependency pins, pytest config
    - .gitignore: Ignores *.db, data/cache/, __pycache__, .pytest_cache, .venv
    - src/f1_prediction/__init__.py: Package version string __version__='0.1.0'
    - src/f1_prediction/db/__init__.py: Empty package init
    - src/f1_prediction/db/engine.py: get_engine() with WAL event listener, init_db()
    - src/f1_prediction/db/models.py: Base, RawSession (raw_sessions), RawLap (raw_laps)
    - src/f1_prediction/db/upsert.py: insert_or_skip_session() and bulk_insert_rows()
    - src/f1_prediction/ingest/__init__.py: Empty package init
    - src/f1_prediction/ingest/normalizer.py: normalize_fastf1_session(), normalize_fastf1_laps()
    - src/f1_prediction/ingest/fastf1_loader.py: load_session() pipeline entrypoint
    - scripts/backfill.py: CLI with --year, --round, --identifier argparse args
    - tests/__init__.py: Empty package init
    - tests/conftest.py: tmp_engine fixture using tmp_path for test isolation (T-01-05)
    - tests/test_skeleton.py: 3 end-to-end skeleton tests

decisions:
  - "session_key synthesis for FastF1 2022 data: year*100_000 + round*100 + session_type_code (1-7)"
  - "WAL mode via SQLAlchemy event listener on connect — no per-query PRAGMA needed"
  - "numpy scalar coercion: .iloc[i] returns numpy.int64 — must wrap in int()/float() in row assembly"
  - "System Python 3.11 used (not venv) — venv has stub dist-info directories without source files"

metrics:
  duration_minutes: 9
  completed_date: "2026-06-16"
  tasks_completed: 3
  files_created: 14
---

# Phase 1 Plan 1: Walking Skeleton Summary

**One-liner:** FastF1-to-SQLite walking skeleton with WAL mode, ORM schema, normalizer, and 3 passing end-to-end tests for 2022 Bahrain race ingest.

## What Was Built

The Phase 1 walking skeleton proves the full data pipeline from FastF1 → normalizer → SQLAlchemy ORM → WAL-mode SQLite works end-to-end against a real 2022 race session. Three pytest tests confirm the pipeline operates correctly and D-11 resumability prevents duplicate inserts.

## Dependencies Installed

| Package | Version | Purpose |
|---------|---------|---------|
| httpx | 0.28.1 | Async HTTP client for OpenF1 polling (Plan 02) |
| sqlalchemy | 2.0.51 | ORM and schema management |
| tenacity | 9.1.4 | Retry with exponential backoff (Plan 02) |
| pytest | 8.3.4 | Test runner |
| fastf1 | 3.8.1 | Historical 2022 data (pre-existing, confirmed working) |

**Installation note:** The `.venv` directory has stub dist-info entries without actual source files. Packages were installed to system Python 3.11 (`/Library/Frameworks/Python.framework/Versions/3.11/`) which is the Python used throughout this project.

## Files Created

| File | Purpose |
|------|---------|
| `pyproject.toml` | PEP 621 project metadata with pinned dependencies and pytest config |
| `.gitignore` | Ignores `*.db`, `data/cache/`, `__pycache__`, `.pytest_cache`, `.venv` |
| `src/f1_prediction/__init__.py` | Package root with `__version__ = "0.1.0"` |
| `src/f1_prediction/db/__init__.py` | Empty package init |
| `src/f1_prediction/db/engine.py` | `get_engine()` factory with WAL PRAGMA event listener; `init_db()` helper |
| `src/f1_prediction/db/models.py` | `Base`, `RawSession` (raw_sessions), `RawLap` (raw_laps) ORM models |
| `src/f1_prediction/db/upsert.py` | `insert_or_skip_session()` + `bulk_insert_rows(chunksize=500)` |
| `src/f1_prediction/ingest/__init__.py` | Empty package init |
| `src/f1_prediction/ingest/normalizer.py` | `normalize_fastf1_session()` + `normalize_fastf1_laps()` |
| `src/f1_prediction/ingest/fastf1_loader.py` | `load_session()` — full FastF1 → ORM pipeline |
| `scripts/backfill.py` | CLI with `--year`, `--round`, `--identifier` argparse args |
| `tests/__init__.py` | Empty package init |
| `tests/conftest.py` | `tmp_engine` fixture for test DB isolation (T-01-05) |
| `tests/test_skeleton.py` | 3 end-to-end skeleton tests |

**Deleted:** `src/f1_poller/` — empty stub directory from prior exploratory work.

## Exported Symbols

### `f1_prediction.db.engine`
- `DB_PATH: Path` — default `project-root/data/f1.db`
- `get_engine(db_path=None) -> Engine` — creates SQLite engine with WAL event listener
- `init_db(engine) -> None` — `Base.metadata.create_all(engine)` wrapper

### `f1_prediction.db.models`
- `Base` — `DeclarativeBase` subclass
- `RawSession` — `raw_sessions` table (session_key PK, year, round_number, country_name, circuit_short_name, session_type, session_name, date_start, date_end, total_laps, source)
- `RawLap` — `raw_laps` table (id autoincrement PK, session_key, driver_number, lap_number, lap_duration, duration_sector_1/2/3, is_pit_out_lap, i1_speed, i2_speed, st_speed, date_start) + composite index `ix_raw_laps_session_driver_lap` on (session_key, driver_number, lap_number)

### `f1_prediction.db.upsert`
- `insert_or_skip_session(engine, session_row) -> bool` — D-11 resumability via parameterized SQL check
- `bulk_insert_rows(engine, table_name, rows) -> int` — chunked bulk insert (chunksize=500)

### `f1_prediction.ingest.normalizer`
- `normalize_fastf1_session(session, *, year, round_number) -> dict` — produces raw_sessions row
- `normalize_fastf1_laps(laps_df, *, session_key) -> list[dict]` — produces raw_laps rows

### `f1_prediction.ingest.fastf1_loader`
- `load_session(year, round_number, identifier='R', *, engine=None) -> dict` — returns `{session_key, laps_inserted, skipped}`

## session_key Synthesis Scheme

For FastF1 2022 data, session keys are synthesized as:

```
session_key = year * 100_000 + round_number * 100 + session_type_code
```

Where `session_type_code` maps: FP1=1, FP2=2, FP3=3, Q=4, SS=5, S=6, R=7

Examples:
- 2022 Round 1 Race (Bahrain): 202,200,107
- 2022 Round 22 Race (Abu Dhabi): 202,202,207

**No collision with OpenF1:** OpenF1 server-assigned keys for 2023+ are in the millions (the 2023 Bahrain Race is session_key 9472). Our synthetic keys are > 200 million, ensuring no overlap.

**Plan 02 impact:** When Plan 02 starts inserting OpenF1 2023+ data, no key-collision check is needed — the ranges are disjoint by construction.

## Test Results

All 3 skeleton tests pass against 2022 Bahrain Race (round 1):

```
tests/test_skeleton.py::test_walking_skeleton_loads_one_2022_race PASSED
tests/test_skeleton.py::test_walking_skeleton_is_resumable PASSED
tests/test_skeleton.py::test_wal_mode_active PASSED

3 passed in 11.17s (first run, network fetch)
3 passed in 1.21s (second run, FastF1 cache hit)
```

- `data/f1.db` was NOT created (tests use `tmp_engine` fixture — T-01-05 satisfied)
- `data/cache/` now contains FastF1 cached session files (expected side effect)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] numpy scalar type coercion in normalize_fastf1_laps**
- **Found during:** Task 2 verification
- **Issue:** `pandas.Series.apply()` with `int()` returns `numpy.int64` when all values are integers (pandas infers dtype). The `.iloc[i]` accessor on the resulting Series also returns `numpy.int64`, not Python `int`. This caused the acceptance criterion check (`type(result[0]['driver_number']) == int`) to fail.
- **Fix:** Added `_pyint()` and `_pyfloat()` helper functions in `normalize_fastf1_laps()` that explicitly call `int()` and `float()` when assembling the final row dict, converting numpy scalars to native Python types.
- **Files modified:** `src/f1_prediction/ingest/normalizer.py`
- **Commit:** `7f9b22e`

**2. [Rule 3 - Blocking] venv structure unusable for package imports**
- **Found during:** Task 1 verification
- **Issue:** The `.venv` directory has stub dist-info directories and empty package directories without actual source `.py` files. The packages (fastf1, pandas, numpy, apscheduler) registered in the venv are hollow stubs that shadow the system Python packages, causing `AttributeError: module 'fastf1' has no attribute '__version__'` and similar errors when using `PYTHONPATH=.venv/...`.
- **Fix:** Used system Python 3.11 (`/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11`) directly, and installed missing packages (`sqlalchemy`, `tenacity`, `pytest`, `httpx`, `fastf1`) to the user site-packages. This is consistent with the RESEARCH.md note: "The `.venv` directory has `lib/python3.11/site-packages/` populated but no `bin/` executables."
- **Impact:** All `python3.11` commands work correctly. The `pyproject.toml` and `PYTHONPATH=src` usage is identical.
- **Files modified:** None (install-only fix)

## Known Stubs

None — all code paths are fully implemented. `date_end` and `total_laps` columns in `raw_sessions` are intentionally `None` for Plan 01 (documented in models.py and normalizer.py; Plan 02 populates them from session results and event metadata).

## Threat Surface Scan

No new network endpoints or auth paths introduced. All security mitigations from the plan's threat model were implemented:

| Threat | Status |
|--------|--------|
| T-01-01: FastF1 columns explicitly enumerated in normalizer | Implemented |
| T-01-02: Parameterized SQL in upsert.py (`text()` with `:sk`) | Implemented — verified no f-string SQL via grep |
| T-01-03: Cache path anchored to package directory | Implemented — `Path(__file__).resolve().parents[3] / "data" / "cache"` |
| T-01-04: `telemetry=False` in session.load() | Implemented — verified `grep -c "telemetry=False" fastf1_loader.py` = 2 |
| T-01-05: Tests use `tmp_engine` fixture (not data/f1.db) | Implemented — verified `data/f1.db` not created by test suite |

## Self-Check: PASSED

All files exist on disk (14 created, 1 deleted):
- pyproject.toml, .gitignore: FOUND
- src/f1_prediction/{__init__,db/__init__,db/engine,db/models,db/upsert}.py: FOUND
- src/f1_prediction/{ingest/__init__,ingest/normalizer,ingest/fastf1_loader}.py: FOUND
- scripts/backfill.py: FOUND
- tests/{__init__,conftest,test_skeleton}.py: FOUND
- src/f1_poller/: DELETED (confirmed absent)

All commits exist:
- 024871a: feat(01-01): scaffold project, install deps, define ORM base + engine + schema
- 7f9b22e: feat(01-01): add FastF1 normalizer, session loader, and backfill CLI
- 275c106: test(01-01): add failing skeleton tests for FastF1 → SQLite pipeline
