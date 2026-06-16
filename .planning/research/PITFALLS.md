# Pitfalls Research — F1 Race Winner Prediction

**Domain:** Formula 1 race winner probability prediction  
**Researched:** 2026-06-16  
**Overall confidence:** HIGH (data-source pitfalls), MEDIUM (modeling pitfalls), MEDIUM (live update pitfalls)

---

## Data Pitfalls

### Pitfall 1: OpenF1 Only Covers 2023 Onwards — 2022 Data Is a Gap

**What goes wrong:** The project plan assumes 2022–2025 training data, but the OpenF1 API's historical coverage begins in the 2023 season. Fetching data for 2022 via OpenF1 returns empty or missing results silently, leaving you with a training corpus that is one season smaller than expected — and the first season of the ground-effect era is the most important one for establishing baseline car/team behavior.

**Why it happens:** OpenF1 is built on F1's live-timing feed; it was not retroactively populated with pre-2023 telemetry.

**Consequences:** You either silently train on less data than planned, or spend significant unplanned time building a supplementary data pipeline for 2022.

**Prevention:** Supplement OpenF1 with FastF1 (which covers 2018 onward via the `fastf1` backend) for 2022 historical sessions. Treat OpenF1 as the live/real-time layer only; use FastF1 as the historical layer. Budget the dual-source integration work into the data phase.

**Warning signs:** API calls for meetings/sessions from the 2022 season key return empty arrays. Test this in Phase 1 before designing your schema around OpenF1 alone.

**Phase:** Address in Phase 1 (data collection). If discovered later, schema and pipeline changes cascade.

---

### Pitfall 2: Laps and Position Endpoints Require Per-Session or Per-Driver Filters

**What goes wrong:** Calling `GET /v1/laps` or `GET /v1/position` without a filter returns no data. You must query by `session_key` or `driver_number`. For a full historical dataset (2023–2025, ~23 races/year × 5 sessions), this means ~350+ separate API requests for lap data alone.

**Why it happens:** The OpenF1 API was designed for live session use, not bulk historical export. Unfiltered queries on high-volume tables are blocked server-side.

**Consequences:** Naive bulk-download scripts silently return empty results. A proper historical loader takes significantly more time to build and must handle rate limits (30 requests per 10 seconds per IP).

**Prevention:** Design the data ingestion loop around session-by-session fetching from day one. Cache all raw responses to SQLite immediately after fetch. Never re-fetch already-stored sessions. Build exponential backoff and retry into the HTTP layer.

**Warning signs:** Empty DataFrames on first lap/position fetch attempt without a filter. Catch this in a quick API spike before writing the full ETL.

**Phase:** Address in Phase 1 (data pipeline design).

---

### Pitfall 3: Grid Penalties Invalidate Qualifying Position as a Feature

**What goes wrong:** Qualifying position is the single strongest predictor of race outcome — but the actual race starting grid can differ significantly from qualifying results due to grid penalties applied after qualifying (power unit changes, gearbox changes, driving infringements). If the model uses the qualifying classification order as a feature without accounting for final grid, it trains on incorrect inputs.

**Why it happens:** Grid penalties are applied between qualifying and race start, sometimes on race morning after late repairs or steward decisions. OpenF1 provides a `starting_grid` endpoint, but the workflow to join qualifying times → penalty adjustments → actual grid is non-trivial.

**Consequences:** The model sees a driver "starting from pole" (qualifying result) when they actually started 5th (after penalties), causing systematic errors on those races.

**Prevention:** Always use the `starting_grid` endpoint (or equivalent post-penalty grid order) as the position feature, not the raw qualifying classification. Store both in the DB and join them. Validate that the grids differ in known penalty races.

**Warning signs:** Model performing worse on races where multiple grid penalties were issued. Cross-check 2023–2025 for known penalty events.

**Phase:** Address in Phase 2 (feature engineering). Easy to miss if you prototype quickly with qualifying data.

