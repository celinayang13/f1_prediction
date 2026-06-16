# Architecture Research — F1 Race Winner Prediction

**Project:** F1 Race Winner Prediction
**Researched:** 2026-06-16
**Confidence:** HIGH for structure; MEDIUM for live data latency specifics (OpenF1 live tier details are partially behind a paywall)

---

## System Components

The system has five distinct components. They are loosely coupled — each can be built and tested independently before being wired together.

| Component | Responsibility | Tech |
|-----------|---------------|------|
| **Data Poller** | Fetches OpenF1 API on a schedule, writes raw records to SQLite | Python, APScheduler, requests/httpx |
| **SQLite Store** | Persistent storage for all raw lap/session data and computed features | SQLite (two logical DBs or one with separate table groups) |
| **Feature Builder** | Reads raw data, computes per-driver per-lap feature vectors, writes feature table | Python, pandas |
| **Model Layer** | Trains offline on historical data; serves live win-probability predictions | scikit-learn / XGBoost, joblib |
| **Dashboard** | Reads latest predictions from DB, renders ranked probability chart, auto-refreshes | Streamlit + streamlit-autorefresh |

The poller, feature builder, and model inference chain run in one Python process (or as coordinated threads). The dashboard runs as a separate Streamlit process reading the same SQLite file. There is no message queue or microservice boundary needed for local deployment.

---

## Data Flow

```
OpenF1 REST API
      |
      | HTTP GET (every ~30s during race, ~5 min historical)
      v
[Data Poller]  ──── incremental fetch using date_gt filter
      |
      | INSERT raw rows
      v
[SQLite: raw_* tables]
  raw_laps, raw_positions, raw_intervals,
  raw_pit_stops, raw_stints, raw_sessions,
  raw_car_data (optional, high-volume)
      |
      | triggered after each poll batch
      v
[Feature Builder]
  - joins raw tables by session_key + driver_number + lap_number
  - computes: qualifying_gap, rolling_pace_3lap, pit_undercut_delta,
    tyre_age, position_delta, gap_to_leader, constructor_standing_pts,
    championship_standing_pts, circuit_history_win_rate
      |
      | UPSERT feature rows
      v
[SQLite: features table]
  one row per (session_key, driver_number, lap_number)
      |
      | read latest lap's feature vectors for all ~20 drivers
      v
[Model Inference]
  - load trained model from disk (joblib)
  - predict_proba() over all drivers
  - normalize to sum = 1.0
      |
      | UPSERT prediction rows
      v
[SQLite: predictions table]
  columns: session_key, lap_number, driver_number,
           win_probability, updated_at
      |
      | SELECT * WHERE lap_number = max(lap_number)
      v
[Streamlit Dashboard]
  - streamlit-autorefresh triggers every 30s
  - reads predictions table
  - renders horizontal bar chart ranked by win_probability
  - shows delta from previous lap
```

**Key design decisions in the flow:**

1. Raw data and predictions live in the same SQLite file. This avoids any inter-process socket. Streamlit reads a file; the poller writes a file. WAL mode (`PRAGMA journal_mode=WAL`) allows concurrent reads and writes without locking conflicts.

2. The Feature Builder runs synchronously after each poll batch. It does not run on a separate schedule. This keeps the pipeline deterministic: poll → build features → infer → predictions updated.

3. Inference is not a long-running server. It is a function call that takes the current feature matrix, runs `predict_proba`, and writes results. At ~20 drivers per lap this completes in milliseconds. No model server (MLflow, Triton, etc.) is needed.

---

## Live Update Mechanism

During a race, OpenF1 publishes lap completion data approximately 3–5 seconds after the lap occurs. The live tier (paid, €9.90/month) is required for real-time access; the free tier treats the current session window as "live" but may have higher latency or restrictions.

**Polling strategy for race day:**

```
Poll interval: 30 seconds
Filter: date_gt=<timestamp of last successful fetch>
Endpoints polled each cycle (in order):
  1. /sessions?session_key=latest          — confirm session active
  2. /laps?session_key=latest&date_gt=...  — new lap completions
  3. /position?session_key=latest&date_gt=...
  4. /intervals?session_key=latest&date_gt=...
  5. /pit?session_key=latest&date_gt=...
  6. /stints?session_key=latest&date_gt=...

Total requests per cycle: 6
Rate limit: 30 req/min (free), 60 req/min (sponsor)
Headroom: 6 req per 30s = 12 req/min — well within free tier
```

**Lap detection logic:**

The poller tracks `max_lap_number` in the raw_laps table per session. After each poll, if `max_lap_number` has increased, it triggers a Feature Builder + Inference pass. If no new laps arrived (e.g., safety car bunching), no inference runs. This avoids redundant predictions with stale data.

**Pre-race baseline:**

Before race start, qualifying session data is used to compute the initial win probability distribution. The qualifying session is fetched once (historical) and loaded into the feature table with `lap_number = 0` as the pre-race snapshot.

---

## Build Order

Dependencies flow strictly downward. Nothing in a later phase can be built without the earlier phase being complete.

