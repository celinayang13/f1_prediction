# Phase 1: Data & Features - Research

**Researched:** 2026-06-16
**Domain:** F1 data ingestion — FastF1 historical backfill, OpenF1 live polling, SQLite storage, feature engineering
**Confidence:** MEDIUM

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Feature Table Schema**
- D-01: Two separate feature tables — `features_prerace` (one row per session_key + driver_number) and `features_inrace` (one row per session_key + driver_number + lap_number).
- D-02: `features_prerace` columns: `qualifying_delta_to_pole`, `practice_pace_fp2`, `practice_pace_fp3`, `circuit_win_rate`, `circuit_avg_finish`, `constructor_pace_delta`, `championship_position`, `grid_position`, `driver_seasons_in_f1`, `finish_position` (target label).
- D-03: `features_inrace` columns: `gap_to_leader`, `tyre_compound`, `tyre_age_laps`, `pit_stop_count`, `position`, `interval_ahead`, `interval_behind`, `pit_stop_delta`, `lap_number`, `laps_remaining`.
- D-04: Circuit-specific stats pre-computed in Phase 1 and stored in `features_prerace`. Phase 2 reads directly — no SQL aggregation at training time.
- D-05: Target label stored as `finish_position` (int), not boolean.

**Practice Sessions Scope**
- D-06: Backfill FP2 and FP3 only. FP1 skipped at ingest.
- D-07: Sprint weekends — include the sprint session as a practice substitute. ETL must detect session type and route accordingly.
- D-08: Store all laps raw with no filtering at ingest. Filtering happens in the feature builder.

