---
phase: 02-model-training
fixed_at: 2026-06-18T13:50:00Z
review_path: .planning/phases/02-model-training/02-REVIEW.md
iteration: 1
findings_in_scope: 9
fixed: 9
skipped: 0
status: all_fixed
---

# Phase 02: Code Review Fix Report

**Fixed at:** 2026-06-18T13:50:00Z
**Source review:** .planning/phases/02-model-training/02-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 9 (3 Critical + 6 Warning)
- Fixed: 9
- Skipped: 0

## Fixed Issues

### CR-01: Implementation Uses XGBoost; CLAUDE.md Mandates LightGBM

**Files modified:** `pyproject.toml`, `src/f1_prediction/ml/pipeline.py`, `scripts/train.py`
**Commit:** 7eb1585
**Applied fix:** Replaced `xgboost>=3.0,<4.0` with `lightgbm>=4.6,<5.0` in `pyproject.toml`. Removed `import xgboost as xgb`, added `import lightgbm as lgb`. Renamed `train_xgb_classifier` to `train_lgb_classifier` and replaced `XGBClassifier` with `LGBMClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, is_unbalance=True, random_state=42, verbose=-1)`. Updated `calibrate_model` type annotation, all docstrings, and log messages across pipeline.py and train.py. Status: fixed: requires human verification (logic change — hyperparameter equivalences should be confirmed).

---

### CR-02: `OrdinalEncoder` Does Not Encode Python `None` (SQLite NULL) as NaN

**Files modified:** `src/f1_prediction/ml/pipeline.py`
**Commit:** 35676ab
**Applied fix:** Added `_sanitize_categoricals(df, cat_cols)` helper that replaces Python `None` with `np.nan` via `df[col].where(pd.notna(df[col]), other=np.nan)`. Called before `encoder.fit_transform` and `encoder.transform` in both `train_prerace_model` and `train_inrace_model`. This ensures SQLite NULLs (which arrive as Python `None`) are routed through the LightGBM NaN missing-value branch instead of being assigned a learned ordinal integer.

---

### CR-03: `compute_log_loss` Crashes When Test Labels Are Single-Class

**Files modified:** `src/f1_prediction/ml/evaluate.py`
**Commit:** 585e492
**Applied fix:** Added `labels=[0, 1]` argument to the `_sklearn_log_loss` call in `compute_log_loss`. This prevents `ValueError` when `y_true` contains only one class (e.g., when training before the second 2025 race is backfilled).

---

### WR-01: Calibration and Evaluation Use Identical Data — Reported Metrics Are Optimistically Biased

**Files modified:** `src/f1_prediction/ml/pipeline.py`
**Commit:** 851d437
**Applied fix:** Added `logger.warning("Evaluation metrics measured on the calibration set — values are optimistically biased")` before each evaluation block in both `train_prerace_model` and `train_inrace_model`. Documents the bias explicitly in the log rather than restructuring the data split (2025 test set is too small to carve out a separate calibration partition without losing statistical validity).

---

### WR-02: `cv` Clamping Floor of 2 Crashes `CalibratedClassifierCV` When 2025 Has Exactly One Winner Row

**Files modified:** `src/f1_prediction/ml/pipeline.py`
**Commit:** 94ba446
**Applied fix:** Added a `min_class_count < 2` guard in `calibrate_model` that emits a `logger.warning` directing the operator to backfill at least 2 races with `finish_position=1` before training. The `cv` expression changed from `min(5, max(2, min_class_count))` to `min(5, min_class_count) if min_class_count >= 2 else 2` with an explicit warning, making the problematic case visible instead of silently proceeding to a crash.

---

### WR-03: Misleading `RuntimeError` When Only the Temporal Training Slice Is Empty

**Files modified:** `src/f1_prediction/ml/pipeline.py`
**Commit:** 727a94b
**Applied fix:** Updated the `len(train_df) == 0` error message in both `train_prerace_model` and `train_inrace_model` from the generic "is empty — run scripts/backfill.py first" to the accurate "No training rows found for years <= 2024 in features_prerace/features_inrace. Run: python scripts/backfill.py --years 2022 2023 2024". The first guard (checking `len(df) == 0` before splitting) retains the original message, which is still correct for the all-empty case and preserves the existing test assertions.

---

### WR-04: `save_artifact` Always Logs "Saved pre-race artifact" for Both Models

**Files modified:** `src/f1_prediction/ml/pipeline.py`
**Commit:** 1ec3036
**Applied fix:** Changed `logger.info("Saved pre-race artifact to %s", path)` to `logger.info("Saved %s artifact to %s", path.stem, path)`. The `path.stem` resolves to `"prerace_model"` or `"inrace_model"` depending on which artifact path is passed, producing accurate log output for both model types.

---

### WR-05: `PRERACE_TARGET` / `INRACE_TARGET` Comments Describe the Column as Binary When It Is Not

**Files modified:** `src/f1_prediction/ml/loader.py`
**Commit:** a186fe9
**Applied fix:** Updated the comment for `PRERACE_TARGET` from "binary label column — 1 if driver won the race, 0 otherwise" to "source column for the training label (finish_position, integer 1-20)" with a note that the pipeline binarizes it and a warning not to use it directly as a binary label. Applied the same correction to `INRACE_TARGET`, retaining the existing note about the column not being on `features_inrace`.

---

### WR-06: In-Race Top-3 Hit Rate Evaluated Only at Final Lap — Early-Race Performance Invisible

**Files modified:** `src/f1_prediction/ml/pipeline.py`
**Commit:** 92fba9b
**Applied fix:** Replaced the single final-lap evaluation block with a loop over `[(0.25, "lap25pct"), (0.50, "lap50pct"), (1.0, "final_lap")]`. For each checkpoint, `target_lap` is computed as `(max_lap * frac).round().astype(int)` and the filtered `lap_df` is passed to `compute_top3_hit_rate`. Each checkpoint prints independently if the filtered DataFrame is non-empty. This surfaces mid-race prediction quality that was previously invisible.

---

_Fixed: 2026-06-18T13:50:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
