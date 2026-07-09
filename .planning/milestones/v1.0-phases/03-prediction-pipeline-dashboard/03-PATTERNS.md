# Phase 3: Prediction Pipeline & Dashboard - Pattern Map

**Mapped:** 2026-07-07
**Files analyzed:** 11 (new/modified)
**Analogs found:** 10 / 11

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/f1_prediction/pipeline.py` | service (entrypoint/orchestrator) | event-driven (poll → inference → write) | `src/f1_prediction/live/poller.py` | role-match |
| `src/f1_prediction/inference/features.py` | utility (shared constants/functions) | transform | `scripts/train.py` (lines 52-76, 238-282, 416-493) | exact (extraction, not new pattern) |
| `src/f1_prediction/inference/prerace.py` | service | CRUD (compute + insert) | `src/f1_prediction/features/builder.py::build_features_for_session` (prerace branch) | role-match |
| `src/f1_prediction/inference/inrace.py` | service | CRUD (compute + insert per lap) | `src/f1_prediction/features/builder.py::build_features_for_session` (inrace branch) | role-match |
| `src/f1_prediction/db/models.py` (add `Prediction`, `RawDriver`) | model | CRUD | `FeaturesInrace`/`RawStint` classes in same file | exact |
| `src/f1_prediction/db/upsert.py` (reuse) | utility | batch/CRUD | `bulk_insert_rows()` (existing) | exact — reuse, no new code needed |
| `src/f1_prediction/ingest/openf1_client.py` (add `drivers()`) | service (HTTP client method) | request-response | `OpenF1Client.stints()`/`fetch_stints()` (lines 144-146, 185-190) | exact |
| `src/f1_prediction/dashboard.py` | component (Streamlit entrypoint) | request-response (read + render) | none (greenfield) — closest process-level analog is `pipeline.py` for read-only DB access style | no close analog (new domain: Streamlit UI) |
| `src/f1_prediction/dashboard_lib/queries.py` | utility (read-only SQL) | CRUD (read-only) | `db/upsert.py` for SQL/text() param conventions; `features/builder.py` for `engine.connect()` read pattern | role-match |
| `src/f1_prediction/dashboard_lib/charts.py` | component (chart builder) | transform | none — new domain (Plotly `go.Bar`); no existing charting code in repo | no analog |
| `scripts/replay_race.py` | utility/script (dev tool) | batch (timed re-insert) | `src/f1_prediction/live/poller.py` (insert helpers) + `scripts/backfill.py` (CLI/main structure, resumability checks) | role-match |
| `pyproject.toml` (dependency additions) | config | — | existing `[project.dependencies]` block | exact |

## Pattern Assignments

### `src/f1_prediction/ingest/openf1_client.py` — add `drivers()` method (service, request-response)

**Analog:** same file, `stints()` + `fetch_stints()` (lines 144-146, 185-190)

**Core pattern to copy** (`stints` method, lines 144-146):
```python
async def stints(self, session_key: int) -> list[dict]:
    """Fetch stints from /v1/stints for a session."""
    return await self._fetch("stints", {"session_key": session_key})
```
Copy exactly this shape for `drivers()`:
```python
async def drivers(self, session_key: int) -> list[dict]:
    """Fetch driver metadata from /v1/drivers for a session."""
    return await self._fetch("drivers", {"session_key": session_key})
```

**Sync convenience wrapper pattern** (`fetch_stints`, lines 185-190):
```python
def fetch_stints(session_key: int) -> list[dict]:
    """Fetch stints synchronously. Opens a fresh client per call."""
    async def _run():
        async with OpenF1Client() as c:
            return await c.stints(session_key=session_key)
    return asyncio.run(_run())
