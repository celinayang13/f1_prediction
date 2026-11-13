# F1 Race Winner Prediction

A local Python application that estimates each driver's chance of winning an F1 Grand Prix before the race and updates the estimate lap by lap. A Streamlit dashboard shows the probabilities and the state of the race.

## How well it works

Every season is predicted by models trained only on earlier seasons (walk-forward). "Winner score" is the average negative log of the probability given to the actual winner: lower is better, and a uniform guess scores about 3.0. Brackets are 95% intervals from resampling whole races.

**All tested seasons, 2023–2026 (90 races)**

| | Winner score | Top pick wins |
| --- | --- | --- |
| Pre-race: grid-slot lookup (baseline) | 1.56 | 61% |
| Pre-race: served model | 1.16 | 60% |
| Pre-race difference | −0.40 [−0.52, −0.28] | |
| In-race: position-by-stage lookup (baseline) | 0.84 | 77% |
| In-race: served model | 0.62 | 81% |
| In-race difference | −0.22 [−0.30, −0.15] | |

**2026 only (20 races)** — the one season no modelling choice had seen:

| | Winner score | Top pick wins |
| --- | --- | --- |
| Pre-race: grid-slot lookup | 1.32 | 70% |
| Pre-race: served model | 1.19 | 65% |
| Pre-race difference | −0.13 [−0.36, +0.10] | |
| In-race: position-by-stage lookup | 1.03 | 65% |
| In-race: served model | 0.72 | 71% |
| In-race difference | −0.32 [−0.51, −0.16] | |

What to take from this:

- **In-race predictions clearly beat the simple baseline**, including on 2026.
- **Pre-race predictions are not established as better than "look at the grid"** on 2026 alone, and should be treated as experimental. 2023–2025 informed the choice of inputs, so the all-seasons pre-race figure is optimistic.
- **The model is not memorising:** it scores about the same on races it was trained on as on races it was not (pre-race 1.13 vs 1.16).
- Top-pick accuracy is similar to the baselines; the gain is in how well the probabilities are sized.

Reproduce with `python scripts/evaluate.py`. Each run writes a timestamped report to `data/eval/`.

## Architecture

```text
FastF1 (2022) / OpenF1 (2023+) → ingestion → SQLite raw tables → feature engineering
                                                                        ↓
                                                                 SQLite features
                                                                        ↓
                                                             field-choice win models
                                                                        ↓
                                                               SQLite predictions
                                                                        ↓
                                                               Streamlit + Plotly
```

The prediction pipeline and dashboard run independently and share a SQLite database. WAL mode supports concurrent reads and writes; every replacement of predictions or live race data happens inside one transaction, so the dashboard never reads a half-written race.

## Stack

| Layer | Tools |
| --- | --- |
| Runtime | Python 3.11+, setuptools |
| Data sources | FastF1 for 2022; OpenF1 for 2023 onward |
| HTTP and retries | HTTPX, Tenacity |
| Storage | SQLite, SQLAlchemy |
| Feature engineering | pandas, NumPy |
| Models | Field-choice model (conditional logit, fitted with SciPy); XGBoost kept as an evaluation challenger |
| Live scheduling | APScheduler |
| Dashboard | Streamlit, Plotly |
| Tests | pytest |

## Setup

