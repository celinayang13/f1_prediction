---
phase: 02-model-training
reviewed: 2026-06-18T00:00:00Z
depth: standard
files_reviewed: 7
files_reviewed_list:
  - src/f1_prediction/ml/__init__.py
  - src/f1_prediction/ml/loader.py
  - src/f1_prediction/ml/pipeline.py
  - src/f1_prediction/ml/evaluate.py
  - scripts/train.py
  - tests/test_train_prerace.py
  - tests/test_train_inrace.py
findings:
  critical: 3
  warning: 6
  info: 4
  total: 13
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-06-18T00:00:00Z
**Depth:** standard
**Files Reviewed:** 7
**Status:** issues_found

## Summary

The Phase 02 implementation covers feature loading, ordinal encoding, XGBoost classifier training, Platt-scaling calibration, evaluation metric computation, and artifact serialization. The overall pipeline structure is sound: temporal split is correctly ordered, the OrdinalEncoder is fitted only on training data, SQL queries use parameterized `text()` bindings, and the artifact dict shape provides a clear Phase 3 contract.

Three blockers stand out. First, the entire implementation uses XGBoost despite CLAUDE.md mandating LightGBM as the primary model — `lightgbm` is not in `pyproject.toml` and is absent from every source file. Second, `OrdinalEncoder` with `encoded_missing_value=np.nan` silently fails for Python `None` values (what SQLite NULLs become after `pd.read_sql`): `None` is treated as a valid category and assigned an ordinal integer rather than `np.nan`, so the XGBoost NaN missing-value branch is never triggered for NULL database fields. Third, `compute_log_loss` passes no `labels=[0, 1]` argument to `sklearn.metrics.log_loss` and will raise `ValueError` if the test split ever contains only one class.

Six warnings cover: evaluation metrics measured on calibration data (inflated numbers), a CWD-relative artifact path that writes to the wrong directory when invoked outside the project root, a misleading error message when only the temporal training slice is empty, a hardcoded "pre-race" log message emitted for the in-race model, misleading `PRERACE_TARGET`/`INRACE_TARGET` comments that describe the column as binary when it is not, and the top-3 hit rate measuring in-race quality only at the final lap. Four info items cover an unused `args` variable, a redundant inline `numpy` import, redundant `.copy()` calls, and hardcoded 2022 dates in test fixtures for 2025 sessions.

---

## Critical Issues

### CR-01: Implementation Uses XGBoost; CLAUDE.md Mandates LightGBM

**File:** `src/f1_prediction/ml/pipeline.py:29`, `pyproject.toml:14`

**Issue:** CLAUDE.md specifies "LightGBM 4.6.0 as primary model" with HIGH confidence and explicit reasoning ("Outperforms XGBoost on categorical-heavy tabular data"). The implementation imports and uses `xgboost` exclusively. `pyproject.toml` lists `"xgboost>=3.0,<4.0"` but not `lightgbm`. No `lightgbm` usage exists anywhere in the codebase. The locked hyperparameters also cross-reference XGBoost semantics (`scale_pos_weight=19`, `enable_categorical=True`, `tree_method="hist"`) rather than LightGBM equivalents.

**Fix:** Replace the XGBoost stack with LightGBM. Add `"lightgbm>=4.6,<5.0"` to `pyproject.toml` and remove the `xgboost` dependency. Replace `train_xgb_classifier`:

```python
import lightgbm as lgb

def train_lgb_classifier(
    X_train: pd.DataFrame, y_train: pd.Series
) -> lgb.LGBMClassifier:
    model = lgb.LGBMClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        is_unbalance=True,       # handles 1:19 class imbalance (D-02)
        random_state=42,
        verbose=-1,
    )
    model.fit(X_train, y_train)
    return model
```

Note: LightGBM supports native categoricals via the `categorical_feature` parameter in `fit()`, eliminating the need for `OrdinalEncoder` on string columns.

---

