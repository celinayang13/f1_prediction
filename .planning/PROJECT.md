# F1 Race Winner Prediction

## What This Is

A machine learning system that predicts the probability of each driver winning an F1 Grand Prix. It produces a win probability distribution across all ~20 drivers, starting from a pre-race baseline (using qualifying and practice data) and updating live lap-by-lap as the race unfolds. A local web dashboard displays the shifting probabilities in real time.

## Core Value

Show accurate, live-updating win probabilities for every driver during an F1 race weekend — the model must be useful *during* the race, not just before it.

## Current State

**Shipped: v1.0 MVP (2026-07-09)** — 4 phases, 12 plans, 14/14 v1 requirements validated, milestone audit passed 14/14 with no blockers ([v1.0-MILESTONE-AUDIT.md](milestones/v1.0-MILESTONE-AUDIT.md)).

The system runs end-to-end: `scripts/backfill.py` populates 2022-2025 historical data (FastF1 + OpenF1) → `scripts/train.py` produces calibrated XGBoost pre-race and in-race models → `python -m src.f1_prediction.pipeline` polls live laps and writes win probabilities → `streamlit run src/f1_prediction/dashboard.py` renders them at localhost:8501 with no auth. Validated via a full 72-lap historical replay (Phase 04) with zero crashes or SQLite lock contention. The one remaining unknown — behavior against a genuinely live OpenF1 session (network latency, rate limits) — is deferred to an actual future race weekend per an explicit, documented scope decision (not a defect).

## Next Milestone Goals

Candidates for v1.1+ (not yet committed — run `/gsd-new-milestone` to scope formally):

- **Real live-session validation**: confirm the poller/dashboard against an actual live OpenF1 race weekend (the one deferred check from v1.0).
- **Populate `constructor_pace_delta`**: the pre-race model currently ships without a constructor-performance signal (accepted v1.0 deviation) — populate the feature and retrain to test calibration impact.
- **v2 dashboard requirements** (already scoped in prior REQUIREMENTS.md, archived at [milestones/v1.0-REQUIREMENTS.md](milestones/v1.0-REQUIREMENTS.md)): probability history trend lines, driver info overlays, race event annotations (safety car/pit stops).
- **Hosting**: deploy dashboard to a small-group-accessible hosted environment (currently local-only by design).
- **Extended predictions**: sprint race winner model; top-3 podium probability distribution.

## Requirements

### Validated

- [x] Generate a pre-race win probability distribution before lights out, using qualifying results as primary input — Validated in Phase 03: prediction-pipeline-dashboard
- [x] Update win probability distribution live during the race using real-time lap positions, gaps, and pit stop/tyre data — Validated in Phase 03: prediction-pipeline-dashboard
- [x] Display win probabilities in a local web dashboard that refreshes during a live race — Validated in Phase 03: prediction-pipeline-dashboard
- [x] Dashboard shows all drivers ranked by win probability with a visual representation (bar/chart) — Validated in Phase 03: prediction-pipeline-dashboard
- [x] Fetch and store historical F1 race data (2022–2025) via OpenF1 API — Validated in Phase 01: data-features
- [x] Train an ML model on qualifying pace, practice times, circuit history, tyre strategy, car/constructor data, and championship standings — Validated in Phase 02: model-training
- [x] Full poller → feature-build → inference → dashboard path runs correctly end-to-end without manual intervention — Validated in Phase 04: live-race-integration (replay-based smoke test, 4/4 ROADMAP success criteria; real live-OpenF1-session check deferred per D-01 to an actual future race weekend)

### Active

None — all v1 requirements validated. v1.0 milestone complete (4/4 phases, 12/12 plans).

### Out of Scope

- Full grid position prediction (1–20 finishing order) — scope limited to winner probabilities for v1
- Public hosting / multi-user access — local deployment first, hosting deferred
- Fantasy F1 / betting integration — not the purpose
- Predictions for non-race sessions (sprint, qualifying outcome) — race winner only for v1