---

### Pitfall 4: Safety Car and Red Flag Events Are Structurally Absent from Training Data

**What goes wrong:** Safety car (SC) and virtual safety car (VSC) deployments reset race dynamics — they compress gaps, create pit window opportunities, and invert strategy. These events are available in the OpenF1 `race_control` endpoint, but their race-outcome impact is nonlinear and hard to encode as a simple feature. Models trained without this signal will produce overconfident predictions in chaotic races.

**Why it happens:** SC/VSC events are irregular, race-specific, and require forward-looking context (the SC compresses the field; the 5 laps after are dramatically different). Standard tabular features don't capture this state shift.

**Consequences:** During live updates, a safety car deployment will not shift win probabilities meaningfully because the model wasn't trained to recognize the state transition. The dashboard will appear broken to users who watch the SC come out and see probabilities barely move.

**Prevention:** Add a binary SC/VSC flag and a "laps since SC deployed" counter as live-update features. In the pre-race model, use historical SC frequency per circuit as a risk factor. Accept that SC events will always add irreducible uncertainty.

**Warning signs:** Validation races with multiple SC events showing worse log-loss than average. Check 2022 Abu Dhabi, 2023 Australian GP as known high-chaos examples.

**Phase:** Address in Phase 2 (feature engineering) and Phase 3 (live model).

---

### Pitfall 5: Weather Data Is Inconsistent Across Historical Sessions

**What goes wrong:** OpenF1's weather endpoint provides temperature, humidity, rainfall, wind speed, and track temperature — but coverage is patchy for historical sessions. Some sessions have no weather records at all; others have gaps mid-session. Filling missing values with zeros or averages silently corrupts wet-race training examples.

**Why it happens:** Weather data depends on circuit sensor availability and was not always captured in the upstream feed.

**Consequences:** Wet races (e.g., wet qualifying sessions) become indistinguishable from dry sessions in the training data, ruining one of the most predictive edge-case signals.

**Prevention:** Audit weather coverage per session in Phase 1. For sessions with missing weather, cross-reference with public weather services (Open-Meteo API is free) using circuit GPS coordinates and session timestamps to backfill. Flag weather-imputed sessions as a feature.

**Warning signs:** `weather` table has null rows for known wet events (Monaco 2023, Japanese GP 2022 typhoon, etc.).

**Phase:** Address in Phase 1 (data collection) and Phase 2 (feature engineering).

---

## Modeling Pitfalls

### Pitfall 1: Temporal Data Leakage via Random Train/Test Split

**What goes wrong:** Splitting race data randomly across train/validation/test sets allows future race information to bleed into past predictions. For example, a model might train on 2024 Australian GP data and test on 2023 Australian GP data — meaning the "future" informs the "past." The model appears to perform well during development but fails completely on genuinely unseen races.

**Why it happens:** Standard sklearn `train_test_split` shuffles by default. Sports analytics practitioners new to time-series modeling apply it without realizing temporal order matters.

**Consequences:** Inflated validation accuracy (sometimes 10–30% higher than true generalization). Model fails at race-time when it has never been tested on a truly held-out future period.

**Prevention:** Always split by time: train on 2023–2024, validate on early 2025, test on late 2025. Never use a race's data to both train and evaluate. Use walk-forward cross-validation: train on seasons 1–N, evaluate on season N+1, roll forward.

**Warning signs:** Validation accuracy suspiciously high (>70% winner accuracy). Model trained and tested on same-season data with random split.

**Phase:** Address in Phase 2 (model training). This is the highest-impact modeling mistake and the easiest to make silently.

---

### Pitfall 2: Class Imbalance — 20 Drivers, One Winner

**What goes wrong:** In any race, 1 of ~20 drivers wins. Without handling class imbalance, a binary "will this driver win?" classifier learns to predict "no" for everyone and achieves ~95% accuracy. The model appears to train, loss drops, but it predicts zero win probability for all drivers.

