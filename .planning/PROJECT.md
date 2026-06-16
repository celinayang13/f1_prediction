# F1 Race Winner Prediction

## What This Is

A machine learning system that predicts the probability of each driver winning an F1 Grand Prix. It produces a win probability distribution across all ~20 drivers, starting from a pre-race baseline (using qualifying and practice data) and updating live lap-by-lap as the race unfolds. A local web dashboard displays the shifting probabilities in real time.

## Core Value

Show accurate, live-updating win probabilities for every driver during an F1 race weekend — the model must be useful *during* the race, not just before it.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Fetch and store historical F1 race data (2022–2025) via OpenF1 API
- [ ] Train an ML model on qualifying pace, practice times, circuit history, tyre strategy, car/constructor data, and championship standings
- [ ] Generate a pre-race win probability distribution before lights out, using qualifying results as primary input
- [ ] Update win probability distribution live during the race using real-time lap positions, gaps, and pit stop/tyre data
- [ ] Display win probabilities in a local web dashboard that refreshes during a live race
- [ ] Dashboard shows all drivers ranked by win probability with a visual representation (bar/chart)

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
| XGBoost + Streamlit over LightGBM + Dash | User preference; widely known ecosystem, st.rerun() sufficient for lap-by-lap refresh cadence | — Pending |
| Local deployment first | Fastest path to a working tool; hosting added later if wanted | — Pending |
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
*Last updated: 2026-06-16 after initialization*
