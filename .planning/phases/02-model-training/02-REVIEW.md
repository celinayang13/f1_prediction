---
phase: 02-model-training
reviewed: 2026-06-17T00:00:00Z
depth: standard
files_reviewed: 7
files_reviewed_list:
  - scripts/train.py
  - src/f1_prediction/ml/__init__.py
  - src/f1_prediction/ml/evaluate.py
  - src/f1_prediction/ml/loader.py
  - src/f1_prediction/ml/pipeline.py
  - tests/test_train_inrace.py
  - tests/test_train_prerace.py
findings:
  critical: 3
  warning: 6
  info: 4
  total: 13
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-06-17
**Depth:** standard
**Files Reviewed:** 7
**Status:** issues_found

## Summary

The Phase 02 model training implementation covers feature loading, ordinal encoding, XGBoost classifier training, Platt-scaling calibration, evaluation metric computation, and artifact serialization. The overall pipeline structure is sound (temporal split, per-session softmax normalization, joblib artifact dict). However, three blockers require fixes before this code ships: the framework actually used (XGBoost) contradicts the explicit project mandate (LightGBM) from CLAUDE.md; `enable_categorical=True` is silently nullified when the training matrix is converted to a numpy array before `fit()`; and the evaluation metrics are computed on the same data used to fit the calibration layer, producing unreliable numbers. Six quality warnings cover a crashable cv-clamping edge case, CWD-relative artifact paths, misleading error messages, and a wrong log message. Four informational items cover naming/docstring inaccuracies, an unused variable, redundant copies, and a repeated import.

---

## Critical Issues

### CR-01: Framework Deviation — XGBoost Used Instead of Project-Mandated LightGBM

**File:** `src/f1_prediction/ml/pipeline.py:29`
**Issue:** `CLAUDE.md` (the binding project specification) mandates LightGBM 4.6.0 as the primary model with an explicit rationale (native categorical support, better performance on small high-cardinality tabular data, tighter scikit-learn integration). The implementation imports and uses `xgboost` instead. `lightgbm` does not appear anywhere in the codebase and is absent from `pyproject.toml`. This is not a minor stylistic drift — LightGBM's `LGBMClassifier` has a different hyperparameter surface (`num_leaves`, `min_child_samples`, `categorical_feature` list) and different categorical-handling semantics. The D-03 locked hyperparameters (`n_estimators=300`, `max_depth=4`, `learning_rate=0.05`, `scale_pos_weight=19`) were specified for LightGBM; `scale_pos_weight` is not a valid LightGBM parameter (the equivalent is `is_unbalance=True` or `class_weight`), so they would need revision when the correct library is used.

**Fix:** Replace the XGBoost stack with LightGBM. Minimal change:
```python
# Remove:
import xgboost as xgb

# Add:
import lightgbm as lgb

def train_lgb_classifier(
    X_train: pd.DataFrame, y_train: pd.Series
) -> lgb.LGBMClassifier:
    model = lgb.LGBMClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        is_unbalance=True,          # handles 1:19 class imbalance (D-02)
        categorical_feature=PRERACE_CAT_COLS,   # native categorical (no OrdinalEncoder needed)
        random_state=42,
        verbose=-1,
    )
    model.fit(X_train, y_train)
    return model
```
Also add `"lightgbm>=4.6,<5.0"` to `pyproject.toml` and remove the `xgboost` dependency.

---

### CR-02: `enable_categorical=True` Is a Silent No-op When Passing Numpy Arrays

**File:** `src/f1_prediction/ml/pipeline.py:111-121`
**Issue:** `train_xgb_classifier` sets `enable_categorical=True` and `tree_method="hist"` on the XGBClassifier, then calls `model.fit(X_train.values, y_train.values)`. When XGBoost 3.x receives a raw numpy ndarray (via `.values`), it has no column metadata and cannot identify which columns are categorical — the `enable_categorical` flag is silently ignored and all features are treated as continuous numeric. No error is raised. The result is that the OrdinalEncoder's float codes (0.0, 1.0, 2.0 …) are fed to XGBoost as ordinary numeric features, providing none of the tree-routing benefits of native categorical handling that D-07 was designed to achieve. The same bug exists in `calibrate_model` at line 176 (`calibrated.fit(X_test.values, y_test.values)`). XGBoost's own documentation states that `feature_types` must be provided, or a pandas DataFrame with `pd.CategoricalDtype` columns must be passed, for categorical features to be detected.

