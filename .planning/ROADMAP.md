# Roadmap: F1 Race Winner Prediction

## Overview

Four phases take the project from an empty SQLite database to a live dashboard showing win probabilities that update lap-by-lap during a race weekend. Phase 1 builds the data foundation and feature pipeline — the two are coupled because features cannot be validated without real data. Phase 2 trains both the pre-race and in-race XGBoost models offline using that feature set. Phase 3 wires inference into the polling loop and ships the Streamlit dashboard. Phase 4 validates the full system against a live race weekend, the only environment that exercises the real-time path end to end.

**Stack:** Python · FastF1 (2022 backfill) + OpenF1 (2023–2025 + live) · SQLite (WAL mode) · XGBoost · scikit-learn · Streamlit

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Data & Features** - Historical backfill, live poller, and feature table populated with engineered race features
- [ ] **Phase 2: Model Training** - Pre-race and in-race LightGBM models trained offline and serialized to disk
- [ ] **Phase 3: Prediction Pipeline & Dashboard** - Inference wired into the poll loop; Dash dashboard showing live win probabilities
- [ ] **Phase 4: Live Race Integration** - End-to-end smoke test against a real F1 race weekend session

## Phase Details

### Phase 1: Data & Features

**Goal:** The system can fetch, store, and transform all historical and live F1 session data into a queryable feature table ready for model training
**Mode:** mvp
**Depends on:** Nothing (first phase)
**Requirements:** DATA-01, DATA-02, DATA-03, DATA-04
**Success Criteria** (what must be TRUE):

  1. Running `scripts/backfill.py` populates `raw_laps`, `raw_positions`, `raw_stints`, `raw_pit_stops`, and `raw_sessions` tables with 2022–2025 race, qualifying, and practice data (sourced from FastF1 for 2022 and OpenF1 for 2023–2025)
  2. Weather conditions (wet/dry/mixed) are stored per session and queryable alongside lap data
  3. The live poller runs continuously and inserts new lap-by-lap position, interval, and lap time rows during an active race session
  4. Tyre compound and age per driver per stint are stored and joinable to lap rows
  5. The feature builder produces a populated `features` table with one row per (session_key, driver_number, lap_number) covering all historical sessions

**Plans:** 3/3 plans executed
**Wave 1**

- [x] 01-01-PLAN.md — Walking Skeleton: scaffold, ORM/WAL engine, FastF1 single-race ingest, E2E pytest

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02-PLAN.md — Full historical backfill: widen schema, OpenF1 client, FastF1+OpenF1 loaders for all 5 raw tables + weather

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 01-03-PLAN.md — Feature builder (features_prerace + features_inrace) + APScheduler live poller

### Phase 2: Model Training

**Goal:** Two trained and calibrated XGBoost models exist on disk — one for pre-race win probability from qualifying data, one for in-race win probability from live lap state
**Mode:** mvp
**Depends on:** Phase 1
**Requirements:** PRERACE-01, PRERACE-02, PRERACE-03, INRACE-01, INRACE-02, INRACE-03
**Success Criteria** (what must be TRUE):

  1. The pre-race model is trained on qualifying gap to pole, practice lap times, circuit-specific win history, constructor performance, and championship standings; the model artifact is saved to `data/models/prerace_model.joblib`
  2. Running the pre-race model on a held-out 2024 qualifying session outputs a valid win probability distribution across all ~20 drivers (probabilities sum to 1.0 ± 0.001)
  3. The in-race model is trained on mid-race state features (current positions, gaps to leader, laps remaining, tyre compound and age, pit stop history); the artifact is saved to `data/models/inrace_model.joblib`
  4. Running the in-race model on a replayed historical race lap outputs an updated win probability distribution that reflects lap-by-lap changes

**Plans:** 4 plans
**Wave 1**

- [ ] 02-01-PLAN.md — Backfill extension (2022 r17-22 + 2023-2025 full) + round_number=0 fix + feature rebuild

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 02-02-PLAN.md — Pre-race XGBoost model end-to-end: scripts/train.py scaffold, --model prerace branch, calibration, sum-to-1, save prerace_model.joblib

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 02-03-PLAN.md — In-race XGBoost model end-to-end: --model inrace branch, tyre one-hot encoding, per-lap softmax, save inrace_model.joblib

**Wave 4** *(blocked on Wave 3 completion)*

- [ ] 02-04-PLAN.md — Validation: scripts/validate_model.py loads both artifacts, asserts sum-to-1 + Brier/log-loss on held-out 2024

### Phase 3: Prediction Pipeline & Dashboard

**Goal:** A locally running dashboard shows ranked win probabilities for all drivers, sourced from a prediction pipeline that fires after each completed race lap
**Mode:** mvp
**Depends on:** Phase 2
**Requirements:** DASH-01, DASH-02, DASH-03, DASH-04
**Success Criteria** (what must be TRUE):

  1. After each poll cycle detects a new lap, inference runs automatically and inserts updated win probability rows into the `predictions` table
  2. The dashboard at `localhost:8501` shows a horizontal bar chart with all drivers ranked by their current win probability
  3. The pre-race tab displays qualifying-based win probabilities computed from qualifying session data before the race starts
  4. The in-race tab auto-refreshes each lap and reflects updated probabilities without manual page reload
  5. The dashboard runs with no authentication and requires only `python -m src.f1_prediction.pipeline` and `python -m streamlit run src/f1_prediction/dashboard.py`

**Plans:** TBD
**UI hint:** yes

### Phase 4: Live Race Integration

**Goal:** The end-to-end system runs correctly against a real F1 race weekend session — live data flows from OpenF1 through the pipeline into the dashboard without manual intervention
**Mode:** mvp
**Depends on:** Phase 3
**Requirements:** (none — integration validation phase; all requirements satisfied in Phases 1–3)
**Success Criteria** (what must be TRUE):

  1. During an active race session, the poller fetches new lap data from OpenF1 within 60 seconds of lap completion and triggers feature building and inference automatically
  2. The dashboard updates win probabilities lap-by-lap without user action during a live race
  3. No SQLite write-contention errors occur during concurrent poller writes and dashboard reads (WAL mode confirmed working)
  4. The full system (poller + dashboard) runs stably for a full race distance (~60 laps) on the developer's machine without crash or hang

**Plans:** TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Data & Features | 3/3 | Complete | 2026-06-17 |
| 2. Model Training | 0/TBD | Not started | - |
| 3. Prediction Pipeline & Dashboard | 0/TBD | Not started | - |
| 4. Live Race Integration | 0/TBD | Not started | - |