**Why it happens:** Binary classification per driver (one row per driver per race) gives a 1:19 positive-to-negative ratio. Standard cross-entropy loss doesn't penalize always-negative predictions heavily enough.

**Consequences:** Pre-race probabilities collapse to near-zero for most drivers and are meaningless for the distribution requirement.

**Prevention:** Frame as a multinomial distribution problem (softmax over all 20 drivers simultaneously) rather than 20 binary classifiers. Use log-loss (negative log likelihood) against the true winner as the loss function. Evaluate with Brier score across the distribution, not accuracy. If using binary models, apply class weights or oversample winners.

**Warning signs:** Sum of predicted probabilities across all drivers doesn't equal 1.0. All drivers showing <5% probability in the pre-race output.

**Phase:** Address in Phase 2 (model architecture decision). Get this right before any training.

---

### Pitfall 3: Dominant-Team Overfitting (Red Bull/Mercedes Memorization)

**What goes wrong:** Red Bull won 21 of 22 races in 2023 — nearly the entire training season. A model trained naively will learn "if car is Red Bull, predict win" rather than learning generalizable pace/position features. When the 2026 season rebalances (as regulation cycles do), the model collapses.

**Why it happens:** Constructor is one of the most predictive features historically, but it encodes regime-specific dominance rather than physical properties of pace. Cross-team performance is underrepresented in training data.

**Consequences:** The model correctly predicts the 2023 dominant-era races but fails to adapt when midfield cars improve or dominant teams have reliability failures (DNFs).

**Prevention:** Exclude or heavily regularize constructor-as-category features. Instead, use qualifying delta-to-fastest (a circuit-relative pace feature) rather than raw team identity. Stratify training data across seasons to balance representation. Evaluate model on 2022 (more competitive) and 2024 (Red Bull decline) as adversarial test sets.

**Warning signs:** Model heavily weights constructor or team features. Feature importance analysis shows "team" as top-3 predictor. Predictions are implausibly concentrated on one team.

**Phase:** Address in Phase 2 (feature selection). Run feature importance analysis before finalizing model.

---

### Pitfall 4: Using Post-Race Features as Model Inputs (Target Leakage)

**What goes wrong:** Features like "final race position," "fastest lap," "number of pit stops taken," or "total race time" are only known after the race ends. Including them in the training feature set causes massive target leakage — the model has access to information that would require knowing the outcome in advance.

**Why it happens:** When joining tables during feature engineering, it is easy to accidentally pull from the race result table rather than pre-race or mid-race state.

**Consequences:** Model accuracy on training data is near-perfect; it fails completely at prediction time because post-race features are unavailable.

**Prevention:** Maintain strict feature categories: (a) pre-race features (qualifying, practice, standings, historical) and (b) in-race features available at the current lap number. Never join result tables when constructing feature vectors for a lap-N prediction. Build a "feature horizon" test: can this value be known at prediction time?

**Warning signs:** Training log-loss suspiciously near zero. Feature list includes "race_position" or "fastest_lap_time."

**Phase:** Address in Phase 2 (feature engineering). Review every feature with the question "could we know this before the event we're predicting?"

---

### Pitfall 5: Probability Calibration Not Validated

**What goes wrong:** A model can correctly rank drivers by win probability (good discrimination) but output wildly miscalibrated probabilities — e.g., predicting 40% for a driver who actually wins 10% of the time when predicted at that confidence level. For a dashboard showing win probabilities to users, miscalibration destroys credibility even if ranking order is correct.

**Why it happens:** Gradient boosting and random forest models are known to produce uncalibrated probability estimates from their default predict_proba output. Calibration is rarely validated in hobby projects.

**Consequences:** Dashboard shows a driver at "45% win probability" before a race they win 8% of the time in reality. Users lose trust rapidly.