**Fix:** Pass the DataFrame directly instead of `.values`, and set the correct dtype on categorical columns before fitting:
```python
def train_xgb_classifier(
    X_train: pd.DataFrame, y_train: pd.Series
) -> xgb.XGBClassifier:
    model = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=19,
        enable_categorical=True,
        tree_method="hist",
        random_state=42,
        eval_metric="logloss",
    )
    # Pass DataFrame directly — XGBoost needs column metadata to detect categoricals.
    # Convert OrdinalEncoder output columns back to pandas Categorical so XGBoost
    # recognises them.
    X_fit = X_train.copy()
    for col in cat_cols:  # cat_cols must be threaded through from the caller
        X_fit[col] = X_fit[col].astype("category")
    model.fit(X_fit, y_train)
    return model
```
Alternatively, abandon `enable_categorical` and rely purely on OrdinalEncoder output as numeric (simpler, honest about what the model actually does). Note: this bug is moot if CR-01 is resolved by switching to LightGBM, which has a proper `categorical_feature` list interface.

---

### CR-03: Calibration and Evaluation Use Identical Data (Reported Metrics Are Unreliable)

**File:** `src/f1_prediction/ml/pipeline.py:325-331`, `464-470`
**Issue:** In both `train_prerace_model` and `train_inrace_model`, the calibration layer is fitted on `X_test` / `y_test` (lines 325, 464), and then the calibrated model's output is immediately evaluated against the same `X_test` / `y_test` (lines 328-331, 467-470). `CalibratedClassifierCV` with `cv=N` internally splits `X_test` into N folds; each fold's calibrator has seen the other N-1 folds' data at calibration time. The predictions fed back to `compute_log_loss` come from a model whose calibration parameters were fitted on these exact samples. This produces optimistic log_loss and top-3 hit rate values that do not generalize. The printed metrics (D-16 stdout output) will be consumed by the operator as accuracy claims; reporting inflated numbers is a correctness defect.

**Fix:** Hold out a dedicated calibration partition from the 2025 test set before calibrating, and report metrics only on the remaining held-out evaluation partition:
```python
# In train_prerace_model, after temporal split:
from sklearn.model_selection import train_test_split

cal_df, eval_df = train_test_split(
    test_df, test_size=0.5, stratify=test_df["label"], random_state=42
)
X_cal  = cal_df[PRERACE_FEATURE_COLS].copy()
X_eval = eval_df[PRERACE_FEATURE_COLS].copy()
X_cal[PRERACE_CAT_COLS]  = encoder.transform(X_cal[PRERACE_CAT_COLS])
X_eval[PRERACE_CAT_COLS] = encoder.transform(X_eval[PRERACE_CAT_COLS])

calibrated = calibrate_model(model, X_cal, cal_df["label"])

proba      = calibrated.predict_proba(X_eval.values)
win_scores = proba[:, 1]
ll = compute_log_loss(eval_df["label"].values, win_scores)
# ... top-3 hit rate on eval_df ...
```
If the 2025 data set is too small to split (fewer than 4 races), document explicitly that calibration and evaluation overlap, and add a warning log so operators are not misled.

---

## Warnings

### WR-01: cv Clamping Floor of 2 Causes `CalibratedClassifierCV` to Crash When 2025 Has Only One Race

**File:** `src/f1_prediction/ml/pipeline.py:161`
**Issue:** `cv = min(5, max(2, min_class_count))`. If the 2025 test set contains exactly one race (and therefore one winner row for the pre-race model), `min_class_count = 1` and the clamping produces `cv = max(2, 1) = 2`. `CalibratedClassifierCV` with `cv=2` requires at least 2 examples of each class per fold; with 1 winner row, one of the two folds will have 0 positive examples and sklearn raises `ValueError: The least populated class in y has only 1 member, which is too few.` This is not just a test concern — it can happen in production if `train.py` is run before the 2025 season is substantially underway.

