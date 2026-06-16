# Requirements: F1 Race Winner Prediction

**Defined:** 2026-06-16
**Core Value:** Show accurate, live-updating win probabilities for every driver during an F1 race weekend

## v1 Requirements

### Data Layer

- [ ] **DATA-01**: System fetches and stores all historical F1 race, qualifying, and practice sessions from OpenF1 API for the 2022–2025 seasons
- [ ] **DATA-02**: System stores weather conditions (wet/dry/mixed) per session
- [ ] **DATA-03**: System polls OpenF1 live during a race weekend and stores lap-by-lap position, interval, and lap time data
- [ ] **DATA-04**: System stores tyre compound and age per driver per stint (required for in-race model)

### Pre-race Model

- [ ] **PRERACE-01**: A pre-race model is trained on qualifying gap to pole, practice lap times, circuit-specific win history, constructor performance, and championship standings
- [ ] **PRERACE-02**: Pre-race model outputs a win probability distribution across all ~20 drivers (probabilities sum to 1)
- [ ] **PRERACE-03**: Pre-race probabilities are computed and available before lights out, using qualifying session data as primary input

### In-race Model

- [ ] **INRACE-01**: A separate in-race model is trained on mid-race states — current positions, gaps to leader, laps remaining, tyre compound and age, and pit stop history
- [ ] **INRACE-02**: In-race model re-runs after each completed lap and outputs an updated win probability distribution
- [ ] **INRACE-03**: In-race model incorporates live pit stop data (when drivers pit, tyre change)

### Dashboard

- [ ] **DASH-01**: Dashboard displays a live win probability bar chart with all drivers ranked by their current win probability
- [ ] **DASH-02**: Dashboard has a pre-race tab showing qualifying-based win probabilities before the race starts
- [ ] **DASH-03**: Dashboard has an in-race tab that auto-refreshes each lap showing live-updating probabilities
- [ ] **DASH-04**: Dashboard runs locally at localhost and requires no authentication

## v2 Requirements

### Dashboard Enhancements

- **DASH-V2-01**: Probability history trend lines showing how each driver's win probability evolved through the race
- **DASH-V2-02**: Driver info overlays showing current position, gap to leader, and tyre info alongside win probability
- **DASH-V2-03**: Race event annotations (safety car, pit stops) marked on the timeline

### Hosting

- **HOST-01**: Dashboard deployed to a simple hosted environment accessible to a small group during race weekends

### Extended Predictions

- **EXT-01**: Sprint race winner predictions (separate model for sprint format weekends)
- **EXT-02**: Top-3 (podium) probability distribution in addition to race winner

## Out of Scope

| Feature | Reason |
|---------|--------|
| Full grid position prediction (1–20) | Much higher complexity; winner probability is sufficient for v1 |
| Public hosting / multi-user access | Local-first; hosting deferred to after the model is validated |
| Fantasy F1 / betting integration | Not the purpose of this project |
| Pre-2022 historical data | Different car regulations make data less predictive for current grid |
| Qualifying outcome prediction | Race winner is the target; qualifying is an input, not a prediction |
| Real-time telemetry (speed, throttle, braking) | Too granular; lap-level data is sufficient for winner prediction |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| DATA-01 | Phase 1 | Pending |
| DATA-02 | Phase 1 | Pending |
| DATA-03 | Phase 1 | Pending |
| DATA-04 | Phase 1 | Pending |
| PRERACE-01 | Phase 2 | Pending |
| PRERACE-02 | Phase 2 | Pending |
| PRERACE-03 | Phase 2 | Pending |
| INRACE-01 | Phase 2 | Pending |
| INRACE-02 | Phase 2 | Pending |
| INRACE-03 | Phase 2 | Pending |
| DASH-01 | Phase 3 | Pending |
| DASH-02 | Phase 3 | Pending |
| DASH-03 | Phase 3 | Pending |
| DASH-04 | Phase 3 | Pending |

**Coverage:**
- v1 requirements: 14 total
- Mapped to phases: 14 (Phases 1–3)
- Unmapped: 0 ✓

Note: Phase 4 (Live Race Integration) is a system validation phase. All v1 requirements are satisfied by Phases 1–3; Phase 4 exercises the end-to-end runtime path that cannot be tested with historical data alone.

---
*Requirements defined: 2026-06-16*
*Last updated: 2026-06-16 — traceability mapped after roadmap creation*