```
Mirror this as `fetch_drivers(session_key)`. Add both `"drivers"` and `"fetch_drivers"` to the `__all__` list (lines 36-45).

**Retry/error handling** — already centralized in `_fetch()` (lines 91-126); no new error handling needed, the new method inherits it automatically.

---

### `src/f1_prediction/db/models.py` — add `Prediction` and `RawDriver` ORM classes (model, CRUD)

**Analog:** `RawStint` (lines 139-168) for a simple per-session/driver table; `FeaturesInrace` (lines 317-365) for composite-PK + `CheckConstraint`/`Index` style.

**Imports** — already present at top of file (lines 24-35): `Boolean, CheckConstraint, Column, Float, Index, Integer, PrimaryKeyConstraint, String, UniqueConstraint` from `sqlalchemy`.

**Pattern to copy** (docstring + Column style + `__table_args__`, from `RawWeather`, lines 231-262, which already has a `CheckConstraint` on a string enum matching D-10's `prediction_type IN ('prerace','inrace')` requirement):
```python
class RawWeather(Base):
    __tablename__ = "raw_weather"
    session_key = Column(Integer, primary_key=True)
    wet_dry_mixed = Column(String, nullable=False)
    ...
    __table_args__ = (
        CheckConstraint(
            "wet_dry_mixed IN ('wet', 'dry', 'mixed')",
            name="ck_raw_weather_wet_dry_mixed",
        ),
    )
```
The `Prediction` model in RESEARCH.md's Code Examples section (lines 384-402 of 03-RESEARCH.md) already follows this exact style — use it verbatim as the starting point:
```python
class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_key = Column(Integer, nullable=False, index=True)
    driver_number = Column(Integer, nullable=False)
    win_probability = Column(Float, nullable=False)
    prediction_type = Column(String, nullable=False)
    lap_number = Column(Integer, nullable=True)
    computed_at = Column(String, nullable=False)
    __table_args__ = (
        CheckConstraint("prediction_type IN ('prerace', 'inrace')", name="ck_predictions_prediction_type"),
        Index("ix_predictions_session_type_lap", "session_key", "prediction_type", "lap_number"),
    )
```

For `RawDriver`, follow `RawStint`'s shape (per-session lookup, composite index on session_key+driver_number, lines 139-168):
```python
class RawDriver(Base):
    __tablename__ = "raw_drivers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_key = Column(Integer, nullable=False)
    driver_number = Column(Integer, nullable=False)
    full_name = Column(String)
    name_acronym = Column(String)
    team_name = Column(String)
    team_colour = Column(String)   # bare hex, no '#' prefix — store as OpenF1 returns it (Pitfall 3)
    __table_args__ = (
        Index("ix_raw_drivers_session_driver", "session_key", "driver_number"),
    )
```

---

### `src/f1_prediction/pipeline.py` (NEW — entrypoint, service, event-driven)

**Analog:** `src/f1_prediction/live/poller.py` — reuse the whitelist-extraction, `text()`+bound-param, and BackgroundScheduler patterns wholesale.

**Imports pattern** (poller.py lines 21-36):
```python
from __future__ import annotations
import asyncio
import logging
from typing import Callable, Optional
from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_STOPPED
from sqlalchemy import text
from sqlalchemy.engine import Engine
from f1_prediction.db.engine import get_engine
from f1_prediction.db.upsert import bulk_insert_rows
```

**Whitelist-extraction pattern for new `/v1/drivers` fields** (poller.py lines 40-66):
```python
_DRIVER_FIELDS = [
    "session_key", "driver_number", "full_name", "name_acronym",
    "team_name", "team_colour",
]

def _extract(record: dict, fields: list[str]) -> dict:
    return {f: record.get(f) for f in fields}
