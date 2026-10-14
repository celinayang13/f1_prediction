# F1 Race Winner Prediction

A local Python application that estimates each driver's chance of winning an F1 Grand Prix before the race and updates predictions as lap data arrives. A Streamlit dashboard displays probabilities and race state.

## Architecture

```text
FastF1 / OpenF1 → ingestion → SQLite raw tables → feature engineering
                                                       ↓
                                                SQLite features
                                                       ↓
                                       XGBoost models + calibration
                                                       ↓
                                            SQLite predictions
                                                       ↓
                                           Streamlit + Plotly
```

The prediction pipeline and dashboard run independently and share a SQLite database. WAL mode supports concurrent reads and writes; prediction replacements use transactions.

## Stack

| Layer | Tools |
| --- | --- |
| Runtime | Python 3.11+, setuptools |
| Data sources | FastF1 for years through 2022; OpenF1 for 2023 onward |
| HTTP and retries | HTTPX, Tenacity |
| Storage | SQLite, SQLAlchemy |
| Feature engineering | pandas, NumPy |
| Models | XGBoost, scikit-learn isotonic calibration |
| Model artifacts and normalization | joblib, SciPy |
| Live scheduling | APScheduler |
| Dashboard | Streamlit, Plotly |
| Tests | pytest |

## Setup

Run commands from the repository root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

### Prepare data and models

The local database and downloaded data cache are excluded from Git. Backfill historical sessions to create the database and features:

```bash
python scripts/backfill.py --years 2022 2023 2024
python scripts/check_backfill.py
```

Downloads require network access and can take time. To rebuild features separately:

```bash
python scripts/build_features.py
```

Train both models with 2024 held out for validation:

```bash
python scripts/train.py --model prerace --val-year 2024
python scripts/train.py --model inrace --val-year 2024
python scripts/validate_model.py --val-year 2024
```

Model artifacts live in `data/models/`. Retrain them in your environment if serialized artifacts are incompatible with installed dependency versions.

### Run the application

Start the prediction pipeline:

```bash
python -m f1_prediction.pipeline
```

In a second terminal, activate the environment and launch the dashboard:

```bash
source .venv/bin/activate
streamlit run src/f1_prediction/dashboard.py
```

The dashboard binds to localhost. The pipeline selects the latest race session in the database, computes pre-race predictions if needed, and polls every 30 seconds. New lap arrivals trigger in-race inference. The dashboard's in-race view refreshes every 15 seconds.

Before running against a current race, ingest its available sessions and build its features. Live API availability and access requirements depend on the data provider.

## Models and features

- **Pre-race:** qualifying delta to pole, FP2/FP3 pace, circuit win rate, championship position, grid position, and driver experience.
- **In-race:** gap to leader, tyre age, pit-stop count, position, intervals ahead/behind, pit-stop delta, lap number, laps remaining, and five tyre-compound indicators.

Training and inference share feature definitions in `src/f1_prediction/inference/features.py`. Each model predicts a binary winner probability per driver. The application then applies softmax within each race or race/lap group and stores the resulting distribution.

### Current limitations

Softmax is applied to already calibrated probabilities, which compresses the displayed distribution and changes its calibration. A sum of one does not establish predictive accuracy. Training and validation scripts report Brier score and log loss for the raw classifier probabilities separately.

Calibration folds are not explicitly grouped by race; race-aware calibration is an area for improvement. Live inference rebuilds the session's in-race features and predicts only the latest available lap, so polling can skip intermediate lap predictions. Historical replay calls inference directly because the live poller checks the actual session time window.

## Testing and replay

```bash
pytest -m 'not slow'
```

Tests cover ingestion, features, training, prediction storage, polling, and historical replay. Tests marked `slow` can require FastF1 cache/network access.

For replay options, run:

```bash
python scripts/replay_race.py --help
```

In-place replay temporarily clears and rebuilds a race's raw rows. Use a scratch database copy with `--db` when experimenting; the normal dashboard continues to use `data/f1.db`.

## Repository layout

```text
src/f1_prediction/
  ingest/          Data clients, loaders, and normalization
  db/              Schema, connections, queries, and inserts
  features/        Historical and lap-level feature computation
  inference/       Shared feature contracts and prediction storage
  live/            Polling and session-window checks
  dashboard_lib/   Dashboard queries and charts
  pipeline.py      Poll-and-infer orchestration
  dashboard.py     Streamlit application
scripts/           Backfill, feature building, training, validation, replay
tests/             Test suite and sample API fixtures
data/models/       Serialized models
```

Implementation dependencies are declared in `pyproject.toml`. Older planning documents may describe technologies that differ from the implemented stack.