**Fix:** Lower the floor to 1 when `min_class_count == 1`, and use `cv="prefit"` style by switching to a single held-out sigmoid fit, or accept cv=1 with a `StratifiedKFold`:
```python
if min_class_count < 2:
    # Fewer positive examples than needed for any cross-validation.
    # Fall back to a simple sigmoid fit on the full calibration set.
    logger.warning(
        "calibrate_model: min_class_count=%d < 2; using direct sigmoid fit "
        "instead of cross-validated calibration",
        min_class_count,
    )
    from sklearn.calibration import calibration_curve
    # ... or manually fit a LogisticRegression on predict_proba output
else:
    cv = min(5, min_class_count)
    calibrated = CalibratedClassifierCV(FrozenEstimator(fitted_model), method="sigmoid", cv=cv)
    calibrated.fit(X_test.values, y_test.values)
```

---

### WR-02: Artifact Paths Are CWD-Relative, Will Silently Write to Wrong Location

**File:** `src/f1_prediction/ml/pipeline.py:53-54`
**Issue:**
```python
PRERACE_MODEL_PATH: Path = Path("data/models/prerace_model.joblib")
INRACE_MODEL_PATH:  Path = Path("data/models/inrace_model.joblib")
```
These are relative paths resolved at call-site against whatever the current working directory is at runtime. Running `python scripts/train.py` from inside `scripts/` would write the artifact to `scripts/data/models/`, not the project root's `data/models/`. There is no guard or normalization. The `scripts/train.py` CLI is the intended entrypoint and it does not `cd` to the project root before calling `train_prerace_model`.

**Fix:** Anchor the path to the module file:
```python
# pipeline.py
_MODULE_DIR = Path(__file__).resolve().parent          # src/f1_prediction/ml/
_PROJECT_ROOT = _MODULE_DIR.parents[2]                 # project root

PRERACE_MODEL_PATH: Path = _PROJECT_ROOT / "data" / "models" / "prerace_model.joblib"
INRACE_MODEL_PATH:  Path = _PROJECT_ROOT / "data" / "models" / "inrace_model.joblib"
```

---

### WR-03: Misleading RuntimeError Message When Training Split (Not the Entire Table) Is Empty

**File:** `src/f1_prediction/ml/pipeline.py:294-296`, `431-433`
**Issue:** After the temporal split, `train_prerace_model` checks `if len(train_df) == 0` and raises `RuntimeError("features_prerace is empty — run scripts/backfill.py first")`. But `train_df` is the result of `df[df["year"] <= 2024]`, and `df` was already confirmed non-empty. A user who has only backfilled 2025 data (a reasonable workflow) will see a message telling them to run backfill when backfill was already run — they need to run it for years 2022-2024 specifically. The same error text appears for the inrace model (lines 431-433). The operator cannot distinguish between "no data at all" and "training years absent" from the same error message.

**Fix:**
```python
if len(train_df) == 0:
    raise RuntimeError(
        "No training rows found for years <= 2024 in features_prerace. "
        "Run: python scripts/backfill.py --years 2022 2023 2024"
    )
```

---

### WR-04: `save_artifact` Log Message Hardcoded as "pre-race" for Both Models

**File:** `src/f1_prediction/ml/pipeline.py:227`
**Issue:** `logger.info("Saved pre-race artifact to %s", path)` is inside `save_artifact`, which is called by both `train_prerace_model` and `train_inrace_model`. When the in-race model is saved the log line says "pre-race artifact", making training logs misleading.

**Fix:** Either pass a `label` parameter to `save_artifact` or log after the call:
```python
def save_artifact(path: Path, *, model, feature_cols, cat_cols, encoder,
                  trained_on, trained_on, tested_on, label: str = "model") -> dict:
    ...
    joblib.dump(artifact, path)
    logger.info("Saved %s artifact to %s", label, path)
    return artifact
```

---

### WR-05: `PRERACE_TARGET` / `INRACE_TARGET` Constants Are Misleadingly Named and Commented

**File:** `src/f1_prediction/ml/loader.py:43-44`, `106-109`
**Issue:** Both constants are documented as "binary label column — 1 if driver won the race, 0 otherwise" but their values are `"finish_position"` — a 1-to-20 ordinal position column, not a binary column. The binary label is derived by comparing `finish_position == 1` inside `pipeline.py`. Any future reader (or Phase 3 implementer) who relies on the constant name and comment to understand the training target will be misled. If Phase 3 calls `df[INRACE_TARGET]` expecting binary labels, it will get positions.