**Raw Data Column Depth**
- D-09 (Claude's Discretion): `raw_laps` stores all fields returned by OpenF1 laps endpoint (~15 columns).
- D-10: FastF1 (2022 data) is normalized to the OpenF1 schema at ingest. FastF1-specific columns dropped.

**Backfill Strategy**
- D-11: `scripts/backfill.py` is resumable — skips sessions already in `raw_sessions`.
- D-12 (Claude's Discretion): Rate limiting via retry with exponential backoff on HTTP 429 or timeout.
- D-13: Single pipeline — `backfill.py` triggers feature builder at end. Expose `--skip-features` flag and standalone `build_features.py`.

### Claude's Discretion
- D-09: Full column mirror for `raw_laps` (all OpenF1 fields).
- D-12: Exponential backoff retry strategy rather than fixed sleep.

### Deferred Ideas (OUT OF SCOPE)
- Driver age / biographical features beyond `driver_seasons_in_f1`
- Weather integration beyond wet/dry/mixed flag
- Real-time telemetry fields (throttle, braking, speed at specific points)
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DATA-01 | System fetches and stores all historical F1 race, qualifying, and practice sessions from OpenF1 API for 2022–2025 | FastF1 for 2022; OpenF1 `/v1/laps`, `/v1/stints`, `/v1/pit`, `/v1/position`, `/v1/sessions` for 2023–2025; SQLAlchemy 2.0 + SQLite for storage |
| DATA-02 | System stores weather conditions (wet/dry/mixed) per session | OpenF1 `/v1/weather` endpoint (`rainfall` field = 0/1); FastF1 `session.weather_data` DataFrame (`Rainfall` boolean). Derive wet/dry/mixed from rainfall flag per session. |
| DATA-03 | System polls OpenF1 live during a race weekend and stores lap-by-lap position, interval, and lap time data | APScheduler `BackgroundScheduler` polls `/v1/laps`, `/v1/intervals`, `/v1/position` every 30s. Session status detected via `/v1/sessions` date window. |
| DATA-04 | System stores tyre compound and age per driver per stint | OpenF1 `/v1/stints` fields: `compound`, `tyre_age_at_start`, `lap_start`, `lap_end`, `stint_number`. Joinable to `raw_laps` on (session_key, driver_number, lap_number). |
</phase_requirements>

---

## Summary

Phase 1 builds the data foundation: five raw tables populated by a backfill script (2022 via FastF1, 2023–2025 via OpenF1), a live poller for race day, and two feature tables derived from the raw data. The work is purely data engineering — no model code.

The data source split is the central complexity. FastF1 3.8.1 covers 2022+ via its internal cache but uses a different schema than OpenF1's REST API; the ETL layer must normalize FastF1 output to the OpenF1 column set. For 2023–2025, OpenF1's REST API provides clean JSON responses with a consistent schema. The live poller is a straightforward APScheduler `BackgroundScheduler` job that polls five OpenF1 endpoints every 30 seconds and upserts into the same raw tables.

Feature engineering is the second major component. The feature builder reads from raw tables and writes to `features_prerace` and `features_inrace`. Computing `circuit_win_rate`, `circuit_avg_finish`, and `constructor_pace_delta` requires aggregating across multiple seasons in raw tables — these aggregations run once and are stored in `features_prerace`, so Phase 2 never needs to join across raw tables.

**Primary recommendation:** Implement a `src/f1_prediction/db/` module with SQLAlchemy models and the WAL-mode engine config, a `src/f1_prediction/ingest/` module with separate `fastf1_loader.py` and `openf1_client.py`, and a `src/f1_prediction/features/` module with the builder. `scripts/backfill.py` and `scripts/build_features.py` are thin CLI wrappers over these modules.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Historical data fetch (2022) | Script / CLI | — | FastF1 is synchronous; run as a one-shot backfill script, not a server |
| Historical data fetch (2023–2025) | Script / CLI | — | OpenF1 REST via httpx; rate-limited batch, not a server concern |
| Live race polling | Background thread (APScheduler) | — | Non-blocking, in-process; must run alongside Phase 3's FastAPI server |
| Data storage (raw + features) | Database / Storage (SQLite) | — | All tiers read/write SQLite; WAL mode handles concurrent access |
| Feature engineering | Script / CLI | — | Stateless transformation; runs after backfill or on-demand |
| Schema management | Database / Storage | — | SQLAlchemy ORM with `Base.metadata.create_all()` |

---

## Standard Stack

### Core — All already installed in `.venv` (verified from dist-info directories)

| Library | Version (Installed) | Purpose | Why Standard |
|---------|---------------------|---------|--------------|
| fastf1 | 3.8.1 | Load 2022 historical session data (laps, stints, results, weather) | Only Python library with 2022 F1 telemetry; built-in file cache avoids re-fetching |
| pandas | 2.3.3 | DataFrame manipulation for FastF1 data and feature engineering | FastF1 returns DataFrames natively; standard for tabular ETL |
| numpy | 2.4.3 | Numerical operations in feature computation | Standard; required by pandas and sklearn |
| apscheduler | 3.11.2 | Background poll scheduler for live race day | BackgroundScheduler runs in-process without blocking ASGI loop |
| requests | 2.32.5 | Already installed; used internally by FastF1 | Do not use directly — use httpx for new code |
| requests-cache | 1.3.1 | FastF1 uses this internally for its HTTP cache layer | Do not use directly |

### Needs Installation

| Library | Version (PyPI current) | Purpose | Installation |
|---------|------------------------|---------|--------------|
| httpx | 0.28.1 | Async HTTP client for OpenF1 REST polling | `pip install httpx==0.28.1` |
| sqlalchemy | 2.0.51 | ORM and schema management for SQLite | `pip install sqlalchemy==2.0.51` |
| tenacity | 9.1.4 | Retry with exponential backoff on HTTP 429/timeout | `pip install tenacity==9.1.4` |

**Note on version discrepancies from CLAUDE.md:** CLAUDE.md lists `fastf1 3.8.3`, `scikit-learn 1.7.2`, `numpy 2.2.6`, `scipy 1.15.3` but the installed venv has `fastf1 3.8.1`, `scikit-learn 1.8.0`, `numpy 2.4.3`, `scipy 1.17.1`. The installed versions are newer and should be used as-is — do not downgrade. [VERIFIED: dist-info directory names in .venv/lib/python3.11/site-packages/]

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| tenacity for retry | Hand-rolled retry loop | tenacity is 3 lines vs 20+; handles Retry-After header, jitter, logging |
| SQLAlchemy ORM | Raw sqlite3 module | ORM gives schema migration path if tables evolve; CLAUDE.md decision |
| APScheduler BackgroundScheduler | asyncio.create_task loop | BackgroundScheduler is simpler to start/stop from FastAPI lifespan; no async complexity in Phase 1 |

**Installation (all Phase 1 missing packages):**
```bash
pip install httpx==0.28.1 sqlalchemy==2.0.51 tenacity==9.1.4
```

---

## Package Legitimacy Audit

> All packages verified to exist on PyPI. All `SUS` verdicts are due to PyPI not exposing download count data to the legitimacy seam — not genuine legitimacy concerns. All packages have GitHub source repos from authoritative maintainers.

| Package | Registry | Age | Source Repo | Verdict | Disposition |
|---------|----------|-----|-------------|---------|-------------|
| fastf1 | PyPI | Active since 2019, latest 2026-04-29 | github.com/theOehrly/Fast-F1 | SUS (no-downloads-data) | Approved — official FastF1 project, confirmed in docs |
| httpx | PyPI | Active; latest 2024-12-06 | github.com/encode/httpx | SUS (no-downloads-data) | Approved — encode organization, widely used |
| sqlalchemy | PyPI | 10+ years; latest 2.0.51 2026-06-15 | sqlalchemy.org | SUS (no-downloads-data) | Approved — SQLAlchemy Foundation, canonical Python ORM |
| apscheduler | PyPI | Active; latest 2025-12-22 | github.com/agronholm/apscheduler | SUS (no-downloads-data) | Approved — agronholm, long-standing maintainer |
| tenacity | PyPI | Active; latest 2026-02-07 | github.com/jd/tenacity | SUS (no-downloads-data) | Approved — jd (Julien Danjou), well-known Python maintainer |
| pandas | PyPI | 15+ years; latest 2026-05-11 | github.com/pandas-dev/pandas | SUS (no-downloads-data) | Approved — pandas-dev org |
| numpy | PyPI | 20+ years; latest 2026-05-18 | github.com/numpy/numpy | SUS (no-downloads-data) | Approved — NumPy org |
| lightgbm | PyPI | Active; latest 2025-02-15 | github.com/microsoft/LightGBM | SUS (no-downloads-data) | Approved — Microsoft org |
| scikit-learn | PyPI | 15+ years; latest 2026-06-02 | scikit-learn.org | SUS (no-downloads-data) | Approved — scikit-learn community |

**Packages removed due to SLOP verdict:** none

**Packages flagged as suspicious SUS:** All packages above carry SUS due to PyPI download count unavailability in the legitimacy seam — not genuine risk signals. All have authoritative GitHub/org sources and long track records. No human verification checkpoint required.

---

## Architecture Patterns

### System Architecture Diagram

```
scripts/backfill.py
       │
       ├─► FastF1Loader (year=2022)
       │       └─► fastf1.get_session() → Session.load()
       │               └─► Normalize to OpenF1 schema
       │                       └─► SQLite: raw_sessions, raw_laps,
       │                                   raw_stints, raw_pit_stops,
       │                                   raw_positions
       │
       ├─► OpenF1Client (year=2023–2025)
       │       └─► httpx.AsyncClient → GET /v1/{sessions,laps,stints,pit,position,weather}
       │               └─► [retry on 429/timeout via tenacity]
       │                       └─► SQLite: same raw tables
       │
       └─► FeatureBuilder (triggered after backfill, or standalone)
               └─► Read raw_laps + raw_stints + raw_sessions + raw_pit_stops
                       └─► Compute qualifying_delta_to_pole (from raw_laps Q session)
                       └─► Compute practice_pace_fp2/fp3 (median-normalized)
                       └─► Compute circuit_win_rate, circuit_avg_finish,
                           constructor_pace_delta (multi-season aggregation)
                       └─► Write features_prerace (one row per session+driver)
                       └─► Write features_inrace (one row per session+driver+lap)

scripts/live_poller.py  (race day only)
       │
       └─► APScheduler BackgroundScheduler
               └─► every 30s: OpenF1Client.poll_live()
                       └─► GET /v1/laps?session_key={current}&lap_number>={last_seen}
                       └─► GET /v1/intervals?session_key={current}
                       └─► GET /v1/position?session_key={current}
                       └─► GET /v1/stints?session_key={current}
                       └─► Upsert into same raw tables (WAL mode handles concurrent reads)
```

### Recommended Project Structure

```
src/
├── f1_prediction/
│   ├── db/
│   │   ├── engine.py          # create_engine() + WAL PRAGMA event listener
│   │   ├── models.py          # SQLAlchemy ORM models (all raw_* and features_* tables)
│   │   └── upsert.py          # helpers for insert-or-skip and bulk upsert
│   ├── ingest/
│   │   ├── fastf1_loader.py   # 2022 backfill: loads session → normalizes → inserts
│   │   ├── openf1_client.py   # httpx.AsyncClient wrapper for OpenF1 REST API
│   │   └── normalizer.py      # FastF1 DataFrame → OpenF1-schema dict list
│   ├── features/
│   │   ├── builder.py         # orchestrates all feature computations
│   │   ├── prerace.py         # computes features_prerace columns
│   │   └── inrace.py          # computes features_inrace columns
│   └── __init__.py
scripts/
├── backfill.py                # CLI: run backfill for all seasons; --skip-features flag
└── build_features.py          # CLI: standalone feature rebuild from raw tables
data/
├── cache/                     # FastF1 file cache (already exists)
└── f1.db                      # SQLite database (created by Phase 1)
```

### Pattern 1: FastF1 Cache and Session Loading

**What:** Enable cache before any session load; use `session.load()` selectively to avoid downloading telemetry (unneeded for this project).
**When to use:** Every call to `get_session()` in the backfill script.

```python
# Source: docs.fastf1.dev/fastf1.html
import fastf1

fastf1.Cache.enable_cache("data/cache")

session = fastf1.get_session(2022, 1, "R")  # year, round number, identifier
session.load(laps=True, telemetry=False, weather=True, messages=False)

laps_df = session.laps          # ~31-column DataFrame
weather_df = session.weather_data  # AirTemp, Rainfall (bool), TrackTemp, etc.
results_df = session.results    # DriverNumber, Position, ClassifiedPosition,
                                # GridPosition, Status, TeamName, Points, Q1-Q3
```

### Pattern 2: SQLite Engine with WAL Mode

**What:** Set `PRAGMA journal_mode=WAL` on every new connection so the live poller (writer) and future dashboard (reader) don't block each other.
**When to use:** In `db/engine.py`; applied once per process.

```python
# Source: docs.sqlalchemy.org/en/20/dialects/sqlite.html
from sqlalchemy import create_engine, event

engine = create_engine("sqlite:///data/f1.db", echo=False)

@event.listens_for(engine, "connect")
def set_wal_mode(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()
```

### Pattern 3: Bulk Insert from DataFrame

**What:** Insert a pandas DataFrame into a SQLite table via SQLAlchemy connection; chunked to avoid parameter limits.
**When to use:** After normalizing a FastF1 session or an OpenF1 API response batch.

```python
# Source: docs.sqlalchemy.org/en/20/tutorial/data_insert.html
import pandas as pd

def bulk_insert(df: pd.DataFrame, table_name: str, engine) -> None:
    with engine.connect() as conn:
        df.to_sql(
            table_name,
            conn,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=500,
        )
        conn.commit()
```

### Pattern 4: OpenF1 Async Polling with Retry

**What:** Poll an OpenF1 endpoint asynchronously with exponential backoff on 429 and timeouts.
**When to use:** All OpenF1 API calls in both backfill and live poller.

```python
# Source: tenacity docs + httpx docs
import httpx
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, retry_if_result

BASE_URL = "https://api.openf1.org/v1"

def _is_rate_limited(response: httpx.Response) -> bool:
    return response.status_code == 429

@retry(
    retry=(
        retry_if_exception_type(httpx.TimeoutException)
        | retry_if_result(_is_rate_limited)
    ),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(6),
)
async def _fetch(client: httpx.AsyncClient, endpoint: str, params: dict) -> list[dict]:
    resp = await client.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15.0)
    resp.raise_for_status()
    return resp.json()

async def fetch_laps(session_key: int, min_lap: int = 1) -> list[dict]:
    async with httpx.AsyncClient() as client:
        return await _fetch(client, "laps", {
            "session_key": session_key,
            "lap_number>": min_lap - 1,
        })
```

### Pattern 5: APScheduler BackgroundScheduler Lifecycle

**What:** Start scheduler in a background thread; stop cleanly on process exit.
**When to use:** In `scripts/live_poller.py` and eventually wired into Phase 3's FastAPI lifespan.

```python
# Source: apscheduler.readthedocs.io/en/3.x/userguide.html
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR
import logging

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

def _on_error(event):
    logger.error("Poller job failed: %s", event.exception)

scheduler.add_listener(_on_error, EVENT_JOB_ERROR)
scheduler.add_job(
    poll_once,
    "interval",
    seconds=30,
    max_instances=1,          # never run two polls concurrently
    misfire_grace_time=15,    # skip if >15s late (race paused)
    id="live_poll",
)
scheduler.start()

# On shutdown:
# scheduler.shutdown(wait=False)
```

### Pattern 6: FastF1 Sprint Weekend Detection

**What:** Check `EventFormat` to route sprint sessions as practice substitutes.
**When to use:** In `fastf1_loader.py` when iterating over 2022 events.

```python
# Source: docs.fastf1.dev/events.html
import fastf1

schedule = fastf1.get_event_schedule(2022, include_testing=False)

for _, event in schedule.iterrows():
    fmt = event["EventFormat"]
    if fmt == "conventional":
        sessions_to_load = ["FP2", "FP3", "Q", "R"]
    elif fmt in ("sprint", "sprint_shootout", "sprint_qualifying"):
        # Use Sprint session as race-pace proxy in place of FP2/FP3
        sessions_to_load = ["S", "Q", "R"]
    else:
        sessions_to_load = ["Q", "R"]  # fallback
```

### Pattern 7: Feature Builder — Qualifying Delta to Pole

**What:** Compute `qualifying_delta_to_pole` for each driver in a qualifying session from raw_laps.
**When to use:** In `features/prerace.py`.

```python
# Source: [ASSUMED] — derived from FastF1/OpenF1 schema knowledge
import pandas as pd
from sqlalchemy import text

def compute_qualifying_delta(session_key: int, conn) -> pd.DataFrame:
    q = """
        SELECT driver_number,
               MIN(lap_duration) AS best_lap
        FROM raw_laps
        WHERE session_key = :sk
          AND is_pit_out_lap = 0
          AND lap_duration IS NOT NULL
        GROUP BY driver_number
    """
    df = pd.read_sql(text(q), conn, params={"sk": session_key})
    pole_time = df["best_lap"].min()
    df["qualifying_delta_to_pole"] = df["best_lap"] - pole_time
    return df[["driver_number", "qualifying_delta_to_pole"]]
```

### Anti-Patterns to Avoid

- **Loading telemetry in backfill:** `session.load(telemetry=True)` downloads 100+ MB per session — 4 seasons × ~24 events × ~5 sessions = ~480 downloads. Always pass `telemetry=False`.
- **Synchronous httpx in APScheduler job:** APScheduler runs jobs in a thread pool; calling `asyncio.run()` inside a job creates a new event loop per call. Use `asyncio.run(fetch_laps(...))` at the top of the job function rather than nesting awaits.
- **Inserting duplicates on re-run:** Without upsert logic, a failed mid-backfill re-run inserts duplicates. Use the resumability check (D-11): query `SELECT COUNT(*) FROM raw_laps WHERE session_key = ?` before fetching. If count > 0, skip.
- **Using `pit_duration` from OpenF1 `/v1/pit`:** This field is deprecated as of OpenF1 docs. Use `lane_duration` (full pit lane transit) and `stop_duration` (stationary time, available from 2024 US GP onward).
- **Filtering FastF1 laps before storage:** D-08 locks this — store all laps raw including outlaps, inlaps, SC laps. The `IsAccurate` and `Deleted` columns enable downstream filtering without re-backfilling.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP retry on 429 | Custom loop with `time.sleep()` | tenacity `@retry` with `wait_exponential` | tenacity handles Retry-After headers, jitter, logging, max attempts in 3 lines |
| File-based HTTP cache for FastF1 | Custom cache decorator | `fastf1.Cache.enable_cache()` | FastF1's built-in cache handles session data (50–100 MB) and request-level caching via requests-cache; already installed |
| SQLite WAL mode per-connection setup | Per-query PRAGMA | SQLAlchemy `event.listens_for(engine, "connect")` | One listener fires for every new connection — set-and-forget |
| DataFrame → SQLite bulk insert | Row-by-row INSERT | `df.to_sql(..., method="multi", chunksize=500)` | pandas handles batching and type coercion; avoids SQLite parameter limit (999 per statement) |
| Weather classification logic | Complex ML | Simple rule: `rainfall > 0` for any lap in session → wet; >50% wet laps → wet session, else mixed | DATA-02 only requires wet/dry/mixed flag — deterministic rule is sufficient |

**Key insight:** The data volume (~72K rows total) is small enough that simple pandas operations outperform complex SQL aggregations in development speed while remaining fast enough at runtime.

---

## Common Pitfalls

### Pitfall 1: FastF1 Version in Venv Differs from CLAUDE.md

**What goes wrong:** CLAUDE.md documents `fastf1 3.8.3` but the installed venv has `3.8.1`. Plans that reference `3.8.3` features (if any were added) will fail silently.
**Why it happens:** The venv was set up before CLAUDE.md was written, or a patch was released after.
**How to avoid:** Use the installed version (3.8.1). The `3.8.x` line is stable; patch differences are minimal.
**Warning signs:** ImportError or AttributeError on methods referenced in docs for 3.8.3.

### Pitfall 2: FastF1 Slowness Without Telemetry=False

**What goes wrong:** A full backfill with `session.load()` defaults (all data including telemetry) downloads 50–100 MB per session and takes hours for 4 seasons.
**Why it happens:** Default `session.load()` fetches all data including car position at 10Hz.
**How to avoid:** Always call `session.load(laps=True, telemetry=False, weather=True, messages=False)` in the backfill.
**Warning signs:** Backfill taking > 5 minutes per session; large files appearing in `data/cache/`.

### Pitfall 3: OpenF1 Rate Limit (Free Tier: 3 req/s)

**What goes wrong:** A naive backfill looping 5 endpoints × ~300 sessions fires 1,500 requests; at 3 req/s that's ~8 minutes minimum but bursts will trigger 429 responses.
**Why it happens:** OpenF1 free tier is 3 req/s, 30 req/min. Looping sessions with no rate management hits the per-minute limit first.
**How to avoid:** Use tenacity exponential backoff (backs off on 429). For large batches, add a `session_key` filter to avoid fetching already-stored sessions (D-11).
**Warning signs:** HTTP 429 responses; tenacity retrying more than twice per session.

### Pitfall 4: OpenF1 Only Covers 2023+

**What goes wrong:** Using OpenF1 for 2022 data returns empty results (sessions exist in the schedule but lap data is absent).
**Why it happens:** OpenF1 officially covers 2023 onward only.
**How to avoid:** The code must route `year == 2022` to the FastF1 loader path exclusively. Assert this in the backfill orchestrator.
**Warning signs:** Empty JSON arrays from OpenF1 `/v1/laps` for 2022 session keys.

### Pitfall 5: FastF1 Schema Diverges from OpenF1 at Normalization

**What goes wrong:** FastF1's `Laps` DataFrame uses `TyreLife` (camelCase), `LapTime` (timedelta), `DriverNumber` (string), while OpenF1 uses `tyre_age_at_start` (on `/v1/stints`), `lap_duration` (float seconds), `driver_number` (int).
**Why it happens:** FastF1 applies significant post-processing and uses its own naming conventions.
**How to avoid:** The normalizer (`ingest/normalizer.py`) must handle: (1) convert `LapTime` timedelta to float seconds; (2) rename columns to snake_case; (3) drop FastF1-only columns (`IsAccurate`, `FastF1Generated`, `Deleted`, `DeletedReason`); (4) cast `DriverNumber` string → int.
**Warning signs:** Schema mismatch errors on insert; unexpected column types in `raw_laps`.

### Pitfall 6: SQLite Parameter Limit on Bulk Insert

**What goes wrong:** `df.to_sql()` without `chunksize` on large DataFrames hits SQLite's 999-parameter limit.
**Why it happens:** SQLite limits bound parameters per statement to 999; a 50-column × 25-row batch = 1,250 parameters.
**How to avoid:** Always set `chunksize=500` in `to_sql()` calls. At 15 columns, `chunksize=500` = 7,500 parameters — well within limits.
**Warning signs:** `sqlite3.OperationalError: too many SQL variables`.

### Pitfall 7: APScheduler Job Running While Race Not Active

**What goes wrong:** Live poller runs 24/7 and makes futile OpenF1 requests during non-race periods.
**Why it happens:** Scheduler has no awareness of the race calendar.
**How to avoid:** The poll job should first check `/v1/sessions` to determine if a race session is currently within its active window (`date_start - 30min` to `date_end + 30min`). If not, return early. This also gracefully handles the free-tier live data restriction (30-min window).
**Warning signs:** 404 or empty responses from OpenF1 during non-race hours.

### Pitfall 8: features_inrace `laps_remaining` Requires Total Laps

**What goes wrong:** `laps_remaining = total_laps - lap_number` cannot be computed per-lap from raw data alone — the total laps for the session must be looked up.
**Why it happens:** OpenF1 doesn't return total scheduled laps in its lap endpoint.
**How to avoid:** Store total laps in `raw_sessions` (from FastF1's `session.event` metadata or from the max `lap_number` at race end). For in-progress races, use the scheduled lap count from the circuit; for completed races, use `MAX(lap_number)` from `raw_laps`.
**Warning signs:** `NULL` values in `features_inrace.laps_remaining`.

---

## Code Examples

### FastF1: Full 2022 Session Backfill Pattern

```python
# Source: docs.fastf1.dev/fastf1.html + docs.fastf1.dev/events.html
import fastf1
import pandas as pd

fastf1.Cache.enable_cache("data/cache")

schedule = fastf1.get_event_schedule(2022, include_testing=False)

for _, event in schedule.iterrows():
    fmt = event["EventFormat"]
    identifiers = (
        ["FP2", "FP3", "Q", "R"]
        if fmt == "conventional"
        else ["S", "Q", "R"]   # sprint weekends
    )
    for identifier in identifiers:
        try:
            sess = fastf1.get_session(2022, event["RoundNumber"], identifier)
            sess.load(laps=True, telemetry=False, weather=True, messages=False)
        except Exception:
            continue  # session may not exist (e.g., no FP3 on sprint weekends)
        # normalize and insert sess.laps, sess.results, sess.weather_data
```

### OpenF1: Fetch All Laps for a Session

```python
# Source: openf1.org/docs
import asyncio, httpx

async def fetch_session_laps(session_key: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            "https://api.openf1.org/v1/laps",
            params={"session_key": session_key},
        )
        resp.raise_for_status()
        return resp.json()
```

### SQLAlchemy: WAL Engine + Model Definition

```python
# Source: docs.sqlalchemy.org/en/20/dialects/sqlite.html
from sqlalchemy import create_engine, event, Column, Integer, Float, String, Boolean
from sqlalchemy.orm import DeclarativeBase

engine = create_engine("sqlite:///data/f1.db")

@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()

class Base(DeclarativeBase):
    pass

class RawLap(Base):
    __tablename__ = "raw_laps"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_key = Column(Integer, nullable=False, index=True)
    driver_number = Column(Integer, nullable=False)
    lap_number = Column(Integer, nullable=False)
    lap_duration = Column(Float)
    is_pit_out_lap = Column(Boolean)
    duration_sector_1 = Column(Float)
    duration_sector_2 = Column(Float)
    duration_sector_3 = Column(Float)
    i1_speed = Column(Integer)
    i2_speed = Column(Integer)
    st_speed = Column(Integer)
    date_start = Column(String)
    # ... remaining OpenF1 laps fields

Base.metadata.create_all(engine)
```

### Feature Builder: Practice Pace (FP2/FP3)

```python
# Source: [ASSUMED] — query pattern derived from raw_laps schema
import pandas as pd
from sqlalchemy import text

def compute_practice_pace(session_key: int, conn) -> pd.DataFrame:
    """
    Returns practice_pace for each driver: driver's median clean lap
    as offset from field median. Negative = faster than median.
    Excludes pit-out laps and laps > 107% of session median.
    """
    q = """
        SELECT driver_number, lap_duration
        FROM raw_laps
        WHERE session_key = :sk
          AND is_pit_out_lap = 0
          AND lap_duration IS NOT NULL
    """
    df = pd.read_sql(text(q), conn, params={"sk": session_key})
    field_median = df["lap_duration"].median()
    # 107% rule: exclude laps more than 7% slower than session median
    df = df[df["lap_duration"] <= field_median * 1.07]
    driver_medians = df.groupby("driver_number")["lap_duration"].median()
    return (driver_medians - field_median).rename("practice_pace").reset_index()
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Ergast API for historical data | Jolpica-f1 via FastF1 | Ergast shut down 2024 | Use FastF1 which wraps jolpica automatically; never call Ergast or jolpica directly |
| OpenF1 `pit_duration` field | `lane_duration` + `stop_duration` | 2024 US GP | `pit_duration` deprecated; use `lane_duration` for pit lane time; `stop_duration` for stationary time |
| SQLAlchemy 1.x `Session.query()` | SQLAlchemy 2.0 `session.execute(select(Model))` | 2023 | 2.0-style queries; `Session.query()` is legacy in 2.0 |

**Deprecated/outdated:**
- Ergast API: shut down 2024; use FastF1 (wraps jolpica-f1 automatically).
- OpenF1 `pit_duration` field: deprecated; use `lane_duration`.
- FastF1 `fastf1.api` module: marked private/deprecated; use high-level `get_session()` API only.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `qualifying_delta_to_pole` can be computed from `raw_laps` using `MIN(lap_duration)` per driver in a Q session | Code Examples — Feature Builder | Qualifying rounds (Q1/Q2/Q3) add complexity: `MIN(lap_duration)` may need to restrict to Q3 for drivers who made it, else Q2/Q1. FastF1 `session.results` `Q1`/`Q2`/`Q3` columns may be cleaner source. |
| A2 | `driver_seasons_in_f1` can be computed by counting distinct seasons in which `driver_number` appears in `raw_sessions` | User Constraints D-02 | If a driver has a gap year, the count would be lower than actual F1 career years. For predictive purposes this approximation is acceptable. |
| A3 | OpenF1 `/v1/sessions` `date_start`/`date_end` can be used to detect an active live session (within the 30-min window) | Architecture diagram, Pitfall 7 | OpenF1 session times are in UTC; local time zone conversion required. If times are inaccurate, the live window check may miss sessions. |
| A4 | `championship_position` feature can be computed from cumulative points in `raw_sessions.results` across the season up to the current race | User Constraints D-02 | Standings are not stored in raw tables currently. Either compute from `raw_sessions.finish_position + points` or accept that this requires a separate data fetch (FastF1 `session.results.Points`). |

**If this table is empty:** It isn't — four assumptions are logged above for planner confirmation.

---

## Open Questions

1. **Where does `championship_position` come from?**
   - What we know: D-02 requires it as a feature in `features_prerace`. FastF1 `session.results` has a `Points` column for each race result.
   - What's unclear: Should `championship_position` be stored as a raw column in `raw_sessions.results`, or computed by the feature builder from cumulative points? The current `raw_sessions` table definition doesn't mention a standings column.
   - Recommendation: Store `points_to_date` in `raw_sessions` from FastF1 results; feature builder derives `championship_position` by ranking drivers on `points_to_date` at the race's round number.

2. **`features_inrace.interval_ahead` / `interval_behind` during live race**
   - What we know: OpenF1 `/v1/intervals` returns `interval` (gap to car ahead) and `gap_to_leader`. There is no `interval_behind`.
   - What's unclear: Computing `interval_behind` requires knowing which car is behind — requires a join to `/v1/position`. The CONTEXT.md D-03 lists both as required columns.
   - Recommendation: Derive `interval_behind` = the `interval` of the car whose current `position` = this driver's `position` + 1. This requires a per-lap position join in the feature builder.

3. **Total laps per race session for `laps_remaining`**
   - What we know: `laps_remaining = total_laps - current_lap`. OpenF1 does not expose total scheduled laps.
   - What's unclear: How to get total laps for each circuit reliably.
   - Recommendation: Store `total_laps` in `raw_sessions` populated from FastF1's `session.event` metadata for 2022, and from the `MAX(lap_number)` observed at race completion for 2023–2025. During live races, use a hard-coded circuit→total_laps lookup table (small; ~24 circuits).

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| Python 3.11 | All code | Yes | 3.11.x at `/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11` | — |
| fastf1 | 2022 backfill | Yes (in .venv) | 3.8.1 | — |
| pandas | Feature engineering | Yes (in .venv) | 2.3.3 | — |
| numpy | Numerical ops | Yes (in .venv) | 2.4.3 | — |
| apscheduler | Live poller | Yes (in .venv) | 3.11.2 | — |
| lightgbm | Phase 2 (not needed in Phase 1) | Yes (in .venv) | 4.6.0 | — |
| scikit-learn | Phase 2 (not needed in Phase 1) | Yes (in .venv) | 1.8.0 | — |
| httpx | OpenF1 polling | Not installed | — | Install: `pip install httpx==0.28.1` |
| sqlalchemy | ORM / schema | Not installed | — | Install: `pip install sqlalchemy==2.0.51` |
| tenacity | Retry logic | Not installed | — | Install: `pip install tenacity==9.1.4` |
| data/cache/ | FastF1 cache dir | Yes (directory exists) | — | — |
| Internet access | OpenF1 + FastF1 APIs | Assumed yes | — | Offline: use existing FastF1 cache only |

**Missing dependencies with no fallback:**
- httpx, sqlalchemy, tenacity — must be installed before Phase 1 execution.

**Note on venv structure:** The `.venv` directory has `lib/python3.11/site-packages/` populated but no `bin/` executables (no `python3.11` symlink, no `pip`). This is an unusual venv setup. Wave 0 must verify that packages are importable and document the correct activation path.

---

## Security Domain

> `security_enforcement: true`, ASVS level 1.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | No auth in Phase 1 (local-only, no user accounts) |
| V3 Session Management | No | No user sessions in Phase 1 |
| V4 Access Control | No | Local tool; single user |
| V5 Input Validation | Partial | OpenF1 JSON responses should be validated before insertion; pandas `dtype` checking on ingest |
| V6 Cryptography | No | No secrets or credentials in Phase 1 (OpenF1 is public, no API key) |
| V7 Error Handling | Yes | Poller must not crash on bad data; use try/except with logging |
| V9 Communications | Partial | All OpenF1 calls use HTTPS (enforced by URL scheme); no additional config needed |

### Known Threat Patterns for This Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed JSON from OpenF1 | Tampering | Wrap `resp.json()` in try/except; validate expected keys before insert |
| SQL injection via unsanitized session_key | Tampering | Use SQLAlchemy parameterized queries (`text()` with `params` dict or ORM `where()`) — never f-string SQL |
| Path traversal in cache_dir | Tampering | `fastf1.Cache.enable_cache()` accepts a path — validate it is inside the project directory |
| Unbounded retry loop | Denial of Service (self) | tenacity `stop_after_attempt(6)` caps retries; always set `max_instances=1` in APScheduler |

**Phase 1 security posture:** Low risk. No external users, no secrets, public API. The primary concern is data integrity (malformed API responses poisoning the feature tables) rather than access control.

---

## Sources

### Primary (MEDIUM confidence — Context7 / official docs)
- [docs.fastf1.dev/fastf1.html](https://docs.fastf1.dev/fastf1.html) — Cache.enable_cache(), get_session() signatures
- [docs.fastf1.dev/events.html](https://docs.fastf1.dev/events.html) — Session identifiers, EventFormat for sprint detection
- [docs.fastf1.dev/core.html](https://docs.fastf1.dev/core.html) — Laps DataFrame columns, weather DataFrame columns
- [docs.fastf1.dev/api_reference/api_autogen/fastf1.core.SessionResults.html](https://docs.fastf1.dev/api_reference/api_autogen/fastf1.core.SessionResults.html) — SessionResults column list
- [openf1.org/docs](https://openf1.org/docs) — All 7 endpoint field tables (laps, stints, pit, intervals, position, weather, sessions), rate limits
- [docs.sqlalchemy.org/en/20/dialects/sqlite.html](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html) — WAL PRAGMA event listener pattern, connection string format
- [apscheduler.readthedocs.io/en/3.x/userguide.html](https://apscheduler.readthedocs.io/en/3.x/userguide.html) — BackgroundScheduler setup, add_job, event listeners

### Secondary (LOW confidence — web search)
- PyPI index — httpx 0.28.1, sqlalchemy 2.0.51, tenacity 9.1.4 versions verified
- Venv dist-info directories — installed package versions verified directly from filesystem

---

## Metadata

**Confidence breakdown:**
- Standard stack: MEDIUM — versions verified from PyPI and dist-info; API patterns from official docs
- Architecture: MEDIUM — derived from official FastF1 and OpenF1 docs; pipeline structure is [ASSUMED]
- Pitfalls: MEDIUM — FastF1/OpenF1 known-issue patterns from official docs; some from [ASSUMED] experience
- Feature engineering patterns: LOW — SQL queries are [ASSUMED] based on schema knowledge

**Research date:** 2026-06-16
**Valid until:** 2026-07-16 (OpenF1 API changes rarely; FastF1 3.8.x is stable)
