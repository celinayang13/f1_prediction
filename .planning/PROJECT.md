# F1 Race Winner Prediction

## What This Is

A machine learning system that predicts the probability of each driver winning an F1 Grand Prix. It produces a win probability distribution across all ~20 drivers, starting from a pre-race baseline (using qualifying and practice data) and updating live lap-by-lap as the race unfolds. A local web dashboard displays the shifting probabilities in real time.

## Core Value

Show accurate, live-updating win probabilities for every driver during an F1 race weekend — the model must be useful *during* the race, not just before it.

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

- Data source: OpenF1 API (https://openf1.org) — provides live telemetry, historical race/qualifying/practice data. Free, no auth required, well-structured JSON.
- Training window: 2022–2025 seasons (modern ground-effect era, consistent regulations — most relevant to current grid behavior)
- Live update mechanism: OpenF1 provides real-time lap data during race weekends; predictions must re-run each lap without UI latency
- The project already has a Python venv set up and empty `src/`, `scripts/`, and `data/` directories
- Prior session context: a data polling architecture was discussed (SQLite DB, scheduler) — this may inform the data layer design

## Constraints

- **Data**: OpenF1 API only — no paid data subscriptions
- **Deployment**: Local-first (runs on developer's machine during race weekends)
- **Language**: Python — venv already set up
- **Training data**: 2022–2025 only — older data has different car/reg characteristics

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| FastF1 (2022) + OpenF1 (2023–2025 + live) | OpenF1 only covers 2023+; FastF1 fills 2022 gap; OpenF1 is the only live race-day option | — Pending |
| 2022–2025 training window | Ground-effect era = consistent car behavior, most predictive for current season | — Pending |
| Win probability distribution (not single pick) | More informative, captures uncertainty, better for dashboard visualization | — Pending |
| XGBoost + Streamlit over LightGBM + Dash | User preference; widely known ecosystem, st.rerun() sufficient for lap-by-lap refresh cadence | Validated in Phase 03 — Streamlit dashboard built, live-tested end-to-end (pre-race + auto-refreshing in-race tabs) |
| Local deployment first | Fastest path to a working tool; hosting added later if wanted | Validated in Phase 03 — dashboard confirmed localhost-only bind, no external exposure, no auth |
| Multinomial model framing (not 20 binary classifiers) | 1:19 class imbalance; single softmax over all drivers is the correct formulation | — Pending |

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
*Last updated: 2026-07-09 after Phase 04 (live-race-integration) completion — v1.0 milestone complete*