```
Phase 1: Data Layer
  - SQLite schema (raw tables)
  - OpenF1 client (session, laps, position, intervals, pit, stints)
  - Poller loop with APScheduler (historical backfill: 2022–2025)
  - Incremental fetch with date_gt filter
  GATE: raw_laps has ~4 seasons of data, queryable

Phase 2: Feature Engineering
  - Feature Builder reading raw tables
  - Feature table schema
  - Feature computation: qualifying gap, rolling pace, tyre age,
    gap to leader, position delta, constructor pts, circuit history
  GATE: features table populated for all historical sessions

Phase 3: Model Training
  - Target label definition: is_winner (binary) per race
  - Train/val split by season (not random — time-based)
  - XGBoost classifier (or LightGBM) with cross-validation
  - Model serialized with joblib
  - Calibration check: win probabilities across 20 drivers sum to ~1
  GATE: model achieves reasonable baseline on held-out 2024 season

Phase 4: Prediction Pipeline
  - Inference function: feature_row → predict_proba
  - Predictions table schema
  - Wire Feature Builder → Inference into post-poll callback
  GATE: predictions table populates after a simulated historical race

Phase 5: Dashboard
  - Streamlit app reads predictions table
  - Bar chart ranked by win_probability
  - streamlit-autorefresh at 30s interval
  - Delta annotation (probability change from previous lap)
  GATE: dashboard shows historical race replay correctly

Phase 6: Live Race Integration (race weekend smoke test)
  - Validate poller works against live session
  - Tune poll interval if rate limits hit
  - End-to-end: OpenF1 live → SQLite → predictions → dashboard
```

**Why this order matters:**

- The Feature Builder cannot be built meaningfully without raw data. Trying to design features without actual data leads to schema drift.
- The Model cannot be trained without features. Training before the feature table is stable causes constant retraining.
- The Dashboard is last because it is the simplest component — it just reads a table. Building it first (a common impulse) wastes time on UI before the data is trustworthy.

---

## Deployment Topology

All components run on the developer's laptop during a race weekend. There is no cloud dependency.

```
Developer Machine
├── process 1: poller + feature builder + inference
│     Entry: python -m src.f1_prediction.pipeline
│     Runs: APScheduler BackgroundScheduler in foreground
│     Writes: data/f1.db (SQLite, WAL mode)
│
└── process 2: Streamlit dashboard
      Entry: streamlit run src/f1_prediction/dashboard.py
      Reads: data/f1.db (concurrent reads, no write)
      Opens: http://localhost:8501 in browser
```

**SQLite WAL mode is critical.** Without WAL (`PRAGMA journal_mode=WAL`), Streamlit reads will block on poller writes and vice versa. WAL allows one writer and multiple readers concurrently without locking.

**Directory layout implied by this architecture:**

```
f1_prediction/
├── data/
│   ├── f1.db                    # SQLite (raw + features + predictions)
│   └── models/
│       └── win_predictor.joblib # Trained model artifact
├── src/
│   └── f1_prediction/
│       ├── client.py            # OpenF1 API wrapper
│       ├── poller.py            # APScheduler + fetch logic
│       ├── schema.py            # SQLite table definitions
│       ├── features.py          # Feature computation
│       ├── model.py             # Training + inference
│       ├── pipeline.py          # Orchestrates poller → features → inference
│       └── dashboard.py         # Streamlit app
├── scripts/
│   ├── backfill.py              # One-shot historical data fetch 2022–2025
│   └── train.py                 # Offline model training
└── data/                        # gitignored
```

**No Docker, no message queue, no external services.** The constraints (local-first, Python-only, OpenF1 as sole data source) make this the right call for v1. Adding Redis or a task queue would solve problems this system will not encounter at local scale.

---

## Key Architecture Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| OpenF1 free tier blocks live session data | MEDIUM | HIGH | Sponsor tier is €9.90/month — budget it for race weekends; fall back to 30s delayed polling |
| SQLite write contention on race day | LOW | MEDIUM | WAL mode + write-ahead log; poller batches inserts in a single transaction |
| Model retraining lag if feature schema changes | HIGH | MEDIUM | Keep feature schema stable; version the model artifact filename |
| Streamlit re-runs on every refresh, slow DB reads | LOW | LOW | Cache DB reads with `@st.cache_data(ttl=25)`, read only predictions table on refresh |
| Race data arrives out of order (interval gaps) | MEDIUM | LOW | Sort by date_utc before feature computation; tolerate missing rows in features (fill with prior lap) |

---

## Sources

- OpenF1 API documentation: https://openf1.org/docs/
- OpenF1 live dashboard implementation (Streamlit): https://dev.to/eraine14/building-a-live-f1-dashboard-using-openf1-and-streamlit-4odd
- APScheduler BackgroundScheduler: https://apscheduler.readthedocs.io/en/3.x/userguide.html
- SSE/FastAPI real-time patterns (considered, rejected for local Streamlit): https://towardsdatascience.com/introducing-server-sent-events-in-python/
- Streamlit auto-refresh pattern: https://discuss.streamlit.io/t/continuously-updating-dashboard/532
- F1 lap-by-lap ML (Jared-Chan reference, archived): https://github.com/Jared-Chan/f1ml