```
(This exact `_extract` helper already exists in `poller.py` — either import/reuse it or duplicate the one-liner; the whitelist constant `_DRIVER_FIELDS` is new.)

**Hook point for "run inference after new laps" (D-09 wiring)** — `poll_once()`'s return dict (poller.py lines 194-203) already exposes `laps_inserted`:
```python
result = {
    "session_key": session_key,
    "skipped": False,
    "laps_inserted": laps_inserted,
    ...
}
```
`pipeline.py` should call `poll_once(session_key, engine=engine)`, check `result["laps_inserted"] > 0`, then trigger `build_features_for_session(session_key, engine=engine, kind="inrace")` + inference (Pattern 1 in RESEARCH.md — do not hand-roll incremental feature computation).

**Scheduler lifecycle pattern to reuse/extend** (poller.py lines 351-425, `start_poller`/`stop_poller`) — copy the `BackgroundScheduler` + `add_listener(EVENT_JOB_ERROR)` + `max_instances=1, misfire_grace_time=15` setup verbatim; `pipeline.py`'s job function should call the new post-poll inference hook after (or instead of directly calling) `poll_once`.

**Idempotent startup check (D-11) — analog:** `scripts/backfill.py::fix_round_numbers` (lines 122-179) and `db/upsert.py::session_exists` (lines 57-76) both demonstrate the "check DB state before acting" resumability convention:
```python
# db/upsert.py pattern (lines 73-76), adapt for prerace idempotency check
check_sql = text("SELECT 1 FROM raw_sessions WHERE session_key = :sk")
with engine.connect() as conn:
    row = conn.execute(check_sql, {"sk": session_key}).fetchone()
return row is not None
```
Adapt to: `SELECT 1 FROM predictions WHERE session_key = :sk AND prediction_type = 'prerace' LIMIT 1` — skip prerace computation if a row already exists.

**Session lookup (D-01)** — use the query given verbatim in RESEARCH.md Code Examples (lines 362-378 of 03-RESEARCH.md), which already follows the `db/engine.py` `get_engine()` + `text()` conventions:
```python
def get_latest_race_session_key(engine) -> int | None:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT session_key FROM raw_sessions "
            "WHERE session_type = 'Race' "
            "ORDER BY year DESC, round_number DESC, session_key DESC "
            "LIMIT 1"
        )).fetchone()
    return int(row[0]) if row else None
