# Stack Research — F1 Race Winner Prediction

**Project:** F1 Race Winner Prediction System
**Researched:** 2026-06-16
**Overall Confidence:** HIGH (versions verified against live PyPI; API coverage confirmed against official docs)

---

## Critical Data Source Finding

**OpenF1 only covers 2023 onwards — not 2022.**

The PROJECT.md specifies a 2022–2025 training window. OpenF1's own documentation states: "Historical data (from 2023 onwards) is free and accessible without authentication." This is a constraint that affects the data layer design. The recommended approach: use **FastF1** for historical 2022–2025 data ingestion (it covers 2018+, sourcing from F1's live timing service), and switch to **OpenF1** for live race-day lap-by-lap polling. These two sources are complementary, not competing.

---

## Data Layer

### Primary Historical Source: FastF1

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| fastf1 | 3.8.3 | Fetch 2022–2025 historical race/qualifying/practice data | Only Python library covering full 2022 season with lap times, sector times, tire compounds, and driver telemetry. Built-in file cache avoids re-fetching. Ergast-compatible via jolpica-f1. |
| httpx | 0.28.1 | HTTP client for OpenF1 REST polling during live races | Async-native, faster than requests, already installed. Use with `asyncio` for non-blocking lap polling. |

FastF1 uses F1's official live timing service as its data source and caches everything locally as Parquet files. The cache is essential — the API is rate-limited and sessions take 10–30 seconds to load the first time.

### Secondary Live Source: OpenF1 API (Race Day Only)

Use OpenF1's `/laps`, `/position`, `/stints`, `/pit` endpoints with polling interval of 10–15 seconds during live races. The free tier allows 3 requests/second and 30/minute, which comfortably supports polling 4–5 endpoints per lap. No auth required.

### Storage: SQLite

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| SQLite (via stdlib) | 3.x (stdlib) | Store processed historical features, lap-level live race state, trained model predictions | Zero setup, zero infra, file-based. This is a local tool; analytical queries on ~3 seasons of F1 data (~60 races × ~60 laps × 20 drivers = ~72,000 rows) never exceed SQLite's performance ceiling. DuckDB is faster for multi-million-row OLAP but that's 100x more data than we have. |
| SQLAlchemy | 2.0.51 | ORM / schema management over SQLite | Gives migration capability if schema evolves; avoids raw SQL string assembly; already familiar to Python devs. |

**Do not use DuckDB here.** DuckDB excels at columnar OLAP over millions of rows. Our dataset is tiny. DuckDB also has slower point-lookup performance than SQLite, which matters for live lap writes. Use SQLite — it is the right tool for this scale.

---

## ML Framework

### Recommendation: LightGBM for win probability

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| lightgbm | 4.6.0 | Primary model: per-driver win probability, trained on historical race features | Fastest training iteration, native categorical support (driver, team, circuit — no one-hot encoding needed), handles missing data gracefully (rookies at new circuits), leaf-wise growth catches non-linear interaction effects (e.g. tyre degradation × lap delta × circuit characteristics). Outperforms XGBoost on categorical-heavy tabular data. |
| scikit-learn | 1.7.2 | Feature preprocessing pipeline, cross-validation, calibration | `CalibratedClassifierCV` essential for converting raw scores to true probabilities. `Pipeline` keeps preprocessing reproducible. `TimeSeriesSplit` for chronological CV — never use random splits on race data. |
| pandas | 2.3.3 | Feature engineering dataframes | Standard. Use with FastF1 which already returns DataFrames. |
| numpy | 2.2.6 | Numerical operations in feature engineering | Standard. |
| joblib | 1.5.3 | Model serialization (save/load trained model) | stdlib-adjacent, used by scikit-learn natively. `joblib.dump(model, 'model.pkl')` + `joblib.load()` for fast model loading. |
| scipy | 1.15.3 | Softmax normalization to produce a valid probability distribution summing to 1.0 across all drivers | `scipy.special.softmax` on raw LightGBM scores → valid win probability distribution. |

