# Phase 2: Model Training - Context

**Gathered:** 2026-06-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Train two XGBoost win-probability models offline from the Phase 1 feature tables (`features_prerace`, `features_inrace`), calibrate them with Platt scaling, evaluate on a held-out 2025 test set, and serialize both artifacts to `data/models/`. Phase 3 loads these artifacts for live inference.

**Not in scope:** live inference wiring, dashboard, serving API, or any Phase 3 work.

</domain>

<decisions>
## Implementation Decisions

### ML Library

- **D-01:** Use **XGBoost** (not LightGBM). This overrides CLAUDE.md which lists LightGBM — XGBoost is the explicit user preference. Install xgboost if not already present; confirm version ≥ 3.0 for `enable_categorical` support.
- **D-02:** Set `scale_pos_weight=19` (approximately 1:19 winner-to-non-winner ratio) to handle class imbalance. Do not use `is_unbalance` — that is a LightGBM parameter.
- **D-03:** Fixed sensible hyperparameters: `n_estimators=300`, `max_depth=4`, `learning_rate=0.05`, `scale_pos_weight=19`. No GridSearch or early stopping — iteration speed matters more than marginal accuracy gains at this data scale (~1,440 pre-race rows, ~72K in-race rows).

### Training / Test Split

- **D-04:** Temporal split: **train on 2022–2024, test on 2025**. 2025 is the most recent complete season and best proxy for production performance. No random splitting — time-series integrity required to prevent data leakage (a driver's 2025 race must not inform training on their 2024 race).
- **D-05:** Both models (pre-race and in-race) use the same 2022–2024 / 2025 temporal split.

### Categorical Encoding

- **D-06:** Use `sklearn.preprocessing.OrdinalEncoder` for string categorical columns (`tyre_compound`, `team_name`) before passing to XGBoost. Set `handle_unknown='use_encoded_value', unknown_value=-1` so unseen categories at inference time (e.g., new 2026 team) get a consistent integer rather than raising an error.
- **D-07:** Set `enable_categorical=True` in the XGBoost estimator so it treats the ordinally-encoded columns as categoricals (better splits than treating them as continuous integers).

### NULL Handling

- **D-08:** Let XGBoost handle NaN natively — no imputation. XGBoost learns the optimal branch direction for missing values during training. This covers: `practice_pace_fp3=NULL` on sprint weekends, `team_name=NULL` for debut races. No separate imputer to fit or save.

### Probability Calibration

- **D-09:** Wrap each XGBoost model in `sklearn.calibration.CalibratedClassifierCV(method='sigmoid', cv='prefit')` (Platt scaling). Fit the calibration layer on the 2025 test set. This converts raw XGBoost scores to better-calibrated probabilities.
- **D-10:** After calibration, apply `scipy.special.softmax` over all drivers in a session to produce a valid win probability distribution summing to 1.0 ± 0.001. This is the per-session normalization step (calibration alone does not guarantee the sum across drivers = 1.0).

### Model Artifacts

- **D-11:** Save each model as a single `.joblib` file at `data/models/prerace_model.joblib` and `data/models/inrace_model.joblib`. Each file is a Python dict:
  ```python
  {
    "model": calibrated_xgb_pipeline,   # CalibratedClassifierCV wrapping XGBoost
    "feature_cols": [...],              # ordered list of feature column names
    "cat_cols": [...],                  # which cols were ordinally encoded
    "encoder": fitted_ordinal_encoder,  # fitted OrdinalEncoder for inference
    "trained_on": "2022-2024",
    "tested_on": "2025",
  }
  ```
  Phase 3 loads this dict and uses `feature_cols` + `encoder` to prepare inference inputs in the correct order.
- **D-12:** Create `data/models/` directory if it doesn't exist. Do not track model files in git (add to `.gitignore`).

### Training Script

- **D-13:** Entry point: `scripts/train.py`. Mirrors `scripts/backfill.py` pattern from Phase 1. Run with `python scripts/train.py`.
- **D-14:** Always retrain both pre-race and in-race models on every run (no selective flags). Training is fast enough (~seconds at this data scale) that separate flags add complexity with no benefit.
- **D-15:** Script reads from the SQLite database at `data/f1.db` (same path as Phase 1).

### Evaluation Metrics

- **D-16:** After training, `scripts/train.py` prints to stdout:
  - Pre-race model: log loss on 2025 test set + top-3 hit rate (% of 2025 races where the winner appeared in the top-3 highest-probability drivers)
  - In-race model: log loss on 2025 test set + top-3 hit rate at final lap (lap before the race ends)
- **D-17:** No quality gate — no `sys.exit(1)` on bad metrics. Print numbers and let the developer decide. A hard gate would block Phase 3 if data quality is unexpectedly poor.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Feature Schema (inputs to both models)
- `src/f1_prediction/db/models.py` — `FeaturesPrerace` and `FeaturesInrace` ORM classes define all column names and types; use these as the authoritative column list
- `.planning/phases/01-data-features/01-CONTEXT.md` §D-02, D-03 — Phase 1 decisions that define pre-race and in-race feature columns

### Requirements
- `.planning/REQUIREMENTS.md` — PRERACE-01 through PRERACE-03 and INRACE-01 through INRACE-03 are the requirements for this phase

### Project Constraints
- `.planning/PROJECT.md` §Constraints — Python only, local-first, 2022–2025 data window
- `CLAUDE.md` §ML Framework — lists xgboost, scikit-learn, scipy, joblib versions and purposes. NOTE: CLAUDE.md lists LightGBM as primary model; this is overridden by D-01 above (user explicit preference for XGBoost). All other stack references in CLAUDE.md remain valid.

### Roadmap
- `.planning/ROADMAP.md` §Phase 2 — success criteria define exact artifact paths and evaluation requirements; read before writing training script

### Deferred Items
- `.planning/STATE.md` §Deferred Items — sprint weekend FP3 NULL and debut race team_name NULL are both handled by D-08 (XGBoost native NaN)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/f1_prediction/db/engine.py` — `get_engine()` function creates the SQLAlchemy engine pointing to `data/f1.db`; use this to read from `features_prerace` and `features_inrace`
- `src/f1_prediction/db/models.py` — `FeaturesPrerace` and `FeaturesInrace` ORM models; use their `__table__` for column name introspection or read directly with `pd.read_sql`

### Established Patterns
- Phase 1 scripts use `pandas.read_sql(text(...), conn)` for DB reads — follow the same pattern in `scripts/train.py`
- All SQL uses `text()` with bound `:param` names (security pattern T-03-05 from Phase 1)
- `scripts/backfill.py` pattern: CLI script at top level, imports from `src/f1_prediction/` for logic

### Integration Points
- Phase 3 loads `data/models/prerace_model.joblib` and `data/models/inrace_model.joblib` — the dict keys in D-11 are the contract Phase 3 depends on
- Phase 3 will call `encoder.transform()` from the saved artifact to prepare inference inputs; ensure the encoder is always saved in the artifact dict

</code_context>

<specifics>
## Specific Ideas

- The Platt scaling calibration (CalibratedClassifierCV) should be fit on the 2025 test set after the base XGBoost model is trained on 2022–2024. This uses the test set for calibration fitting, which is acceptable here because the primary metric is the downstream top-3 hit rate, not a fully independent calibration set. If rigorous calibration is needed later, a held-out 2025-H2 split can be introduced in a future phase.
- `data/models/` directory should be created by `scripts/train.py` at runtime with `pathlib.Path("data/models").mkdir(parents=True, exist_ok=True)`.

</specifics>

<deferred>
## Deferred Ideas

- **Hyperparameter tuning**: GridSearchCV or Optuna over `n_estimators`, `max_depth`, `subsample` deferred to a future improvement phase once baseline model quality is known.
- **Separate calibration set**: Using a truly held-out calibration set (separate from both train and test) deferred — acceptable for v1 given data scale.
- **SHAP feature importance**: Generating SHAP plots for model interpretability — useful for debugging but out of scope for Phase 2. Phase 2 is about getting models trained and serialized.
- **In-race model per-lap strategy**: Some laps have far fewer drivers (retirements). Handling variable grid size in the softmax normalization is deferred — Phase 3 handles inference and can filter retired drivers.

</deferred>

---

*Phase: 2-model-training*
*Context gathered: 2026-06-17*