### CR-02: `OrdinalEncoder` Does Not Encode Python `None` (SQLite NULL) as NaN

**File:** `src/f1_prediction/ml/pipeline.py:83-87`

**Issue:** `build_encoder()` is documented to ensure "NULL inputs → NaN output for XGBoost NaN branch" via `encoded_missing_value=np.nan`. This is factually wrong. `OrdinalEncoder.encoded_missing_value` only applies to `np.nan` float values. When `pd.read_sql` reads a NULL TEXT column from SQLite, the value is Python `None` (object dtype), not `np.nan`. `OrdinalEncoder` treats `None` as a distinct category and assigns it an ordinal integer:

```python
enc = OrdinalEncoder(handle_unknown='use_encoded_value',
                     unknown_value=np.nan, encoded_missing_value=np.nan)
df = pd.DataFrame({'team_name': ['Red Bull', 'Ferrari', None]})
enc.fit_transform(df)
# Returns [[1.], [0.], [2.]] — None becomes ordinal 2.0, NOT np.nan
```

Verified empirically against sklearn 1.9.0 (the project requirement of `>=1.8` resolves to this version). The training data in the test fixture explicitly seeds `team_name=None` and `tyre_compound=None`, so `None` enters the encoder during `fit_transform` and becomes a learnable category. At inference time, a new driver with NULL `team_name` gets ordinal `2.0` (routed as known category) instead of `np.nan` (routed via XGBoost missing-value branch). This produces incorrect predictions for drivers with missing categorical data.

**Fix:** Sanitize `None` to `np.nan` before encoding:

```python
def _sanitize_categoricals(df: pd.DataFrame, cat_cols: list[str]) -> pd.DataFrame:
    """Convert Python None to np.nan in categorical columns before OrdinalEncoder."""
    df = df.copy()
    for col in cat_cols:
        df[col] = df[col].where(pd.notna(df[col]), other=np.nan)
    return df

# In train_prerace_model / train_inrace_model, before fit_transform:
X_train = _sanitize_categoricals(X_train, PRERACE_CAT_COLS)
X_test  = _sanitize_categoricals(X_test,  PRERACE_CAT_COLS)
X_train[PRERACE_CAT_COLS] = encoder.fit_transform(X_train[PRERACE_CAT_COLS])
X_test[PRERACE_CAT_COLS]  = encoder.transform(X_test[PRERACE_CAT_COLS])
```

---

### CR-03: `compute_log_loss` Crashes When Test Labels Are Single-Class

**File:** `src/f1_prediction/ml/evaluate.py:35`

**Issue:** `compute_log_loss` wraps `sklearn.metrics.log_loss` without providing `labels=[0, 1]`. If `y_true` contains only one class, sklearn raises:

```
ValueError: y_true contains only one label (0). Please provide the list of all
expected class labels explicitly through the labels argument.
```

This will crash the training pipeline at the evaluation step (pipeline.py:331 and 470) with an opaque error. Edge cases that trigger it: running `train.py` mid-season before any 2025 race winner is recorded (test set has all-zero labels), a backfill that filters all `finish_position=1` rows due to a NULL constraint issue, or any test fixture that produces a single-class test split.

**Fix:**

```python
def compute_log_loss(y_true, win_proba) -> float:
    return float(_sklearn_log_loss(y_true, win_proba, labels=[0, 1]))
```

---

## Warnings

### WR-01: Calibration and Evaluation Use Identical Data — Reported Metrics Are Optimistically Biased

**File:** `src/f1_prediction/ml/pipeline.py:325-331`, `464-470`

**Issue:** In both `train_prerace_model` and `train_inrace_model`, `calibrate_model` is fitted on `(X_test, y_test)` and then `calibrated.predict_proba(X_test.values)` is immediately called to compute the reported `log_loss` and `top3_hit_rate`. `CalibratedClassifierCV` with `cv=N` fits N sigmoid calibrators on N folds of the test set. The final `predict_proba` averages all N calibrators; for each row, the calibrator that trained on it contributes to the average prediction. The evaluation metrics are therefore not out-of-sample and will be optimistically inflated. The stdout output (D-16) will misrepresent model quality to the operator.