```

---

### `src/f1_prediction/inference/features.py` (NEW — extracted shared module)

**Analog:** `scripts/train.py` lines 52-76 (constants), 238-282 (`encode_tyre_compound`/`build_inrace_feature_matrix`), 434-493 (`predict_with_softmax`/`predict_inrace_with_softmax`).

**Move these verbatim** (do not reimplement — Pitfall 4/5 in RESEARCH.md flags column-order drift risk):
```python
PRERACE_FEATURE_COLS = [
    "qualifying_delta_to_pole", "practice_pace_fp2", "practice_pace_fp3",
    "circuit_win_rate", "championship_position", "grid_position",
    "driver_seasons_in_f1",
]
INRACE_NUMERIC_FEATURE_COLS = [
    "gap_to_leader", "tyre_age_laps", "pit_stop_count", "position",
    "interval_ahead", "interval_behind", "pit_stop_delta", "lap_number",
    "laps_remaining",
]
TYRE_COMPOUND_VALUES = ["HARD", "INTERMEDIATE", "MEDIUM", "SOFT", "WET"]
```
Plus `encode_tyre_compound(df)`, `build_inrace_feature_matrix(df)`, `predict_with_softmax(model, df, feature_cols)`, `predict_inrace_with_softmax(model, df)` — copy function bodies exactly as shown in RESEARCH.md's Pattern 2 excerpt (lines 234-244 of 03-RESEARCH.md) and as read directly from `scripts/train.py` lines 238-493. After extraction, `scripts/train.py` should `import` from this new module instead of defining these locally (avoids the drift Pitfall 4 warns about).

**Model loading pattern** — `save_model()` (train.py lines 416-427) uses `joblib.dump`; the inverse for inference:
```python
import joblib
model = joblib.load(path)   # e.g. data/models/prerace_model.joblib
```

---

### `src/f1_prediction/inference/prerace.py` / `inrace.py` (NEW — service, CRUD)

**Analog:** `src/f1_prediction/features/builder.py::build_features_for_session` (lines 156-333) for the DELETE-then-bulk-INSERT pattern, and `db/upsert.py::bulk_insert_rows` (lines 79-113) for the actual insert mechanics.

**Core pattern** (builder.py lines 205-219, DELETE before re-insert for clean re-runs):
```python
conn.execute(
    text("DELETE FROM predictions WHERE session_key = :sk AND prediction_type = :pt"),
    {"sk": session_key, "pt": "inrace"},
)
conn.commit()
```
Note: D-11 makes prerace idempotent via **skip-if-exists**, not delete-then-reinsert (different from `build_features_for_session`'s pattern) — check existence first per the pipeline.py idempotency pattern above, only DELETE+INSERT for `inrace` rows (new row per lap, no dedup needed since `lap_number` differentiates them — or follow builder.py's per-session DELETE if a full-session prediction re-write is easier).

**Bulk insert pattern** (`db/upsert.py::bulk_insert_rows`, lines 79-113) — reuse directly, do not reimplement:
```python
def bulk_insert_rows(engine: Engine, table_name: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    with engine.connect() as conn:
        df.to_sql(table_name, conn, if_exists="append", index=False, method="multi", chunksize=500)
        conn.commit()
    return len(rows)
```
Call as `bulk_insert_rows(engine, "predictions", prediction_rows)`.

**Fresh-connection-per-write pattern (WR-02)** — builder.py lines 309-317 explicitly closes the read connection before opening a separate one for the bulk insert, to avoid SQLite "database is locked" under WAL. Follow the same two-connection split in `inference/prerace.py`/`inrace.py`.

---

### `src/f1_prediction/dashboard.py` + `dashboard_lib/queries.py` (NEW — component + utility, request-response, read-only)

**No close in-repo analog** (greenfield Streamlit domain) — use RESEARCH.md's Pattern 3/4 code verbatim as the canonical starting point (already vetted against this codebase's `get_engine()`):

**Engine caching + fresh-connection-per-read** (RESEARCH.md Pattern 4, lines 274-285):
```python
import streamlit as st
from f1_prediction.db.engine import get_engine

@st.cache_resource
def _engine():
    return get_engine()

def load_inrace_predictions(session_key: int) -> pd.DataFrame:
    with _engine().connect() as conn:
        return pd.read_sql(text("SELECT ... FROM predictions WHERE ..."), conn, params={"sk": session_key})
```
This directly reuses `db/engine.py::get_engine()` (WAL mode already configured there, lines 30-51) — do not create a second engine factory.

**Fragment-scoped auto-refresh** (RESEARCH.md Pattern 3, lines 250-267):
```python
tab_prerace, tab_inrace = st.tabs(["Pre-race", "In-race"])
with tab_prerace:
    render_prerace_tab()
with tab_inrace:
    @st.fragment(run_every=15)
    def render_inrace_tab():
        df = load_inrace_predictions()
        st.plotly_chart(build_win_prob_bar_chart(df), use_container_width=True)
        st.dataframe(build_side_table(df))
    render_inrace_tab()
```

**SQL param convention** — even in read-only dashboard code, continue the project-wide `text()` + bound `:param` convention already used in `db/upsert.py`/`live/poller.py`/`features/builder.py`. No f-string SQL, ever, per T-0X-05.

---

### `src/f1_prediction/dashboard_lib/charts.py` (NEW — no analog)

No existing charting code in the repo. Build `build_win_prob_bar_chart(df)` using `plotly.graph_objects.Bar(orientation="h", marker_color=[...])` per RESEARCH.md's stack recommendation. Apply Pitfall 3's `#` prefix fix when mapping `team_colour` → `marker_color`:
```python
marker_color = [f"#{c}" for c in df["team_colour"]]
```

---

### `scripts/replay_race.py` (NEW — utility script, batch)

**Analog:** `scripts/backfill.py` for CLI/`argparse` + `main()` structure (lines 181+) and `src/f1_prediction/live/poller.py`'s insert helpers (`_fetch_and_insert_laps` etc., lines 210-276) for the target-table insert shape — but replay reads from `raw_laps`/`raw_positions`/`raw_stints`/`raw_pit_stops` (already-backfilled historical data) rather than the OpenF1 API, and re-inserts on a timer via `time.sleep(interval)` in a loop (not APScheduler — this is a one-shot dev script, not a persistent service).

**Reuse directly:** `bulk_insert_rows()` from `db/upsert.py` for the re-insert step; `get_engine()` from `db/engine.py` for DB access; `text()` + bound params for the SELECT that reads the source historical race's rows.

**Argparse pattern to copy** — `scripts/train.py` lines 500-515 (`argparse.ArgumentParser` + `ArgumentDefaultsHelpFormatter` + `add_argument` with `choices=`) is the closest CLI-flag style example for the `--interval` flag (D-03).

## Shared Patterns

### SQL parameterization (no f-string SQL)
**Source:** `src/f1_prediction/db/upsert.py` (module docstring, lines 8-9), enforced throughout `live/poller.py`, `features/builder.py`
**Apply to:** All new files touching SQL — `pipeline.py`, `inference/prerace.py`, `inference/inrace.py`, `dashboard_lib/queries.py`, `scripts/replay_race.py`
```python
conn.execute(text("SELECT ... WHERE session_key = :sk"), {"sk": session_key})
```

### Whitelist field extraction from OpenF1 responses
**Source:** `src/f1_prediction/live/poller.py` lines 40-66 (`_LAP_FIELDS`, `_extract()`)
**Apply to:** New `drivers()` client method's whitelist (`_DRIVER_FIELDS`) and any insert code touching `raw_drivers`

### Engine access via `get_engine()`
**Source:** `src/f1_prediction/db/engine.py::get_engine()` (WAL mode pre-configured)
**Apply to:** All new files (`pipeline.py`, `dashboard.py`, `inference/*.py`, `scripts/replay_race.py`) — never construct a second engine/connection factory

### Bulk insert via `bulk_insert_rows()`
**Source:** `src/f1_prediction/db/upsert.py` lines 79-113
**Apply to:** `inference/prerace.py`, `inference/inrace.py` (predictions writes), `scripts/replay_race.py` (raw_* re-inserts) — reuse the existing function, do not reimplement chunked pandas `to_sql`

### Two-connection split for read-then-write (WR-02 pattern)
**Source:** `src/f1_prediction/features/builder.py` lines 309-317
**Apply to:** `inference/prerace.py`, `inference/inrace.py` — read feature rows in one `engine.connect()` block, close it, then open a fresh connection for the bulk insert to avoid SQLite lock contention under WAL

### Idempotent/resumable startup checks
**Source:** `db/upsert.py::session_exists` (lines 57-76), `scripts/backfill.py::fix_round_numbers` (idempotent re-run docstring, lines 122-179)
**Apply to:** `pipeline.py`'s D-11 prerace-skip-if-exists check

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `src/f1_prediction/dashboard.py` | component | request-response | Greenfield — first Streamlit UI file in the repo; use RESEARCH.md Patterns 3/4 (official Streamlit docs-derived) as the canonical reference instead of an in-repo analog |
| `src/f1_prediction/dashboard_lib/charts.py` | component | transform | No existing charting/Plotly code anywhere in the codebase; use Plotly's `go.Bar(orientation="h")` API directly per RESEARCH.md Standard Stack |
| `.streamlit/config.toml` (dark theme, per 03-UI-SPEC.md) | config | — | No existing Streamlit config in repo; follow 03-UI-SPEC.md's `[theme] base = "dark"` contract directly, no analog needed (trivial TOML) |

## Metadata

**Analog search scope:** `src/f1_prediction/` (all subpackages), `scripts/` (train.py, backfill.py, build_features.py)
**Files scanned:** `openf1_client.py`, `live/poller.py`, `db/models.py`, `db/upsert.py`, `db/engine.py`, `features/builder.py`, `scripts/train.py`, `scripts/backfill.py`
**Pattern extraction date:** 2026-07-07
</content>