**Why LightGBM over XGBoost for this project:**
- Native categorical encoding for driver/team/circuit columns — critical for F1 where these are high-cardinality but meaningful.
- Faster training (leaf-wise vs level-wise) makes iterative experimentation faster.
- `is_unbalance=True` parameter handles the extreme class imbalance (1 winner per ~20 drivers).
- XGBoost 3.x now has similar categorical support, but LightGBM's implementation is more mature and better tested on small, high-cardinality tabular data.

**Feature set to engineer (informs the data pipeline):**
- Qualifying delta to pole (seconds behind fastest)
- Practice pace (FP2/FP3 representative lap vs field median)
- Circuit-specific historical win rate per driver
- Championship standings position
- Constructor pace delta at this circuit (last 2 seasons)
- Starting grid position
- Live (race-day): current lap gap to leader, tyre compound age, pit stop count

---

## Live Update Layer

### Polling architecture (not WebSocket)

OpenF1 is a REST API with 3-second data lag. There is no WebSocket feed on the free tier. The live update pattern is therefore **polling**, not event-driven.

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| APScheduler | 3.11.2 | Schedule periodic lap polling during race | Proven, configurable interval, runs in-process alongside FastAPI. Use `BackgroundScheduler` (threaded) so it doesn't block the ASGI event loop. No separate worker process needed. |
| httpx | 0.28.1 | Async HTTP client for OpenF1 polling | Same library as data layer. `httpx.AsyncClient` for non-blocking requests inside APScheduler jobs. |

**Polling flow every lap (~90 seconds at race pace):**
1. APScheduler fires every 60 seconds during race window
2. Fetch `/laps?session_key=...&lap_number=N` + `/position?session_key=...` + `/stints?session_key=...` from OpenF1
3. Compute updated feature vector per driver
4. Run `model.predict_proba()` — LightGBM inference on ~20 rows takes < 5ms
5. Apply softmax normalization
6. Write new prediction row to SQLite `predictions` table with timestamp
7. FastAPI `/predictions` endpoint picks it up on next dashboard poll

**Why not Celery/RQ:** Both require Redis/RabbitMQ brokers — unnecessary infrastructure for a local tool that runs on one machine. APScheduler runs in-process.

---

## Web Dashboard

### Recommendation: Dash (Plotly)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| dash | 4.2.0 | Dashboard framework — live-updating bar chart of driver win probabilities | Pure Python dashboard. Dash `dcc.Interval` component triggers callback every 10–15 seconds to refresh from the SQLite predictions table. No JavaScript required. Built on Plotly for charts. |
| dash-bootstrap-components | 2.0.4 | Responsive layout, clean styling | Bootstrap grid layout in pure Python. Minimal effort for professional-looking dashboard. |
| plotly | 6.8.0 | Charting engine (ships with Dash) | Horizontal bar chart for win probabilities with color-coded driver teams. Interactive hover. Already ships as Dash dependency. |
| FastAPI | 0.137.1 | Backend API serving predictions and status | Handles `/predictions` (current probabilities), `/race/status` (lap number, session state), `/health`. Dash can read directly from SQLite but separating concerns with a thin API allows future dashboard replacement. |
| uvicorn | 0.49.0 | ASGI server for FastAPI | Standard. Run with `--reload` during development. |

**Why Dash over Streamlit:**
Streamlit reruns the entire script on every interaction — for a dashboard that auto-refreshes every 10 seconds, this creates jitter and stale chart redraws. Dash only re-runs the specific callback that changed (the Interval), which is significantly more predictable for a live monitoring scenario. Streamlit's `st.rerun()` pattern also makes it harder to reason about state during a long race.

**Why Dash over a custom HTML/JS frontend:**
This is a local tool for one user. Dash gives live charts, driver table, and lap counter in ~100 lines of Python with no JavaScript build tooling.

**Dashboard layout:**
- Top: race name, current lap, session status (LIVE/HISTORICAL)
- Main: horizontal bar chart, drivers sorted by win probability (updates each interval)
- Side: data table with probability values, tire compound, current position
- Auto-refresh: `dcc.Interval(interval=10_000)` polling FastAPI `/predictions`

---

## What NOT to Use