**Fix:** Reserve a dedicated calibration partition from the 2025 test set and evaluate only on a held-out portion. If the test set is too small to split (fewer than 4 races with enough positive examples), document the bias explicitly:

```python
logger.warning(
    "Evaluation metrics measured on the calibration set — values are optimistically biased"
)
```

---

### WR-02: `cv` Clamping Floor of 2 Crashes `CalibratedClassifierCV` When 2025 Has Exactly One Winner Row

**File:** `src/f1_prediction/ml/pipeline.py:161`

**Issue:** `cv = min(5, max(2, min_class_count))`. If the 2025 pre-race test set has exactly one race with one winner row, `min_class_count=1`, clamping produces `cv=max(2, 1)=2`. `CalibratedClassifierCV` with `cv=2` attempts stratified splitting with at least 2 examples per class per fold. With only 1 positive example, one fold will have 0 positive examples and sklearn raises `ValueError`. This scenario occurs in production when `train.py` is run before the second 2025 race is backfilled.

**Fix:**

```python
if min_class_count < 2:
    logger.warning(
        "calibrate_model: only %d positive examples — falling back to direct sigmoid fit",
        min_class_count,
    )
    cv = 2  # CalibratedClassifierCV will raise; handle separately or use cv='prefit'
else:
    cv = min(5, min_class_count)
```

A more robust fix is to check `min_class_count < 2` before calling `CalibratedClassifierCV` and fall back to a simpler sigmoid fit via `LogisticRegression` on the raw `predict_proba` scores.

---

### WR-03: Misleading `RuntimeError` When Only the Temporal Training Slice Is Empty

**File:** `src/f1_prediction/ml/pipeline.py:293-295`, `430-432`

**Issue:** After a successful temporal split, `if len(train_df) == 0` raises `RuntimeError("features_prerace is empty — run scripts/backfill.py first")`. But `features_prerace` is NOT empty — `df` was already confirmed non-empty on line 278. This situation arises when only 2025 data has been backfilled (year > 2024 exclusively). The error message directs the operator to run `backfill.py` without specifying which years are missing, and may cause them to re-run a backfill that is already complete for 2025 data, wasting time. The identical message is used for the in-race model at lines 430-432.

**Fix:**

```python
if len(train_df) == 0:
    raise RuntimeError(
        "No training rows found for years <= 2024 in features_prerace. "
        "Run: python scripts/backfill.py --years 2022 2023 2024"
    )
```

---

### WR-04: `save_artifact` Always Logs "Saved pre-race artifact" for Both Models

**File:** `src/f1_prediction/ml/pipeline.py:227`

**Issue:** `logger.info("Saved pre-race artifact to %s", path)` is hardcoded inside `save_artifact()`, which is called by both `train_prerace_model` and `train_inrace_model`. When the in-race artifact is saved, the log line reads "Saved pre-race artifact to data/models/inrace_model.joblib" — factually incorrect and confusing during log inspection.

**Fix:** Derive the label from the path stem:

```python
logger.info("Saved %s artifact to %s", path.stem, path)
```

---

### WR-05: `PRERACE_TARGET` / `INRACE_TARGET` Comments Describe the Column as Binary When It Is Not

**File:** `src/f1_prediction/ml/loader.py:43-44`, `106-109`

**Issue:**

```python
# PRERACE_TARGET: binary label column — 1 if driver won the race, 0 otherwise.
PRERACE_TARGET: str = "finish_position"
```

`finish_position` is an ordinal integer 1–20, not a binary column. The pipeline binarizes it internally via `(finish_position == 1).astype(int)`. A Phase 3 developer reading this comment and using `df[PRERACE_TARGET]` directly as a training label will get multiclass values (1–20) rather than binary 0/1 labels. The incorrect comment is a concrete risk for Phase 3 integration.

