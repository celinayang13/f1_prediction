# Milestones

## v1.0 MVP (Shipped: 2026-07-09)

**Phases completed:** 4 phases, 12 plans, 14 tasks

**Key accomplishments:**

- FastF1-to-SQLite walking skeleton with WAL mode, ORM schema, normalizer, and 3 passing end-to-end tests for 2022 Bahrain race ingest.
- Complete raw-data substrate: 6 SQLite tables populated from FastF1 (2022) and OpenF1 (2023+) via tenacity-retried async client, with weather classification, stint derivation, and resumable year-loop backfill CLI.
- Two feature tables populated from raw SQL joins (qualifying delta, 107% practice pace, circuit win rates, in-race gap/tyre/pit features) plus APScheduler live poller with ±30-min session window guard and 34 total tests passing across Phase 1.
- Extended the historical backfill to full 2023-2025 OpenF1 coverage, fixed the `round_number=0` root-cause bug blocking multi-round ingestion, and rebuilt the feature tables to produce a complete training corpus with 1,753 pre-race and 84,258 in-race feature rows across 2022-2025.
- Calibrated XGBoost pre-race win probability model trained on 2022-2023 historical data, validated on held-out 2024 races, and serialized to `data/models/prerace_model.joblib` as a fitted `CalibratedClassifierCV`.
- Calibrated XGBoost in-race win probability model trained on 36756 lap-level rows (2022-2023), validated on 28952 held-out 2024 rows across 1566 (session_key, lap_number) groups, and serialized to `data/models/inrace_model.joblib` as a fitted `CalibratedClassifierCV`. Per-lap softmax sums to 1.0 ± 1e-6 across all validation lap groups.
- Standalone validation CLI (`scripts/validate_model.py`) that loads both saved model artifacts, runs `predict_proba` on held-out 2024 data, applies per-group softmax, and asserts sum-to-1 within 0.001 tolerance — confirming ROADMAP success criteria PRERACE-02 and INRACE-02 end-to-end.
- Repaired a completely broken Streamlit install (altair v4 crash), declared 6 silently-undeclared dependencies, extracted training/inference feature logic into a single shared module, and added the `predictions`/`raw_drivers` schema plus an OpenF1 `/v1/drivers` client method — clearing every blocker later Phase 3 plans depend on.
- `python -m src.f1_prediction.pipeline` auto-detects the latest race and computes idempotent pre-race win probabilities (XGBoost + per-race softmax) into SQLite; `streamlit run src/f1_prediction/dashboard.py` reads them read-only and renders a ranked, team-colored horizontal bar chart at localhost:8501.
- After each poll cycle that ingests new laps, `pipeline.py` rebuilds in-race features (rebuild-then-slice, Pattern 1), runs the calibrated XGBoost in-race model with per-lap softmax normalization, and writes the result to SQLite; `dashboard.py`'s In-Race tab reads it back every 15 seconds via `st.fragment`, rendering a LIVE badge, freshness caption, team-colored bar chart, and a position/tyre/gap side table — no manual refresh, no page reload.
- Dev-only lap-by-lap replay CLI (`scripts/replay_race.py`) proves the full poll → inference → dashboard chain works live, using a completed historical race in place of a real OpenF1 session — human-verified working on localhost with no auth.
- End-to-end replay validation (pipeline + dashboard + replay_race.py running concurrently against session_key=9550, 72 laps) scored 4/4 on all ROADMAP Phase 4 success criteria, with zero SQLite lock-contention errors and a human-verified auto-advancing dashboard.

---
