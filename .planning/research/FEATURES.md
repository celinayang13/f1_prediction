# Features Research — F1 Race Winner Prediction

**Researched:** 2026-06-16
**Domain:** Live sports prediction / motorsport ML
**Scope:** Aligned with REQUIREMENTS.md — pre-race and in-race win probability distribution; winner only (not full grid)

---

## Most Predictive ML Input Features

Ranked by empirical importance across published studies. Confidence levels based on agreement across multiple sources.

### Tier 1 — Dominant Predictors (use always)

| Feature | Predictive Power | Notes |
|---------|-----------------|-------|
| **Qualifying gap to pole** | Very High | Correlation with race finish ~0.75 (Spearman). Removing this drops accuracy from 78% to 67% in podium models. Single strongest pre-race signal. |
| **Grid position (qualified)** | Very High | Strongly correlated with finish; drivers starting P1-P5 disproportionately finish P1-P5. Captures grid-vs-pole gap plus strategy call implications. |
| **Current race position (in-race)** | Very High | After lap 5+, current position is the most actionable in-race state. Strongly correlated with final position especially post-lap-10. |
| **Gap to race leader (in-race)** | High | OpenF1 `intervals` endpoint provides this ~every 4 seconds. Determines overtaking feasibility and undercut potential. |

### Tier 2 — Strong Supporting Predictors (include in v1)

| Feature | Predictive Power | Notes |
|---------|-----------------|-------|
| **Tyre compound** | High | Soft/Medium/Hard creates meaningful pace delta; critical for stint modeling. OpenF1 `stints` endpoint. |
| **Tyre age (laps on current compound)** | High | Degradation is nonlinear; laps-on-tyre is the primary degradation proxy available without telemetry. |
| **FP2 average lap time** | High | Best proxy for race pace. FP2 uses race-simulation compounds and fuel loads. More predictive than FP1 or FP3. |
| **Constructor / team** | High | Constructor captures car performance ceiling. In dominant-team eras (Verstappen 2023), this is near-deterministic. |
| **Championship standings (driver)** | Medium-High | Correlates with recent form and car development trajectory. Rolling 5-race window more predictive than season total. |

### Tier 3 — Useful Contextual Signals (include where available)

| Feature | Predictive Power | Notes |
|---------|-----------------|-------|
| **Circuit-specific win/podium history (driver)** | Medium | Hamilton at Silverstone (7 wins), Verstappen at Austria (5 wins). Track specialty is real but sample-size constrained with 2022+ data only. |
| **Pit stop history in current race** | Medium | How many stops taken, whether undercut window has passed, competitors' stop status. Critical for lap 20-50 predictions. |
| **Laps remaining** | Medium | Modifies all other signals — a 10-second gap with 5 laps left is not closeable; same gap with 30 laps left is. |
| **Race progress fraction** | Medium | `laps_completed / total_laps`. Normalizes across different-length circuits. |
| **FP2 best lap time** | Medium | Peak pace indicator. Less predictive than average (which reflects racecraft), but useful combined feature. |
| **Constructor championship standings** | Medium | Broader team development trajectory; sometimes more stable than driver-specific form. |

### Tier 4 — Include if OpenF1 Makes it Easy (nice-to-have for v1)

| Feature | Predictive Power | Notes |
|---------|-----------------|-------|
| **Weather (wet/dry/mixed)** | Situationally High | Weather inverts expected order dramatically, but >90% of races are dry. Model should accept weather flag and degrade gracefully. OpenF1 `weather` endpoint covers this. |
| **Track status / safety car** | Situationally High | VSC and SC compress gaps and enable free pit stops — major win-probability shifts. OpenF1 `race_control` endpoint. |
| **Stint number** | Low-Medium | Proxy for strategy phase (1-stop vs 2-stop). Correlates with tyre age but adds context on planned strategy. |
| **Delta lap time (vs previous lap)** | Low-Medium | Trend signal for degradation detection; noisy lap-to-lap but useful over rolling 3-5 lap window. |

### Explicitly Low-Value Features (avoid or defer)