**Fix:**

```python
# PRERACE_TARGET: source column for the training label (finish_position, integer 1-20).
# The pipeline binarizes it as: label = (finish_position == 1).astype(int).
# Do NOT use this column directly as a binary label.
PRERACE_TARGET: str = "finish_position"
```

---

### WR-06: In-Race Top-3 Hit Rate Evaluated Only at Final Lap — Early-Race Performance Invisible

**File:** `src/f1_prediction/ml/pipeline.py:484-492`

**Issue:** `final_lap_df = test_df[test_df["lap_number"] == test_df["max_lap"]]` filters to the last lap only before calling `compute_top3_hit_rate`. The printed metric gives no visibility into model quality early in the race, which is exactly when the model is most novel and most uncertain. A model that is poorly calibrated at lap 5 but happens to rank correctly at the last lap shows a deceptively good hit rate. The primary business value of the in-race model is mid-race prediction, not a final-lap post-hoc ranking.

**Fix:** Report hit rate at multiple lap checkpoints:

```python
for frac, label in [(0.25, "lap25pct"), (0.50, "lap50pct"), (1.0, "final_lap")]:
    target_lap = (test_df.groupby("session_key")["lap_number"].transform("max") * frac).round().astype(int)
    lap_df = test_df[test_df["lap_number"] == target_lap].copy()
    if len(lap_df) > 0:
        r, h, t = compute_top3_hit_rate(lap_df, "win_prob", "label", "session_key")
        print(f"inrace  {label}  top3_hit_rate={r:.3f}  ({h}/{t} races)")
```

---

## Info

### IN-01: `args` Variable Assigned but Never Referenced in `train.py`

**File:** `scripts/train.py:50`

**Issue:** `args = parser.parse_args()` captures the result but `args` is never used. Since D-14 prohibits selective training flags there are no attributes to read from it. Dead assignment.

**Fix:**

```python
parser.parse_args()  # validates no unexpected flags; result unused (D-14: no selective flags)
```

---

### IN-02: `numpy` Re-imported Inside Test Function Where It Is Already Module-Level

**File:** `tests/test_train_prerace.py:318`

**Issue:** `import numpy as np` appears inside `test_train_prerace_softmax_sums_to_one`. `numpy` is already imported at module scope on line 18 and the inner import is never actually used within the function. Redundant import shadowing module-level name.

**Fix:** Remove line 318 (`import numpy as np` inside the function body).

---

### IN-03: Redundant `.copy()` Calls on Already-Copied DataFrames

**File:** `src/f1_prediction/ml/pipeline.py:305-306`, `442-443`

**Issue:** `train_df` and `test_df` are produced with `.copy()` at lines 284-285 (and 421-422 for in-race), making them independent from `df`. They are then copied again at lines 305-306 (and 442-443) before label column assignment. The second `.copy()` is unnecessary — the DataFrames are already owned. A third gratuitous `.copy()` appears at lines 334 and 475 when `test_df["win_score"]` is assigned.

**Fix:** Remove the redundant `.copy()` at lines 305-306 and 442-443. The first copy at filter-time is sufficient.

---

### IN-04: Test Fixtures Hardcode `2022-03-20` Dates for 2025 Session Rows

**File:** `tests/test_train_inrace.py:51-53`, `tests/test_train_prerace.py:43-50`

**Issue:** `_seed_raw_session` inserts `date_start='2022-03-20T15:00:00Z'` and `date_end='2022-03-20T17:00:00Z'` regardless of the `year` parameter. Sessions seeded with `year=2025` therefore have 2022 timestamps. The pipeline does not currently parse dates, so tests pass, but any future filtering or ordering on `date_start` will silently produce wrong results from these fixtures.

**Fix:** Derive the date string from the `year` parameter:

```python
date_start = f"{year}-03-20T15:00:00Z"
date_end   = f"{year}-03-20T17:00:00Z"
```

---

_Reviewed: 2026-06-18T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