## Context

- Data source: OpenF1 API (https://openf1.org) — provides live telemetry, historical race/qualifying/practice data. Free, no auth required, well-structured JSON. FastF1 fills the 2022 gap (OpenF1 only covers 2023+).
- Training window: 2022–2025 seasons (modern ground-effect era, consistent regulations — most relevant to current grid behavior). Corpus: 1,753 pre-race rows, 84,258 in-race rows.
- Live update mechanism: `pipeline.py`'s APScheduler polls every 30s; on new laps it rebuilds in-race features and reruns inference; the Streamlit dashboard's In-Race tab auto-refreshes every 15s via `st.fragment`.
- Codebase: ~7,300 LOC Python across `src/f1_prediction/` (db, ingest, features, inference, dashboard) and `scripts/` (backfill, train, validate_model, replay_race, live_poller). 69 tests passing.
- Storage: SQLite in WAL mode — confirmed safe for concurrent poller writes + dashboard reads (0 lock-contention errors across all phase verifications).
- Known tech debt (non-blocking, tracked in [v1.0-MILESTONE-AUDIT.md](milestones/v1.0-MILESTONE-AUDIT.md)): `raw_weather` stored but unused by any model; `constructor_pace_delta` column unpopulated; no FastAPI layer (dashboard reads SQLite directly).

## Constraints

- **Data**: OpenF1 API only — no paid data subscriptions
- **Deployment**: Local-first (runs on developer's machine during race weekends)
- **Language**: Python — venv already set up
- **Training data**: 2022–2025 only — older data has different car/reg characteristics

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| FastF1 (2022) + OpenF1 (2023–2025 + live) | OpenF1 only covers 2023+; FastF1 fills 2022 gap; OpenF1 is the only live race-day option | ✓ Validated in Phase 01 — both loaders ship all 6 raw tables; confirmed end-to-end in Phase 04 replay |
| 2022–2025 training window | Ground-effect era = consistent car behavior, most predictive for current season | ✓ Validated in Phase 02 — full 2022-2025 corpus (1,753 prerace / 84,258 inrace rows), models trained on 2022-2023, held out 2024 for validation |
| Win probability distribution (not single pick) | More informative, captures uncertainty, better for dashboard visualization | ✓ Validated in Phase 02/03 — both models produce softmax distributions summing to 1.0 ± 1e-6; rendered as ranked bar charts |
| XGBoost + Streamlit over LightGBM + Dash | User preference; widely known ecosystem, st.rerun() sufficient for lap-by-lap refresh cadence | ✓ Validated in Phase 03 — Streamlit dashboard built, live-tested end-to-end (pre-race + auto-refreshing in-race tabs) |
| Local deployment first | Fastest path to a working tool; hosting added later if wanted | ✓ Validated in Phase 03 — dashboard confirmed localhost-only bind, no external exposure, no auth |
| Multinomial model framing (not 20 binary classifiers) | 1:19 class imbalance; single softmax over all drivers is the correct formulation | ✓ Validated in Phase 02 — CalibratedClassifierCV + per-race/per-lap softmax confirmed sum-to-1 on held-out 2024 data (0 violations) |
| No FastAPI layer (Streamlit reads SQLite directly) | CLAUDE.md recommended a thin FastAPI backend, but Phase 03 found direct SQLite reads sufficient at this scale and simpler to maintain | ✓ Validated in Phase 03/04 — no write contention (WAL mode), no broken links; noted as accepted stack simplification in v1.0 milestone audit |
| Accept constructor_pace_delta as unpopulated (PRERACE-01 scope narrowing) | Feature builder never populated the column; 7-feature model still produced valid calibrated probabilities (Brier=0.0381) | ⚠️ Revisit — candidate for v1.1: populate constructor pace delta and retrain to test whether it improves calibration |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-07-09 after v1.0 milestone completion*
