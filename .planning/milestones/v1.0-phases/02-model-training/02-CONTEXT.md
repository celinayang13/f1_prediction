# Phase 2: Model Training - Context

**Gathered:** 2026-06-24
**Status:** Ready for planning

<domain>
## Phase Boundary

Train two XGBoost models (pre-race and in-race) on Phase 1's feature tables and serialize them to disk. Phase 2 begins only after a full 2023–2025 backfill is complete (see D-01). The output is two calibrated model artifacts in `data/models/` that Phase 3 will load for inference.

**Not in scope:** inference loop, live polling, dashboard, real-time prediction updates.

</domain>

<decisions>
## Implementation Decisions

### Backfill Gate (required before training starts)

- **D-01:** Phase 2 is gated on completing a full 2023–2025 backfill. The current DB has only 1 race per year for 2023–2025 (20 rows each). The first plan in Phase 2 must extend the backfill to all available 2023–2025 races and re-run the feature builder before any model training code is written. This is a Phase 1 rework task that lives in Phase 2's plan as Plan 01.

### Feature Set

- **D-02 [informational]:** Drop `team_name`, `circuit_avg_finish`, and `constructor_pace_delta` from the pre-race training feature set. All three are 100% NULL in the current DB. They add no signal and including them would mislead the model. Re-evaluate after the full backfill — `circuit_avg_finish` and `constructor_pace_delta` should become computable once 2022 data exists to feed 2023+ lookback windows. *(Implicitly enforced by PRERACE_FEATURE_COLS in Plan 02-02; no explicit plan citation required.)*

- **D-03:** The pre-race feature set for training is: `qualifying_delta_to_pole`, `practice_pace_fp2`, `practice_pace_fp3`, `circuit_win_rate`, `championship_position`, `grid_position`, `driver_seasons_in_f1`. Target: `is_winner = (finish_position == 1)` derived at training time.

- **D-04:** The in-race feature set matches Phase 1 D-03: `gap_to_leader`, `tyre_compound`, `tyre_age_laps`, `pit_stop_count`, `position`, `interval_ahead`, `interval_behind`, `pit_stop_delta`, `lap_number`, `laps_remaining`. Target: `is_winner` inherited from the pre-race label for the same (session_key, driver_number).

- **D-05:** `circuit_win_rate` is 87% NULL (drivers with no prior win at the circuit). XGBoost's native NULL handling (learned default split direction) is acceptable here — it has real non-NULL signal for experienced drivers. Leave it in; do not impute.

### Model Framework