**Prevention:** Apply isotonic regression or Platt scaling post-training. Evaluate calibration with reliability diagrams and expected calibration error (ECE). Evaluate prediction quality with the Brier score across the 20-driver distribution, not just accuracy. Use a proper scoring rule (log-loss) as the training objective.

**Warning signs:** Reliability diagram shows systematic over- or under-confidence. Predicted max probability is consistently >50% when historical winner base rate is ~5%.

**Phase:** Address in Phase 2 (model evaluation). Add calibration check to your training pipeline before moving to live use.

---

## Live Update Pitfalls

### Pitfall 1: Confusing "3-Second API Lag" with "Instant Model Updates"

**What goes wrong:** OpenF1 real-time data arrives with approximately 3 seconds of latency relative to the live event. Projects assume this means the prediction system updates every 3 seconds. In reality, model inference takes additional time (data fetch → feature construction → model.predict → dashboard push), and if this pipeline blocks, predictions become multiple laps stale before updating.

**Why it happens:** Developers prototype the pipeline synchronously (fetch → predict → display), which works fine in testing but accumulates latency under race conditions with 20 drivers × per-lap data points.

**Consequences:** During a fast phase of racing (multiple position changes per lap), the dashboard shows probabilities that are 2–4 laps stale. Safety car decisions or pit stops have already resolved before the model reflects them.

**Prevention:** Decouple the data fetch, prediction, and display into separate processes with a shared state store (SQLite or in-memory). Use a background poller on a fixed 4-second tick to fetch new data. Run model inference asynchronously on new data arrival; the dashboard reads the latest computed probabilities independently. Never block the UI update loop on model inference.

**Warning signs:** Dashboard freezes for several seconds during busy laps. Profiling shows model.predict() taking >1 second.

**Phase:** Address in Phase 3 (live update architecture). Design the async separation before writing any inference code.

---

### Pitfall 2: Cold Start — No Prediction at Race Start

**What goes wrong:** The model is designed to use in-race features (gap to leader, tire age, lap number) to update predictions. At lap 1 before any meaningful race data has arrived, these features are all zero or null. The model produces either an error or a degenerate output, leaving the dashboard blank at the most exciting moment of the race.

**Why it happens:** A single model trained on mid-race features has no useful signal for lap 1. The model architecture doesn't account for the pre-race → early-race → mid-race state transitions.

**Consequences:** Dashboard shows "loading" or zeros at lights out, which is the exact moment users want predictions.

**Prevention:** Maintain a separate pre-race probability distribution (from qualifying + practice features, computed before lights out) as the fallback. Blend from the pre-race distribution toward the in-race model outputs over the first 5–10 laps as in-race features stabilize. This is the "warm start" pattern: display pre-race probabilities at lap 0, transition to in-race model after lap 5.

**Warning signs:** Lap 1 predictions show equal probability across all 20 drivers (uniform distribution) or crash with NaN.

**Phase:** Address in Phase 3 (live model design). The pre-race model (Phase 2) should be designed with the handoff point in mind.

---

### Pitfall 3: Retirement/DNF Events Not Zeroing Win Probability

**What goes wrong:** When a driver retires mid-race (engine failure, crash, etc.), the OpenF1 feed stops returning position data for that driver. If the live update system only updates drivers with new data and doesn't actively process the absence of data, retired drivers may retain stale win probabilities — sometimes showing 20%+ long after they've parked the car.

**Why it happens:** Polling-based systems check "what changed" rather than "what is the current state of all drivers." A missing lap record is not the same as a zero-probability event to a naive pipeline.

**Consequences:** Users see a parked car on TV while the dashboard shows the driver as a likely winner. Dashboard credibility collapses immediately.

**Prevention:** At each prediction cycle, check the driver status via the race control endpoint or by detecting a gap in lap data greater than 1 lap. If a driver is flagged as retired (DNF), set their win probability to 0.0 immediately and renormalize the remaining distribution to sum to 1.0. Track driver status explicitly in your state model.