| Feature | Why Weak | Alternative |
|---------|---------|------------|
| **FP1 lap times** | Not on race compounds, drivers often run setup experiments not pace runs | Use FP2 instead |
| **Driver nationality** | Spurious correlation (pre-2022 models included this) | Drop entirely |
| **Driver age** | Confounded by car quality in F1 much more than other sports | Drop entirely |
| **Raw qualifying lap time (seconds)** | Circuit-specific; only useful as gap-to-pole delta | Use qualifying gap to pole instead |
| **Telemetry (speed, throttle, braking)** | Too granular for race-level prediction; adds noise | Requirements.md already scopes this out |
| **Pre-2022 historical performance** | Different car regulations (pre-ground-effect era) create distribution shift | Stick to 2022+ per project constraint |

---

## Table Stakes — Model Inputs

These are the minimum credible feature set. A model missing any of these will produce demonstrably worse predictions.

**Pre-race model minimum:**
- Qualifying gap to pole (driver, session)
- Grid position
- Constructor identity (team encoding)
- FP2 average lap time (or gap to session best)
- Driver rolling form (last 5 races finish position average)
- Circuit identity (enables circuit-specific priors)

**In-race model minimum:**
- Current race position
- Gap to race leader (seconds)
- Tyre compound (current)
- Tyre age (laps on current compound)
- Laps remaining
- Pit stops completed (count)
- Race progress fraction

**OpenF1 availability check:**
- Qualifying position: available via `session_result` — but OpenF1 historical data only goes back to 2023. For 2022 data, supplement with Jolpica (Ergast-compatible replacement API, free, covers 2018+).
- Stints (tyre compound + age): `stints` endpoint, 2023+
- Intervals (gap to leader): `intervals` endpoint, 2023+
- Weather: `weather` endpoint, 2023+
- Race control (safety car): `race_control` endpoint, 2023+

**Data gap implication:** Training set is effectively 2023–2025 from OpenF1 (not 2022–2025 as stated in requirements). Either supplement with Jolpica for 2022 qualifying/race results (no telemetry), or accept 3-season training window and note reduced sample size.

---

## Table Stakes — Dashboard

Must-have for the dashboard to be usable during a live race.

| Feature | Why Required | Implementation Note |
|---------|-------------|-------------------|
| **Driver ranking by win probability** | Core output — the whole point of the tool | Ordered bar chart, highest prob at top |
| **Win probability as percentage** | Probabilities sum to 1; display as % for readability | Round to 1 decimal; don't show 0.0% for backmarkers, use `< 1%` |
| **Current race position alongside probability** | Context; a P3 driver with 35% win prob needs their position shown | Fetch from `position` endpoint each update |
| **Tyre compound indicator** | Explains probability shifts; users will ask "why did this change?" | Show compound color coding (soft=red, medium=yellow, hard=white) |
| **Last-updated lap counter** | Users need to know if predictions are stale | Show "Last updated: Lap 23" |
| **Pre-race vs in-race mode toggle** | Requirements DASH-02/DASH-03 explicitly require both tabs | Two states: qualifying-based preview and live race |
| **Auto-refresh** | Race updates every ~90 seconds per lap; dashboard must poll | Client-side poll every 15-30 seconds during race window |

---

## Differentiators (v2+)

These are genuinely interesting and add value but are explicitly out of scope for v1 (per REQUIREMENTS.md v2 section and project constraints).

| Feature | Value | Complexity | When to Add |
|---------|-------|-----------|-------------|
| **Probability history trend lines** | Shows how race evolved — narrative value | Medium | Already in REQUIREMENTS.md DASH-V2-01 |
| **Pit stop annotations on timeline** | Explains why probabilities shifted | Medium | DASH-V2-03; requires event overlay logic |
| **Gap-to-leader delta on dashboard** | Adds race context without full telemetry | Low | Easy add after in-race model works |
| **Podium (top-3) probability** | More nuanced than winner-only; covers "likely to contend" | Medium | REQUIREMENTS.md EXT-02 |
| **Sprint race predictions** | Different strategy game — short race, minimal pit stops | High | REQUIREMENTS.md EXT-01 |
| **Undercut/overcut probability** | Predicts whether a pit strategy will gain or lose positions | High | Requires deep stint modeling |
| **DNF risk signal** | Reliability signal that reduces all probabilities for fragile cars | Medium | Requires historical mechanical failure data |
| **Safety car probability** | Situation-aware: some circuits (Monaco, Baku) have high SC rates | Medium | Can be circuit-prior, not live prediction |