**Fix:** Rename to `PRERACE_POSITION_COL` / `INRACE_POSITION_COL` with a corrected comment, or add a clear note that the column is an intermediate that the pipeline binarizes:
```python
# Column holding raw finish position (1-20); pipeline.py binarizes as (finish_position == 1).
# NOT a binary column. Do not use directly as a training label.
PRERACE_POSITION_COL: str = "finish_position"
```

---

### WR-06: Top-3 Hit Rate Evaluated Only at Final Lap for In-Race Model — Missing Lap-Level Coverage

**File:** `src/f1_prediction/ml/pipeline.py:484-492`
**Issue:** The `train_inrace_model` filters `final_lap_df = test_df[test_df["lap_number"] == test_df["max_lap"]]` and passes it to `compute_top3_hit_rate`. This evaluates model quality at only the last lap. Early-lap performance (where the model has the highest uncertainty and most business value) is not measured or reported. The stdout line for `inrace` therefore gives no insight into how well the model performs during the race before the final lap. A model that performs poorly on lap 5 but happens to rank correctly on the final lap would show a high hit rate.

**Fix:** Report hit rate at multiple lap checkpoints (e.g., lap 1, lap 10, lap 20, final):
```python
for lap_pct, desc in [(0.1, "lap10pct"), (0.5, "lap50pct"), (1.0, "final_lap")]:
    lap_threshold = (test_df["max_lap"] * lap_pct).round().astype(int)
    lap_df = test_df[test_df["lap_number"] == lap_threshold].copy()
    if len(lap_df) > 0:
        r, h, t = compute_top3_hit_rate(lap_df, "win_prob", "label", "session_key")
        print(f"inrace  {desc}  top3_hit_rate={r:.3f}  ({h}/{t} races)")
```

---

## Info

### IN-01: `args` Variable Assigned but Never Used in `train.py`

**File:** `scripts/train.py:50`
**Issue:** `args = parser.parse_args()` — `args` is captured but never referenced. Because D-14 prohibits selective training flags there are no attributes to read from `args`, so the assignment is dead. Some linters will flag this; it also signals to readers that something might be missing.

**Fix:** Replace with bare `parser.parse_args()` (valid — it still handles `--help` and errors on unknown flags):
```python
parser.parse_args()
```

---

### IN-02: `numpy` Re-imported Inside Function Body When Already Imported at Module Level

**File:** `tests/test_train_prerace.py:318`
**Issue:** `import numpy as np` appears inside `test_train_prerace_softmax_sums_to_one` at line 318. `numpy` is already imported at module scope on line 18. The inner import is redundant and unused within the function (the function doesn't use `np` after the import either — the variable is from the outer scope). Dead code inside tests reduces readability.

**Fix:** Remove line 318 (`import numpy as np` inside the function body).

---

### IN-03: Redundant `.copy()` Calls After Already-Copied DataFrames

**File:** `src/f1_prediction/ml/pipeline.py:305-306`, `442-443`
**Issue:** `train_df` and `test_df` are produced by `df[df["year"] <= 2024].copy()` and `df[df["year"] == 2025].copy()` at lines 284-285 (and 421-422 for inrace). They are then immediately re-copied at lines 305-306 and 442-443 before label assignment. The second `.copy()` is unnecessary because the DataFrames are already independent copies. A third `.copy()` follows at lines 334 and 475. This is harmless but shows confused ownership reasoning.

**Fix:** Remove the redundant `.copy()` calls at lines 305-306 and 442-443 (the first `.copy()` at filter time is sufficient). Keep the copy at line 334/475 where an additional column is being appended to an existing local variable.

---

### IN-04: Test Seed Hardcodes `2022-03-20` Date for 2025 Session Row

**File:** `tests/test_train_inrace.py:51-53`
**Issue:** `_seed_raw_session` inserts `date_start='2022-03-20T15:00:00Z'` and `date_end='2022-03-20T17:00:00Z'` regardless of the `year` parameter. The 2025 test session rows therefore have 2022 timestamps. This is not a current test failure (the pipeline does not parse dates), but if a future column or filter uses `date_start` for ordering or validation, the fixture will silently produce wrong behavior. In test_train_prerace.py, the same hardcoded 2022 dates are used for both the 2022 and 2025 qualifying sessions (lines 43-50).

**Fix:** Parametrize the date strings by `year`:
```python
date_start = f"{year}-03-20T15:00:00Z"
date_end   = f"{year}-03-20T17:00:00Z"
```

---

_Reviewed: 2026-06-17_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