**Warning signs:** Mid-race probabilities for a driver who visibly retired (from public F1 commentary) remain non-zero. Test by simulating a missing lap sequence in development.

**Phase:** Address in Phase 3 (live state management). Add a driver-status check as a mandatory step in the prediction loop.

---

### Pitfall 4: API Rate Limit Exhaustion During Race

**What goes wrong:** During a live race, the system polls multiple endpoints simultaneously — laps, intervals, positions, car data, pit stops, race control, weather — creating N-endpoint × M-driver requests per polling cycle. With 30 requests per 10 seconds per IP (OpenF1 free tier), a naive implementation hits the rate limit within minutes of race start, causing 429 errors and silent data gaps.

**Why it happens:** Developers prototype polling each endpoint individually without counting total requests per second. During a busy lap with multiple pit stops, additional endpoints are triggered ad-hoc, spiking the request rate.

**Consequences:** The live prediction system goes dark for periods during the race. Predictions freeze or error. In the worst case, the entire polling loop crashes.

**Prevention:** Budget your request rate before writing a single polling call. Map each endpoint to its required update frequency (intervals/positions need every 4s; weather can be every 60s). Batch all necessary fetches into a single coordinated polling cycle. Build a token-bucket rate limiter around the HTTP client. Cache responses and only re-fetch when the session is known to be live.

**Warning signs:** HTTP 429 responses in logs during race simulation. Request count per 10-second window exceeds 25 (leave headroom).

**Phase:** Address in Phase 3 (data poller design). Rate-limit compliance must be designed in, not bolted on.

---

### Pitfall 5: Lap Data Arriving Out of Order

**What goes wrong:** OpenF1 real-time data occasionally delivers lap records out of chronological order, or delivers the same lap's data multiple times (duplicate events). A pipeline that appends new records without deduplication will train the live model on doubled or misordered features, producing probability spikes or crashes.

**Why it happens:** The underlying F1 timing feed uses a broadcast protocol where retransmission can cause duplicates. OpenF1 does not guarantee strict ordering for live data.

**Consequences:** Lap N features contain data from lap N+1 (order inversion), causing the live model to predict as if one more lap has elapsed than actually has. Or duplicate records cause feature averages to be computed on repeated data points.

**Prevention:** Use `session_key` + `driver_number` + `lap_number` as a composite primary key in the DB. `INSERT OR IGNORE` or `ON CONFLICT DO NOTHING` for all lap records. Sort all feature construction windows by lap_number explicitly. Never assume API response order equals chronological order.

**Warning signs:** Lap count in DB is higher than expected. Position-deltas feature shows implausible swings (driver jumps from 1st to 15th and back in one lap).

**Phase:** Address in Phase 1 (DB schema design) and Phase 3 (live poller).

---

## Dashboard/UX Pitfalls

### Pitfall 1: Treating Win Probabilities as Certainties

**What goes wrong:** A dashboard showing "Hamilton: 47%" is read by users as a near-certainty, especially when the nearest competitor shows 23%. Without context that this is a probability distribution (not a forecast), users will be confused or frustrated when the "favorite" doesn't win.

**Why it happens:** Bar charts with no uncertainty bands or confidence intervals invite point-estimate thinking. The label "Win Probability" is not self-explanatory about its distributional nature.

**Consequences:** Users interpret the model as "wrong" when the lower-probability driver wins, rather than understanding that a 47% prediction means the driver wins less than half the time by design.

**Prevention:** Always display the full distribution across all 20 drivers (not just top 5). Avoid calling out a single "predicted winner." Include a note that probabilities sum to 100%. Consider showing probability ranges or confidence bands if the model supports them. Design the update animation to show smooth transitions rather than jumps, which communicate the continuous nature of probability.

**Warning signs:** Dashboard designs that rank one driver prominently as "the predicted winner" or use language like "Model predicts X wins."