---

## Anti-Features for v1

Explicitly avoid these to keep v1 focused and deliverable.

| Anti-Feature | Why Avoid | What to Do Instead |
|-------------|-----------|-------------------|
| **Full grid position prediction (1–20)** | Combinatorially complex; much harder than winner probability | Already scoped out in PROJECT.md |
| **Betting odds integration** | Changes the product purpose; brings regulatory/ethical complexity | Never — stated out of scope |
| **Driver photo/avatar display** | Visual polish with high asset-management overhead | Add only if dashboard otherwise feels bare |
| **Circuit map visualization** | Nice but irrelevant to prediction; significant front-end work | Defer; a static circuit image is sufficient if anything |
| **Push notifications / alerts** | Requires notification infrastructure; overkill for local tool | The auto-refresh dashboard is sufficient |
| **Historical race replay** | Interesting but orthogonal to live prediction; doubles DB complexity | Separate tool if ever wanted |
| **Multi-user / auth layer** | Local-first is the constraint; auth adds deployment complexity | Defer to hosting phase (REQUIREMENTS.md HOST-01) |
| **Social / sharing features** | Prediction sharing or leaderboards | Never for v1 |
| **Model explainability UI** | SHAP values per driver per lap are computationally expensive | Log feature importances to file; don't surface in dashboard |
| **Lap-by-lap telemetry replay** | Already scoped out; adds nothing for winner probability | Out of scope per requirements |

---

## Feature Engineering Notes

These computed features appear consistently in high-performing models and should be derived from raw data during preprocessing:

- **Qualifying gap to pole** = `(driver_qualifying_time - pole_qualifying_time) / pole_qualifying_time` — normalizes across circuits.
- **Driver recent form** = rolling 5-race average of `finish_position / grid_size` — normalizes across field sizes.
- **Circuit-specific prior** = driver's historical average finish rank at this circuit (2023+ only; small sample, use with prior smoothing).
- **Tyre degradation proxy** = `current_lap_time - driver_median_lap_time_this_stint` — noisy but available without telemetry model.
- **Race progress** = `lap_number / total_laps` — 0.0 to 1.0 scalar, improves in-race model calibration across different-length tracks.

---

## Sources

- [Formula 1 Race Predictor (Veronica Nigro, TDS)](https://medium.com/data-science/formula-1-race-predictor-5d4bfae887da) — Neural network model, grid position as top feature, 62% winner accuracy on 2019 season
- [Predicting F1 Podiums with 78% Accuracy (Polishchuk)](https://ipolishchuk22.medium.com/predicting-f1-podiums-with-78-accuracy-using-machine-learning-and-real-race-data-0de3bcd6d2c4) — Qualifying position, FP2 avg/best lap, FP2 laps as top 5 features; removing qualifying drops accuracy 11 points
- [CS 334: Predicting F1 Race Winners 2018–2024 (Bodla)](https://medium.com/@qutub649/cs-334-final-project-predicting-the-winner-of-a-formula-1-race-2018-2024-using-machine-learning-872c9a904a6a) — Qualifying gap and teammate-relative performance as strongest features; 0.75 qualifying-finish correlation
- [Data-driven pit stop decision (Frontiers/PMC)](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2025.1673148/full) — Bi-LSTM model; tyre compound, tyre age, lap time delta, track status as key in-race features
- [The Use of ML in Predicting F1 Race Outcomes (Preprints.org 2025)](https://www.preprints.org/manuscript/202504.1471) — TabNet model; grid position, constructor, laps, overtakes as primary features
- [OpenF1 API Documentation](https://openf1.org/docs/) — Endpoint coverage: stints, intervals, race_control, weather, laps, pit stops; historical data from 2023 onwards (free, no auth)
- [Underrated Stat: Why Tire Wear Is More Predictive Than Qualifying (Medium)](https://medium.com/formula-one-forever/underrated-stat-why-tire-wear-is-more-predictive-than-qualifying-c0cbf9f31231) — In-race tyre state as dominant late-race predictor
- [Jolpica F1 API (Ergast replacement)](https://github.com/arkraieski/formula1data) — Ergast-compatible, covers 2018+, enables 2022 qualifying and race result data