- **D-06 (Claude's discretion):** Use XGBoost as specified in ROADMAP.md. CLAUDE.md recommends LightGBM for categorical support, but `team_name` is being dropped (D-02) and the remaining categoricals (`tyre_compound`) can be one-hot encoded. XGBoost is consistent with the roadmap and the user has not requested a change.

### Model Evaluation

- **D-07 (Claude's discretion):** Use `TimeSeriesSplit` with season-based folds (train on earlier seasons, validate on later). A held-out 2024 race set is the ROADMAP success criterion validation target. Brier score and log loss are appropriate metrics for calibrated probability output. No numerical threshold is required for Phase 2 — the success criterion is a valid probability distribution (sums to 1.0 ± 0.001), not a minimum accuracy target.

### Calibration

- **D-08 (Claude's discretion):** Apply `CalibratedClassifierCV` with isotonic regression over XGBoost's raw probability scores, then normalize per-race with `scipy.special.softmax` to enforce the sum-to-1 constraint across all drivers in the same race. Do not use Platt scaling — isotonic handles the asymmetric calibration errors typical in imbalanced classification better.

### Training Script Structure

- **D-09 (Claude's discretion):** Single script `scripts/train.py` with a `--model prerace|inrace` flag. Both models share the same training infrastructure (CV, calibration, artifact saving). No hyperparameter search for v1 — use sensible defaults (n_estimators=500, learning_rate=0.05, max_depth=6, scale_pos_weight=(n_neg/n_pos)).

  **OVERRIDE (2026-06-24 — planning revision):** The original D-09 listed `is_unbalance=true` as a default. This is a **LightGBM-only parameter** and is **silently ignored by XGBoost** (RESEARCH.md Pitfall 7). Since D-06 locks the model framework to XGBoost, `is_unbalance=true` would be a no-op and the trained model would treat the extreme class imbalance (1 winner per ~20 drivers) as balanced — producing degenerate probability outputs. The correct XGBoost equivalent is `scale_pos_weight`, computed at train time as `(n_total - n_pos) / n_pos`. Plans 02-02 and 02-03 use `scale_pos_weight` instead of `is_unbalance`. This override is within Claude's discretion (D-09 is marked Claude's discretion) and the substitution is mandatory to make the imbalance handling actually take effect under the locked D-06 (XGBoost) framework choice.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` — PRERACE-01 through PRERACE-03 and INRACE-01 through INRACE-03 are the requirements for this phase; see Traceability table

### Roadmap
- `.planning/ROADMAP.md` §Phase 2 — Success criteria define the exact artifact paths and validation method (held-out 2024 qualifying session); MUST be read before planning

### Phase 1 Context (feature schema locked here)
- `.planning/phases/01-data-features/01-CONTEXT.md` — D-02 and D-03 define the full feature column set that Phase 2 reads from SQLite; D-05 defines target label storage

### Project Constraints
- `.planning/PROJECT.md` — Key Decisions table and Constraints section (XGBoost in stack, local-first, Python, 2022–2025 window)
- `CLAUDE.md` — Technology stack section; ML Framework section; "What NOT to Use" table (no PyTorch/TensorFlow)

### Existing Code (Phase 1 output)
- `src/f1_prediction/db/engine.py` — SQLAlchemy engine + WAL mode setup; Phase 2 reads from the same DB
- `src/f1_prediction/features/prerace.py` — Defines which columns exist in `features_prerace`; confirms NULL behavior
- `src/f1_prediction/features/inrace.py` — Defines which columns exist in `features_inrace`
- `scripts/backfill.py` — Resumable backfill script that Phase 2 Plan 01 will re-invoke to extend 2023–2025 coverage

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/f1_prediction/db/engine.py`: provides `get_engine()` — Phase 2 reads feature tables using the same SQLAlchemy engine + WAL setup already in place
- `scripts/backfill.py`: resumable backfill with per-session skip logic — re-invoking it for 2023–2025 extends coverage without re-fetching 2022

### Established Patterns
- All SQL uses `text()` with bound `:param` names (no f-string SQL) — follow this in any training queries
- Feature tables use `session_key` as the primary join key to `raw_sessions` for year/round metadata
- `scripts/build_features.py` is a standalone script that can re-run the feature builder without re-fetching raw data — useful after backfill extension

### Known Data Issues
- `circuit_avg_finish` and `constructor_pace_delta` are 100% NULL in the current DB — both depend on 2-season lookback that requires 2022 data to exist before 2023+ can be populated. These become available after a full 2023–2025 backfill runs.
- `team_name` is 100% NULL — root cause not yet diagnosed; investigate during Plan 01
- `pit_stop_delta` in `features_inrace` is 96% NULL (19,728 / 20,472) — expected; most laps don't follow a pit stop

### Integration Points
- Phase 3 will load `data/models/prerace_model.joblib` and `data/models/inrace_model.joblib` via `joblib.load()` — these paths are the integration contract
- Phase 3 reads `predictions` table written by the inference loop; Phase 2 does not write to `predictions`

</code_context>

<specifics>
## Specific Ideas

- The backfill extension (D-01) should be the first plan in Phase 2, not a prerequisite left to Phase 3. It logically belongs here since it's data preparation for training.
- After backfill completes, re-evaluate `circuit_avg_finish` and `constructor_pace_delta` NULL rates before finalizing the feature drop in D-02. If they populate correctly, they may be included.
- The in-race model needs a join from `features_inrace` to `features_prerace` at training time to pull the `is_winner` target label (stored in `features_prerace.finish_position`).

</specifics>

<deferred>
## Deferred Ideas

- Reinstating `team_name`, `circuit_avg_finish`, `constructor_pace_delta` as features — deferred to post-Phase 4 if model accuracy needs improvement
- Hyperparameter search (grid search, Optuna) — deferred to post-Phase 4 iteration
- Top-3 (podium) prediction as a secondary model output — explicitly out of scope per REQUIREMENTS.md v2 items
- LightGBM as an alternative model framework — deferred; XGBoost chosen per ROADMAP.md

None — discussion stayed within phase scope otherwise.

</deferred>

---

*Phase: 2-model-training*
*Context gathered: 2026-06-24*