**Phase:** Address in Phase 4 (dashboard design). Fix the copy and labels before any user testing.

---

### Pitfall 2: Dashboard Update Frequency Causes Seizure-Risk Flicker

**What goes wrong:** Updating the bar chart every 4 seconds with animated transitions causes constant visible motion during the entire race. If the model outputs fluctuate significantly lap-to-lap (due to noisy in-race features), the bars jump rapidly, making the display unreadable and visually exhausting.

**Why it happens:** Raw model output is noisy; probabilities oscillate by 5–15 percentage points between consecutive laps even when nothing meaningful happens. Dashboard renders every model output directly.

**Consequences:** Dashboard is unusable during the race because the chart never settles. Users focus on the animation rather than the information.

**Prevention:** Apply exponential moving average (EMA) smoothing to probability outputs before display — weight the current prediction at 30% and the previous display value at 70%. Only trigger a full chart redraw when any driver's probability changes by more than a threshold (e.g., 3 percentage points). Show a "last updated" timestamp so users know the data is live without constant visual churn.

**Warning signs:** During race simulation, probabilities oscillate by >10pp on consecutive laps with no major events. Subjective review: can you watch 10 seconds of the dashboard without eye strain?

**Phase:** Address in Phase 4 (dashboard implementation). Add smoothing before any user testing.

---

### Pitfall 3: Race Position vs. Win Probability Disconnect

**What goes wrong:** A driver leading in lap 40 of 57 has high win probability. But a driver in 3rd with 2-lap-fresher tires and the fastest car has meaningful win probability too. If the dashboard only shows current position (or heavily weights it), it looks identical to a race position tracker — not a predictive system. The value proposition disappears.

**Why it happens:** In-race features are dominated by current gap to leader (which strongly correlates with current position). Models that over-index on gap produce probabilities nearly identical to the inverse of current position.

**Consequences:** Users ask "why is this different from the official F1 timing screen?" The differentiating value of ML prediction is lost.

**Prevention:** Validate that your probability output differs from a naively position-based heuristic. Check: does a driver 2 seconds behind with fresh tires show meaningfully higher probability than a driver 2 seconds behind with worn tires? If not, tyre age and compound features are not contributing. Specifically test scenarios where tyre advantage should cause a probability inversion.

**Warning signs:** Rank correlation between win probability output and current race position is >.95 consistently.

**Phase:** Address in Phase 2 (feature validation) and check again in Phase 3 (live model integration).

---

## Prevention Summary

**Top 5 things to get right from day 1:**

1. **Dual data source architecture from the start.** OpenF1 is the live layer (2023+); FastF1 is the historical layer (2022+). Design your schema and ETL to ingest from both. Do not discover the 2022 gap mid-project.

2. **Temporal train/test split — never random.** All validation must be on races chronologically after the training window. Use 2023–2024 for training, 2025 for testing. Any random split invalidates your evaluation metrics.

3. **Multinomial framing, not 20 binary classifiers.** Model win probability as a distribution (one softmax output over all 20 drivers) from the start. This solves class imbalance, guarantees probabilities sum to 1, and makes calibration tractable.

4. **Async live pipeline with explicit driver state tracking.** Decouple polling, inference, and display. Track driver status (active/retired) explicitly and zero-out retired drivers in real time. Do not discover the DNF bug during a live race.

5. **Grid starting position from `starting_grid`, not qualifying classification.** Always use the post-penalty actual grid. Audit for known penalty events in 2023–2025 to validate your data join is correct before any model training begins.

---

## Phase-Specific Warning Index