| Library/Tool | Why Not |
|---|---|
| **Celery + Redis** | Overkill for a single-machine scheduler. APScheduler in-process is sufficient. |
| **DuckDB** | Wrong tool for this scale. SQLite handles our dataset easily; DuckDB adds complexity with no benefit at 72K rows. |
| **PyTorch / TensorFlow / neural networks** | F1 race prediction is a tabular problem with small data (~3 seasons). Deep learning requires 10x more data to beat LightGBM here, adds interpretability cost, and slows iteration. SOTA on tabular ML in 2025 is still gradient boosting. |
| **Streamlit** | Script-rerun-on-interaction model creates unpredictable behavior for a self-refreshing live dashboard. See above. |
| **aiohttp** | httpx provides the same async HTTP with a cleaner API and better timeout/retry ergonomics. No reason to use both. |
| **CatBoost** | Excellent library, but slower inference than LightGBM and heavier install. For this project (20-row inference every 60 seconds) the performance difference is immaterial, and LightGBM has better integration with scikit-learn pipelines. |
| **OpenF1 alone for historical data** | OpenF1 only covers 2023+. FastF1 is required for 2022 data. |
| **schedule (pip)** | Single-threaded, blocks the main thread. APScheduler runs in a background thread pool and survives exceptions gracefully. |
| **WebSockets for live updates** | OpenF1 free tier is REST only. The data lag is already 3 seconds; polling every 10–15 seconds is perfectly adequate and far simpler to implement and debug. |
| **Ergast API** | Deprecated and shut down in 2024. Jolpica-f1 is its successor, but FastF1 wraps jolpica-f1 automatically — use FastF1, not Jolpica directly. |

---

## Confidence Levels

| Recommendation | Confidence | Basis |
|---|---|---|
| FastF1 for historical 2022–2025 | HIGH | Official FastF1 docs confirm 2018+ telemetry coverage; PyPI version 3.8.3 verified |
| OpenF1 for live race polling (2023+ only) | HIGH | Official OpenF1 docs confirm 2023+ coverage and rate limits; endpoints confirmed |
| SQLite + SQLAlchemy for storage | HIGH | Confirmed appropriate scale (< 100K rows); well-established pattern |
| LightGBM 4.6.0 as primary model | HIGH | PyPI version verified; categorical feature handling advantage confirmed in literature |
| scikit-learn 1.7.2 for calibration + CV | HIGH | PyPI version verified; `CalibratedClassifierCV` + `TimeSeriesSplit` are established patterns |
| APScheduler 3.11.2 for polling | HIGH | PyPI version verified; BackgroundScheduler thread model confirmed for FastAPI |
| Dash 4.2.0 for dashboard | MEDIUM | Dash 4.x is newly released (early 2026); core API is stable but some component APIs shifted from Dash 3. Test Interval + callbacks on 4.x specifically. |
| FastAPI 0.137.1 as backend | HIGH | PyPI version verified; WebSocket and SSE support confirmed; active maintenance |
| httpx 0.28.1 for HTTP | HIGH | Already installed in project venv; async support confirmed |

---

## Installation

```bash
# Data layer
pip install fastf1==3.8.3 httpx==0.28.1 sqlalchemy==2.0.51

# ML stack
pip install lightgbm==4.6.0 scikit-learn==1.7.2 pandas==2.3.3 numpy==2.2.6 joblib==1.5.3 scipy==1.15.3

# Live update layer
pip install apscheduler==3.11.2

# Web dashboard + API
pip install fastapi==0.137.1 uvicorn==0.49.0 dash==4.2.0 dash-bootstrap-components==2.0.4 plotly==6.8.0
```

---

## Sources

- OpenF1 official documentation (openf1.org/docs) — data coverage confirmed 2023+, rate limits, endpoint list
- FastF1 official documentation (docs.fastf1.dev) — 2018+ coverage confirmed
- Jolpica-f1 (github.com/jolpica/jolpica-f1) — Ergast successor, 1950-present coverage
- PyPI live version queries — all versions verified June 2026
- tomasz-solis/formula1 GitHub — real F1 prediction project using scikit-learn + FastF1 + FastAPI
- Analytics Vidhya / GeeksForGeeks — LightGBM vs XGBoost for small tabular datasets
- Dash vs Streamlit comparison (reflex.dev/blog, squadbase.dev) — Dash callbacks vs Streamlit rerun model
- BetterStack DuckDB vs SQLite guide — confirmed SQLite appropriate at our scale