Run commands from the repository root:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock  # exact versions the model artifacts were built with
python -m pip install -e '.[dev]'
```

### Prepare data and models

The database and download cache are not in Git. Backfill creates them:

```bash
python scripts/backfill.py --years 2022 2023 2024 2025 2026
python scripts/check_backfill.py
```

A full backfill takes a few hours: OpenF1's free tier allows 30 requests a minute and the loader paces itself under that. It is resumable — sessions already stored are skipped, and sessions that have not finished yet are left for a later run. The backfill also loads official race results and driver/team data, then builds features. To rebuild features on their own:

```bash
python scripts/build_features.py
```

Train and evaluate:

```bash
python scripts/train.py --model prerace
python scripts/train.py --model inrace
python scripts/evaluate.py   # walk-forward: served model vs baselines and an XGBoost challenger
```

`train.py` fits on every season in the database. Model artifacts live in `data/models/`, each with a `.meta.json` recording the training seasons, fitted coefficients, library versions and code revision.

### Run the application

Start the prediction pipeline:

```bash
python -m f1_prediction.pipeline                      # follows the race in progress, else the next one
python -m f1_prediction.pipeline --session-key 9839   # or a specific race
```

In a second terminal, launch the dashboard:

```bash
source .venv/bin/activate
streamlit run src/f1_prediction/dashboard.py
```

The pipeline:

1. Picks the race from the OpenF1 schedule (falling back to the latest stored race if the schedule is unavailable).
2. Prepares the weekend — loads finished qualifying, practice and sprint sessions, the starting grid and drivers — and recomputes pre-race predictions every 10 minutes until the start.
3. Polls OpenF1 every 30 seconds during the race and recomputes in-race predictions when laps arrive or change.

The dashboard binds to localhost, has a race selector in the sidebar, and refreshes its in-race view every 15 seconds. Each race shows a status: **PRE-RACE**, **LIVE**, **STALE** (the race is on but no fresh prediction has arrived for 90 seconds) or **FINISHED** (with the official winner). A past race is never shown as live.

### Live data needs a paid OpenF1 account

OpenF1 serves a session's data for free only from 30 minutes after it ends. During a session (and 30 minutes either side) data requires their sponsor tier. See [docs/provider-capability.md](docs/provider-capability.md).

- **Without an account:** pre-race predictions work on race weekend; in-race predictions fill in about 30 minutes after the flag.
- **With an account:** set `OPENF1_USERNAME` and `OPENF1_PASSWORD` in the environment before starting the pipeline. This login path is tested only against a mock, never against the real live feed.

## Models and features

Both models are **field-choice models**: each driver gets a score, and the probabilities come from comparing scores across the whole field in one step. They are trained directly on the probability given to the actual winner, so the output is a distribution by construction — there is no class re-weighting, calibration or rescaling step.

- **Pre-race (2 inputs):** grid slot, and the driver's average qualifying gap to pole over their previous five races.
- **In-race (15 inputs):** position, gap to the leader, gap per remaining lap, pit stops made relative to the field, tyre age relative to the field, grid slot and recent qualifying pace — each also weighted by how far through the race it is.

Input definitions live in `src/f1_prediction/inference/field_choice.py` and are shared by training and inference.

Things that were tested and left out because they did not help on unseen races: championship position, recent wins and podiums, practice pace, constructor points, sector-time "ultimate lap", sprint results, driver and team Elo ratings, and training on the full finishing order instead of the winner.

### Known limitations

- The pre-race model is not yet shown to beat the grid baseline on a clean season (see above).
- The pre-race grid is the one published after qualifying; late penalties can change it before the start.
- 2022 gaps to the leader are approximated from lap times (later seasons use lap timestamps), and 2022 grid slots come from lap-1 positions.
- Six 2023 races have no pit-stop data.
- During a race the scheduled distance is taken from the circuit's previous race; a new circuit has none, so laps remaining is wrong until the race ends.
- If more than 60% of the running cars are lapped, the final lap's prediction is not published.
- Live operation has never been run against a real live feed.

## Testing and replay

```bash
pytest -m 'not slow'
```

Tests cover ingestion, features, the model, training, evaluation scoring, prediction storage, live polling, race selection, dashboard status and historical replay. Tests marked `slow` need FastF1 cache or network access.

For replay options, run:

```bash
python scripts/replay_race.py --help
```

In-place replay temporarily clears and rebuilds a race's raw rows. Use a scratch database copy with `--db` when experimenting; it has previously left a race without position data in the working database.

## Repository layout

```text
src/f1_prediction/
  ingest/          Data clients, loaders, and normalization
  db/              Schema, connections, queries, and inserts
  features/        Historical and lap-level feature computation
  inference/       Field-choice model, shared inputs, prediction storage
  live/            Polling and session-window checks
  dashboard_lib/   Dashboard queries, charts and status logic
  pipeline.py      Race selection, weekend preparation, poll-and-infer loop
  dashboard.py     Streamlit application
scripts/           Backfill, feature building, training, evaluation, replay
tests/             Test suite and sample API fixtures
data/models/       Serialized models and their metadata
data/eval/         Timestamped evaluation reports
docs/              Provider capability report
```

Dependencies are declared in `pyproject.toml` and pinned in `requirements.lock`. `CLAUDE.md` and older planning documents still describe an earlier planned stack (LightGBM, Dash, FastAPI) that was never the implemented one.