| Phase Topic | Likely Pitfall | Mitigation |
|---|---|---|
| Phase 1: Data collection | OpenF1 only covers 2023+; 2022 silently missing | Add FastF1 as supplementary source |
| Phase 1: Data collection | Laps/positions endpoint requires per-session filter | Design session-loop ETL from the start |
| Phase 1: Data collection | Weather data patchy for historical sessions | Audit coverage; backfill with Open-Meteo |
| Phase 1: DB schema | Duplicate/out-of-order lap records from live feed | Composite PK + INSERT OR IGNORE |
| Phase 2: Feature engineering | Grid penalties invalidate qualifying position feature | Use `starting_grid` endpoint, not qualifying order |
| Phase 2: Feature engineering | Post-race features accidentally included (target leakage) | Feature horizon audit on every column |
| Phase 2: Model architecture | Binary classifiers per driver → class imbalance collapse | Multinomial softmax from the start |
| Phase 2: Model training | Random train/test split → temporal leakage | Time-ordered split; walk-forward CV |
| Phase 2: Model evaluation | Calibration not checked | Brier score + reliability diagram |
| Phase 2: Feature selection | Constructor identity dominates → team overfitting | Use pace-delta features; regularize team encoding |
| Phase 3: Live pipeline | Synchronous fetch-predict-display → stale dashboard | Async separation; background poller |
| Phase 3: Live pipeline | No predictions at lap 0 (cold start) | Pre-race model as fallback; blend over first 5 laps |
| Phase 3: Live pipeline | Retired driver retains non-zero win probability | Explicit driver status tracking; zero on DNF |
| Phase 3: Live pipeline | Rate limit exhaustion during race | Budget requests; coordinated polling cycle |
| Phase 4: Dashboard | Probabilities read as certainties | Show full 20-driver distribution; avoid "predicted winner" language |
| Phase 4: Dashboard | Constant animation → unreadable display | EMA smoothing; change-threshold rendering |
| Phase 4: Dashboard | Win probability ≈ race position → no predictive value | Validate tyre-advantage probability inversions before shipping |

---

## Sources

- [OpenF1 API Documentation](https://openf1.org/docs/) — endpoint reference, data coverage, rate limits
- [OpenF1 GitHub — API Usage Discussion #148](https://github.com/br-g/openf1/discussions/148) — laps/position endpoint filter requirement
- [The Use of Machine Learning in Predicting Formula 1 Race Outcomes — Preprints.org](https://www.preprints.org/manuscript/202504.1471) — class imbalance, feature importance findings
- [Data-driven pit stop decision support for Formula 1 — Frontiers/PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC12626961/) — feature engineering for in-race decisions
- [Ergast API deprecation discussion — FastF1 GitHub](https://github.com/theOehrly/Fast-F1/discussions/445) — FastF1 backend options for 2022 data
- [FastF1 Data Reference](https://docs.fastf1.dev/data_reference/index.html) — 2018+ coverage confirmation
- [Seven Common Causes of Data Leakage — Towards Data Science](https://towardsdatascience.com/seven-common-causes-of-data-leakage-in-machine-learning-75f8a6243ea5/) — leakage taxonomy
- [Train-Test Split Strategies for Time Series Data — APXML](https://apxml.com/courses/time-series-analysis-forecasting/chapter-6-model-evaluation-selection/train-test-split-time-series) — walk-forward validation
- [Underrated Stat: Why Tire Wear Is More Predictive Than Qualifying — Medium](https://medium.com/formula-one-forever/underrated-stat-why-tire-wear-is-more-predictive-than-qualifying-c0cbf9f31231) — tyre as predictor vs qualifying
- [Probability Calibration — scikit-learn docs](https://scikit-learn.org/stable/auto_examples/calibration/plot_calibration_curve.html) — calibration curves and Brier score
- [I built a real-time F1 race replay and live timing app — DEV Community](https://dev.to/waruna/i-built-a-real-time-f1-race-replay-and-live-timing-app-heres-how-4ph) — real-time data architecture
- [How F1 Sets the Starting Grid: Qualifying, Sprints, and Penalties — Race Sundays](https://racesundays.com/f1-explained/sport-rules/how-f1-sets-the-starting-grid-qualifying-sprints-and-penalties) — grid penalty impact
