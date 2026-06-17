# Phase 2: Model Training - Research

**Researched:** 2026-06-17
**Domain:** XGBoost binary classification, sklearn calibration, joblib serialization, F1 win probability training pipeline
**Confidence:** MEDIUM

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Use **XGBoost** (not LightGBM). This overrides CLAUDE.md which lists LightGBM — XGBoost is the explicit user preference. Install xgboost if not already present; confirm version >= 3.0 for `enable_categorical` support.
- **D-02:** Set `scale_pos_weight=19` (approximately 1:19 winner-to-non-winner ratio) to handle class imbalance. Do not use `is_unbalance` — that is a LightGBM parameter.
- **D-03:** Fixed sensible hyperparameters: `n_estimators=300`, `max_depth=4`, `learning_rate=0.05`, `scale_pos_weight=19`. No GridSearch or early stopping.
- **D-04:** Temporal split: **train on 2022–2024, test on 2025**. No random splitting — time-series integrity required.
- **D-05:** Both models (pre-race and in-race) use the same 2022–2024 / 2025 temporal split.
- **D-06:** Use `sklearn.preprocessing.OrdinalEncoder` for string categoricals (`tyre_compound`, `team_name`). Set `handle_unknown='use_encoded_value', unknown_value=-1` so unseen categories get a consistent integer.
- **D-07:** Set `enable_categorical=True` in XGBoost so it treats ordinally-encoded columns as categoricals.
- **D-08:** Let XGBoost handle NaN natively — no imputation. No separate imputer to fit or save.
- **D-09:** Wrap each model in `CalibratedClassifierCV(FrozenEstimator(xgb_model), method='sigmoid')`. Fit calibration layer on 2025 test set.
- **D-10:** After calibration, apply `scipy.special.softmax` over all drivers in a session to produce a valid win probability distribution summing to 1.0 ± 0.001.
- **D-11:** Save each model as a single `.joblib` file containing a Python dict with keys: `model`, `feature_cols`, `cat_cols`, `encoder`, `trained_on`, `tested_on`.
- **D-12:** Create `data/models/` directory at runtime. Do not track model files in git (add to `.gitignore`).
- **D-13:** Entry point: `scripts/train.py`. Run with `python scripts/train.py`.
- **D-14:** Always retrain both models on every run (no selective flags).
- **D-15:** Script reads from `data/f1.db`.
- **D-16:** After training, print to stdout: log loss on 2025 test set + top-3 hit rate for both models.
- **D-17:** No quality gate — no `sys.exit(1)` on bad metrics.

### Claude's Discretion

*(none listed in CONTEXT.md)*

### Deferred Ideas (OUT OF SCOPE)

- Hyperparameter tuning (GridSearchCV or Optuna)
- Separate calibration set (truly held-out from both train and test)
- SHAP feature importance plots
- In-race model per-lap variable grid size handling in softmax normalization
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PRERACE-01 | Pre-race model trained on qualifying delta to pole, practice lap times, circuit-specific win history, constructor performance, championship standings | FeaturesPrerace columns confirmed in models.py; feature SQL pattern established in features/prerace.py |
| PRERACE-02 | Pre-race model outputs a win probability distribution across all ~20 drivers (probabilities sum to 1) | CalibratedClassifierCV(FrozenEstimator) + scipy.special.softmax pattern documented |
| PRERACE-03 | Pre-race probabilities computed and available before lights out, using qualifying session data as primary input | Artifact saved to data/models/prerace_model.joblib; Phase 3 loads at race start |
| INRACE-01 | In-race model trained on current positions, gaps to leader, laps remaining, tyre compound and age, pit stop history | FeaturesInrace columns confirmed in models.py |
| INRACE-02 | In-race model re-runs after each completed lap and outputs updated win probability distribution | Model artifact serialized for fast joblib.load(); softmax normalization per lap group |
| INRACE-03 | In-race model incorporates live pit stop data | pit_stop_count and pit_stop_delta columns present in FeaturesInrace |
</phase_requirements>

---

## Summary

Phase 2 trains two XGBoost binary classifiers that predict race win probability — one from pre-qualifying state, one from lap-by-lap race state. Both read from feature tables produced by Phase 1 (`features_prerace`, `features_inrace` in `data/f1.db`), apply an OrdinalEncoder for string categoricals, train with a 2022–2024 temporal split, calibrate on the 2025 test set using Platt scaling via sklearn's `FrozenEstimator` pattern, then serialize to `.joblib` artifacts.

The critical execution prerequisite is that `data/f1.db` must be populated via `python scripts/backfill.py` before `scripts/train.py` can run. The database does not exist at time of research (only `data/cache/` is present). This is expected — Phase 1 completed the infrastructure but the actual backfill of all 2022–2025 data must be run to produce training rows.

Two API changes in the installed stack require attention. First, `CalibratedClassifierCV(cv='prefit')` is deprecated in scikit-learn 1.6 and removed in 1.8 (the installed version is 1.8.0) — the correct API is `CalibratedClassifierCV(FrozenEstimator(fitted_model), method='sigmoid')`. Second, xgboost is not currently installed in the project venv and must be added to `pyproject.toml` dependencies. The CONTEXT.md decision D-09 uses the old `cv='prefit'` syntax; the plan must use `FrozenEstimator` instead.

**Primary recommendation:** Write `scripts/train.py` following the `scripts/backfill.py` CLI pattern; use `FrozenEstimator` (not `cv='prefit'`) for calibration; set `unknown_value=np.nan` in OrdinalEncoder (not `-1`) for correct XGBoost NaN routing; add xgboost to `pyproject.toml`.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Feature loading (read from SQLite) | Data Layer (SQLite) | scripts/train.py | Phase 1 already populated feature tables; train.py is a reader |
| OrdinalEncoder fit + transform | ML Pipeline (sklearn) | scripts/train.py | Encoder fitted on training split only; must be saved in artifact for inference |
| XGBoost model training | ML Layer (XGBoost) | scripts/train.py | Binary classifier with scale_pos_weight; per-session softmax post-processing |
| Probability calibration | ML Layer (sklearn calibration) | — | FrozenEstimator wraps fitted XGB; calibration fitted on 2025 test data |
| Model serialization | Storage (joblib) | data/models/ | Dict artifact preserves encoder + feature_cols contract for Phase 3 |
| Evaluation metrics | scripts/train.py stdout | — | Log loss + top-3 hit rate; no persistence to DB |

---

## Standard Stack

### Core (all already installed in .venv except xgboost)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| xgboost | 3.3.0 (latest on PyPI) | Primary model: binary classifier for win probability | User decision D-01; `enable_categorical=True` support available since 3.0; native NaN handling |
| scikit-learn | 1.8.0 (installed) | OrdinalEncoder, FrozenEstimator, CalibratedClassifierCV, log_loss | Pipeline + calibration layer; `FrozenEstimator` required for prefit calibration in 1.6+ |
| pandas | 2.3.3 (installed) | DataFrame operations for feature loading and train/test split | Already used across all Phase 1 code |
| numpy | 2.4.3 (installed) | Array ops, np.nan for OrdinalEncoder unknown_value | Required by sklearn/xgboost |
| joblib | 1.5.3 (installed) | Model serialization to .joblib | Used natively by scikit-learn; fast pickle for numpy arrays |
| scipy | 1.17.1 (installed) | `scipy.special.softmax` for per-session probability normalization | Numerically stable softmax; decision D-10 |
| SQLAlchemy | 2.0.51 (installed) | Database connection via `get_engine()` | Same engine as Phase 1 |

[VERIFIED: PyPI registry] — xgboost 3.3.0 confirmed via `pip3 index versions xgboost`
[VERIFIED: project venv] — scikit-learn 1.8.0, pandas 2.3.3, numpy 2.4.3, joblib 1.5.3, scipy 1.17.1 confirmed in `.venv/lib/python3.11/site-packages/`

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pathlib (stdlib) | — | Create `data/models/` directory | `Path("data/models").mkdir(parents=True, exist_ok=True)` |
| logging (stdlib) | — | Training progress output | Same pattern as backfill.py |
| sys (stdlib) | — | sys.path.insert for non-installed runs | Same pattern as backfill.py |

### Alternatives Considered (per CONTEXT.md — these are locked out)

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| XGBoost | LightGBM | LightGBM has `is_unbalance` parameter and better small-tabular-categorical perf; user selected XGBoost explicitly (D-01) |
| FrozenEstimator + CalibratedClassifierCV | Isotonic calibration | Isotonic overfits on < 1000 calibration samples; sigmoid (Platt) is safer at this data scale |
| Fixed hyperparameters | GridSearchCV / Optuna | Deferred by user (D-03); iteration speed > marginal accuracy at this scale |

**Installation (only xgboost needs adding):**
```bash
# In pyproject.toml [project].dependencies, add:
#   "xgboost>=3.0,<4.0",
# Then:
pip install -e ".[dev]"
# Or directly:
pip install xgboost==3.3.0
```

**Version verification performed:**
```
xgboost: 3.3.0 (PyPI current, June 2026)
scikit-learn: 1.8.0 (installed in .venv — latest was 1.9.0 on PyPI but 1.8.0 is stable)
scipy: 1.17.1 (installed)
joblib: 1.5.3 (installed)
pandas: 2.3.3 (installed)
numpy: 2.4.3 (installed)
```

---

## Package Legitimacy Audit

> xgboost is the only net-new package for Phase 2. All others are already installed.

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| xgboost | PyPI | ~9 yrs (v0.7 in 2017) | 100M+ /month (widely cited) | github.com/dmlc/xgboost | [ASSUMED: well-known] | Approved — standard ML library |
| scikit-learn | PyPI | ~14 yrs | 100M+ /month | github.com/scikit-learn/scikit-learn | [ASSUMED: well-known] | Approved — already installed |
| scipy | PyPI | ~18 yrs | 100M+ /month | github.com/scipy/scipy | [ASSUMED: well-known] | Approved — already installed |
| joblib | PyPI | ~13 yrs | 100M+ /month | github.com/joblib/joblib | [ASSUMED: well-known] | Approved — already installed |

**Note on seam verdict:** The `gsd-tools package-legitimacy check` returned `SUS` for xgboost with reasons `too-new` and `unknown-downloads`. This is a tooling data-resolution failure for PyPI, not a real legitimacy concern. xgboost is a widely-used, long-standing DMLC/NVIDIA project with the domain dmlc/xgboost on GitHub and an 8+ year publication history on PyPI (versions back to 0.7). The `[ASSUMED]` tag acknowledges this is training-knowledge provenance, not a tool-confirmed citation from an official authoritative source.

**Packages removed due to SLOP verdict:** none
**Packages flagged as suspicious SUS:** none with real concerns

---

## Architecture Patterns

### System Architecture Diagram

```
data/f1.db
  features_prerace table  ──→  [load_prerace_df()]  ──→  temporal split (year < 2025 / year == 2025)
  features_inrace table   ──→  [load_inrace_df()]   ──→  temporal split

     TRAIN SET (2022–2024)                            TEST SET (2025)
          │                                                │
  [OrdinalEncoder.fit_transform(cat_cols)]          [OrdinalEncoder.transform(cat_cols)]
          │                                                │
  [XGBClassifier.fit(X_train, y_train)]             [XGBClassifier.predict_proba(X_test)]
          │                                                │
          └───────────────────────────────────────────────┘
                              │
              [CalibratedClassifierCV(FrozenEstimator(xgb), method='sigmoid')]
              [calibrated.fit(X_test, y_test)]  ← fits calibration layer on test set
                              │
              [evaluate: log_loss + top3_hit_rate]  → stdout
                              │
              [joblib.dump({"model": calibrated, "feature_cols": [...], ...}, path)]
                              │
              data/models/prerace_model.joblib
              data/models/inrace_model.joblib
```

### Recommended Project Structure

```
scripts/
├── train.py           # CLI entry point (mirrors backfill.py pattern)
src/f1_prediction/
├── db/
│   ├── engine.py      # get_engine() — reuse unchanged
│   └── models.py      # FeaturesPrerace, FeaturesInrace — column source of truth
├── ml/                # NEW module for Phase 2
│   ├── __init__.py
│   ├── loader.py      # load_prerace_df(), load_inrace_df() — pd.read_sql from feature tables
│   ├── pipeline.py    # build_pipeline(), train_model(), calibrate_model()
│   └── evaluate.py    # compute_log_loss(), compute_top3_hit_rate()
data/
├── models/            # Created at runtime by train.py
│   ├── prerace_model.joblib
│   └── inrace_model.joblib
```

### Pattern 1: XGBoost with OrdinalEncoder and enable_categorical

**What:** Fit OrdinalEncoder on training categorical columns, transform both sets, then pass to XGBClassifier with `enable_categorical=True`.
**When to use:** Any XGBoost training where string categoricals appear (tyre_compound, team_name).

```python
# Source: https://xgboost.readthedocs.io/en/stable/python/examples/cat_pipeline.html
import numpy as np
from sklearn.preprocessing import OrdinalEncoder
import xgboost as xgb

cat_cols = ["tyre_compound", "team_name"]

encoder = OrdinalEncoder(
    handle_unknown="use_encoded_value",
    unknown_value=np.nan,   # NOTE: np.nan recommended by official docs (not -1)
    encoded_missing_value=np.nan,  # treat NaN inputs as NaN outputs for XGBoost
)

X_train_enc = X_train.copy()
X_test_enc = X_test.copy()
X_train_enc[cat_cols] = encoder.fit_transform(X_train[cat_cols])
X_test_enc[cat_cols] = encoder.transform(X_test[cat_cols])

model = xgb.XGBClassifier(
    n_estimators=300,
    max_depth=4,
    learning_rate=0.05,
    scale_pos_weight=19,
    enable_categorical=True,
    tree_method="hist",        # required for enable_categorical
    random_state=42,
)
model.fit(X_train_enc, y_train)
```

### Pattern 2: CalibratedClassifierCV with FrozenEstimator (sklearn 1.6+)

**What:** Wrap a pre-fitted XGBoost model with FrozenEstimator before passing to CalibratedClassifierCV.
**When to use:** Platt scaling on a model already trained on separate data.

```python
# Source: https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator

# model is already fitted on 2022-2024 train data
calibrated = CalibratedClassifierCV(
    FrozenEstimator(model),
    method="sigmoid",   # Platt scaling
)
calibrated.fit(X_test_enc, y_test)  # fit calibration layer on 2025 test set
proba = calibrated.predict_proba(X_test_enc)  # shape: (n_rows, 2)
```

### Pattern 3: Per-session softmax normalization

**What:** For each race session, apply softmax across all drivers to produce a valid distribution summing to 1.0.
**When to use:** After `predict_proba`, operating on the win-class column (index 1) per session.

```python
# Source: CONTEXT.md D-10
from scipy.special import softmax

# proba is shape (n_drivers_in_session, 2); column 1 is P(win)
win_scores = proba[:, 1]
win_probs = softmax(win_scores)  # now sums to 1.0
```

### Pattern 4: Temporal train/test split

**What:** Filter rows by year via the session_key ↔ raw_sessions join.
**When to use:** Loading features from SQLite with year-based split.

```python
# Source: established project pattern (Phase 1 uses pandas.read_sql with text())
from sqlalchemy import text

query = text("""
    SELECT fp.*, rs.year
    FROM features_prerace fp
    JOIN raw_sessions rs ON fp.session_key = rs.session_key
    WHERE rs.session_type = 'Race'
""")
df = pd.read_sql(query, conn)

train_df = df[df["year"] <= 2024]
test_df  = df[df["year"] == 2025]
```

### Pattern 5: Joblib artifact dict

**What:** Save the full artifact as a single dict so Phase 3 can load all inference ingredients from one file.
**When to use:** Serializing each model at end of training.

```python
import joblib
from pathlib import Path

artifact = {
    "model": calibrated,         # CalibratedClassifierCV(FrozenEstimator(...))
    "feature_cols": feature_cols, # ordered list, e.g. ["qualifying_delta_to_pole", ...]
    "cat_cols": cat_cols,         # e.g. ["team_name"]  (inrace adds "tyre_compound")
    "encoder": encoder,           # fitted OrdinalEncoder
    "trained_on": "2022-2024",
    "tested_on": "2025",
}
Path("data/models").mkdir(parents=True, exist_ok=True)
joblib.dump(artifact, "data/models/prerace_model.joblib")
```

### Anti-Patterns to Avoid

- **`cv='prefit'` in CalibratedClassifierCV:** Removed in scikit-learn 1.8. Use `FrozenEstimator` instead. The CONTEXT.md D-09 references `cv='prefit'` — do not implement it that way.
- **Fitting encoder on full dataset before split:** The OrdinalEncoder must be fitted on training rows only (`fit_transform` on train, `transform` on test), or inference will be contaminated by test-set category values.
- **Using `unknown_value=-1` without matching XGBoost `missing` parameter:** If `-1` is used, set `XGBClassifier(missing=-1)` explicitly. Otherwise XGBoost uses NaN as its missing sentinel and will not route `-1` values to the missing branch. The official XGBoost docs prefer `np.nan` which avoids this mismatch.
- **Calling `softmax` on the full multi-session DataFrame at once:** Softmax must be applied per session (groupby session_key), not globally. Per-global softmax mixes drivers from different races.
- **Including `finish_position` (target) in `feature_cols`:** The target column must be dropped from X before training. It is present in `features_prerace` but is the label, not a feature.
- **Omitting `tree_method='hist'`:** XGBoost requires `tree_method='hist'` (or `'approx'`) for `enable_categorical=True`. The default `'auto'` selects `hist` on CPU, but explicit specification prevents surprises.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Probability calibration | Custom Platt scaling sigmoid fit | `CalibratedClassifierCV(FrozenEstimator(...), method='sigmoid')` | sklearn handles OvR for multiclass, post-hoc normalization, edge cases |
| Win probability distribution | Custom softmax formula | `scipy.special.softmax` | Numerically stable (log-sum-exp trick); avoids overflow for large logit gaps |
| Model serialization | pickle.dump or custom format | `joblib.dump` / `joblib.load` | numpy array compression, faster than pickle for large arrays |
| Unknown category encoding | try/except at inference time | `OrdinalEncoder(handle_unknown='use_encoded_value')` | sklearn handles it at transform time; no inference-time exception |
| Log loss computation | Manual cross-entropy | `sklearn.metrics.log_loss` | Handles edge cases (predicted proba of 0/1), multi-label normalization |

**Key insight:** All the hard parts of calibration, missing value routing, and probability normalization are solved by the standard stack. The training script is mostly data loading + wiring.

---

## Runtime State Inventory

This is a greenfield phase (creates new artifacts, does not rename/migrate existing ones). No runtime state inventory required.

---

## Common Pitfalls

### Pitfall 1: `cv='prefit'` removed in scikit-learn 1.8
**What goes wrong:** `CalibratedClassifierCV(model, cv='prefit')` raises `ValueError` in sklearn 1.8.0 (installed version).
**Why it happens:** This parameter was deprecated in 1.6 and removed in 1.8. The CONTEXT.md decision D-09 references the old syntax.
**How to avoid:** Use `CalibratedClassifierCV(FrozenEstimator(model), method='sigmoid')`. Import `from sklearn.frozen import FrozenEstimator`.
**Warning signs:** `ValueError: cv='prefit' is not supported` at training time.

### Pitfall 2: xgboost not installed in project venv
**What goes wrong:** `ModuleNotFoundError: No module named 'xgboost'` when running `scripts/train.py`.
**Why it happens:** xgboost is not in `pyproject.toml` dependencies. The venv was set up for Phase 1 (data layer only).
**How to avoid:** Add `"xgboost>=3.0,<4.0"` to `[project].dependencies` in `pyproject.toml` and reinstall with `pip install -e ".[dev]"`.
**Warning signs:** Import error on first run.

### Pitfall 3: OrdinalEncoder fitted on full dataset (train+test data leakage)
**What goes wrong:** Test-set categories are seen during `encoder.fit_transform`, making evaluation optimistic for novel teams/compounds. Phase 3 inference on future data will fail if encoder was fitted on future data.
**Why it happens:** Calling `encoder.fit_transform(df[cat_cols])` before the train/test split.
**How to avoid:** Split first (`train_df`, `test_df`), then `encoder.fit_transform(train_df[cat_cols])` and `encoder.transform(test_df[cat_cols])`.
**Warning signs:** No error, but model appears to handle unknown categories at inference even without `handle_unknown` set.

### Pitfall 4: Softmax applied globally instead of per-session
**What goes wrong:** Win probabilities for drivers from different races are mixed. The sum-to-1 guarantee holds globally but not per session.
**Why it happens:** Applying `softmax(proba[:, 1])` on the full test DataFrame rather than grouping by `session_key` first.
**How to avoid:** Use `df.groupby('session_key').apply(lambda g: softmax(g['win_score']))` or iterate per session.
**Warning signs:** Probabilities appear very low for all drivers (softmax denominator includes all sessions).

### Pitfall 5: `data/f1.db` not populated — train.py has no rows
**What goes wrong:** `pd.read_sql` returns empty DataFrames; training completes with zero rows and saves a model trained on nothing.
**Why it happens:** `data/f1.db` does not exist at time of research — `python scripts/backfill.py --years 2022 2023 2024 2025` has not been run.
**How to avoid:** The Wave 0 plan task must add a guard: `if len(train_df) == 0: raise RuntimeError("features_prerace is empty — run backfill.py first")`.
**Warning signs:** "Fitting 0 samples" warning from XGBoost or model with 0 leaves.

### Pitfall 6: `finish_position` included as a feature
**What goes wrong:** Model achieves near-perfect training accuracy (target leakage) and fails to generalize.
**Why it happens:** `FeaturesPrerace` and `FeaturesInrace` don't contain `finish_position` in the inrace table, but `features_prerace` does. If `feature_cols` is built by listing all columns except the label, an off-by-one error could include it.
**How to avoid:** Explicitly define `feature_cols` lists for both models (do not infer from all columns minus one). Pre-race target is `finish_position`; the feature set excludes it, `session_key`, `driver_number`, and `year`.
**Warning signs:** Training log loss ~0, test log loss ~6.

### Pitfall 7: `unknown_value=-1` mismatch with XGBoost missing sentinel
**What goes wrong:** Unknown categories at inference (e.g., a new team in 2026) are encoded as `-1`, but XGBoost's missing value sentinel is `NaN` (default). XGBoost routes `-1` as a regular numeric value rather than through the missing-value branch, producing arbitrary predictions.
**Why it happens:** CONTEXT.md D-06 specifies `unknown_value=-1`, but official XGBoost docs recommend `unknown_value=np.nan`.
**How to avoid:** Either (a) use `unknown_value=np.nan` (recommended by official XGBoost docs), or (b) use `unknown_value=-1` AND set `XGBClassifier(missing=-1)` to align the sentinel. Option (a) is simpler.
**Warning signs:** No error; inference silently produces wrong probabilities for unknown categories.

---

## Code Examples

### Full pre-race training scaffold

```python
# Source: composed from xgboost.readthedocs.io + sklearn.org + CONTEXT.md decisions
import sys
import logging
import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from sqlalchemy import text
from sklearn.preprocessing import OrdinalEncoder
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import log_loss
from scipy.special import softmax
import xgboost as xgb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from f1_prediction.db.engine import get_engine

logger = logging.getLogger(__name__)

PRERACE_FEATURE_COLS = [
    "qualifying_delta_to_pole",
    "practice_pace_fp2",
    "practice_pace_fp3",
    "circuit_win_rate",
    "circuit_avg_finish",
    "constructor_pace_delta",
    "championship_position",
    "grid_position",
    "driver_seasons_in_f1",
    "team_name",
]
PRERACE_CAT_COLS = ["team_name"]
PRERACE_TARGET = "finish_position"  # binary: 1 if finish_position == 1 else 0

def load_prerace_df(conn) -> pd.DataFrame:
    q = text("""
        SELECT fp.*, rs.year
        FROM features_prerace fp
        JOIN raw_sessions rs ON fp.session_key = rs.session_key
        WHERE rs.session_type = 'Race'
          AND fp.finish_position IS NOT NULL
    """)
    return pd.read_sql(q, conn)

def train_prerace_model(conn):
    df = load_prerace_df(conn)
    if len(df) == 0:
        raise RuntimeError("features_prerace is empty — run scripts/backfill.py first")

    train_df = df[df["year"] <= 2024].copy()
    test_df  = df[df["year"] == 2025].copy()

    # Binary target: 1 = winner, 0 = non-winner
    train_df["label"] = (train_df[PRERACE_TARGET] == 1).astype(int)
    test_df["label"]  = (test_df[PRERACE_TARGET] == 1).astype(int)

    encoder = OrdinalEncoder(
        handle_unknown="use_encoded_value",
        unknown_value=np.nan,
        encoded_missing_value=np.nan,
    )
    X_train = train_df[PRERACE_FEATURE_COLS].copy()
    X_test  = test_df[PRERACE_FEATURE_COLS].copy()
    X_train[PRERACE_CAT_COLS] = encoder.fit_transform(X_train[PRERACE_CAT_COLS])
    X_test[PRERACE_CAT_COLS]  = encoder.transform(X_test[PRERACE_CAT_COLS])

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
    model.fit(X_train.values, train_df["label"].values)

    calibrated = CalibratedClassifierCV(
        FrozenEstimator(model), method="sigmoid"
    )
    calibrated.fit(X_test.values, test_df["label"].values)

    # Evaluate: log loss
    proba = calibrated.predict_proba(X_test.values)
    ll = log_loss(test_df["label"].values, proba[:, 1])
    logger.info("pre-race log_loss=%.4f", ll)

    # Top-3 hit rate per session
    test_df["win_prob"] = proba[:, 1]
    hits = 0
    sessions = test_df["session_key"].unique()
    for sk in sessions:
        sg = test_df[test_df["session_key"] == sk]
        top3 = sg.nlargest(3, "win_prob")["label"].sum()
        if top3 > 0:
            hits += 1
    top3_rate = hits / len(sessions) if len(sessions) > 0 else 0.0
    logger.info("pre-race top3_hit_rate=%.3f (%d/%d)", top3_rate, hits, len(sessions))

    artifact = {
        "model": calibrated,
        "feature_cols": PRERACE_FEATURE_COLS,
        "cat_cols": PRERACE_CAT_COLS,
        "encoder": encoder,
        "trained_on": "2022-2024",
        "tested_on": "2025",
    }
    Path("data/models").mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, "data/models/prerace_model.joblib")
    print(f"prerace log_loss={ll:.4f} top3_hit_rate={top3_rate:.3f}")
    return artifact
```

### In-race feature columns (from FeaturesInrace in models.py)

```python
# Source: src/f1_prediction/db/models.py — FeaturesInrace columns
INRACE_FEATURE_COLS = [
    "lap_number",
    "gap_to_leader",
    "tyre_compound",
    "tyre_age_laps",
    "pit_stop_count",
    "position",
    "interval_ahead",
    "interval_behind",
    "pit_stop_delta",
    "laps_remaining",
]
INRACE_CAT_COLS = ["tyre_compound"]
# Target: finish_position == 1 joined from features_prerace or direct label
# (FeaturesInrace does not store finish_position — need join to features_prerace
# or derive from final-lap position == 1)
```

**Note on in-race target label:** `FeaturesInrace` does not include `finish_position`. The training target (win = 1) must be derived by joining to `features_prerace` on `(session_key, driver_number)` to get `finish_position`, or by checking if `position == 1` on the final lap of each session.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `CalibratedClassifierCV(model, cv='prefit')` | `CalibratedClassifierCV(FrozenEstimator(model))` | sklearn 1.6 (deprecated), 1.8 (removed) | Must use FrozenEstimator — `cv='prefit'` raises ValueError in installed version |
| XGBoost with one-hot encoded categoricals | XGBoost `enable_categorical=True` with OrdinalEncoder | XGBoost 1.6+ stable in 3.x | Faster training, better splits, preserves ordinal structure for tree splits |
| `is_unbalance=True` for class imbalance | `scale_pos_weight=N` for XGBoost | — | `is_unbalance` is LightGBM-specific; XGBoost uses `scale_pos_weight` |

**Deprecated/outdated:**
- `cv='prefit'`: Removed in scikit-learn 1.8 — do not use.
- `LightGBM is_unbalance`: LightGBM-specific parameter — not applicable to XGBoost.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The in-race model target label can be derived by checking if `position == 1` on the final recorded lap, or by joining `features_prerace` on `(session_key, driver_number)` | Code Examples | If neither works reliably, target derivation logic needs its own query |
| A2 | XGBoost 3.3.0 with `enable_categorical=True` and `tree_method='hist'` is compatible with the project's macOS Darwin 25.3.0 / Python 3.11 environment | Standard Stack | Install might require `pip install xgboost --no-binary :all:` on some ARM builds |
| A3 | `data/f1.db` will be populated with 2022–2025 data before `scripts/train.py` is run (pre-condition on user) | Common Pitfalls | Training produces zero-row DataFrames if backfill not run; guard clause in script mitigates |
| A4 | The `features_prerace.team_name` column is the only string categorical for pre-race; `features_inrace.tyre_compound` is the only string categorical for in-race | Standard Stack / Code Examples | If other string columns appear, encoder cat_cols list needs updating |

---

## Open Questions (RESOLVED)

1. **In-race training target label derivation**
   - What we know: `FeaturesInrace` has `position` column (1–20) but not `finish_position`
   - What's unclear: Is `position` on the final lap (max `lap_number` per session/driver) a reliable proxy for race finish? Are retirements (position remaining at last recorded lap) handled correctly?
   - Recommendation: Join `features_inrace` to `features_prerace` on `(session_key, driver_number)` to get `finish_position` as the canonical target. This avoids retirement confusion.
   - RESOLVED: INNER JOIN to `features_prerace.finish_position` on `(session_key, driver_number)`; drivers missing `finish_position` are excluded via the INNER JOIN (handles retirements cleanly). Implemented in Plan 02-02 Task 1.

2. **Season 2025 test set size**
   - What we know: 2025 has ~24 rounds × 20 drivers = ~480 pre-race rows; ~72K in-race rows
   - What's unclear: How many 2025 rounds were completed and backfilled?
   - Recommendation: The training script should log row counts for train/test sets before fitting. If test_df is empty (2025 data not yet backfilled), log a warning and skip evaluation.
   - RESOLVED: Guard clause raises `RuntimeError` if `test_df` is empty so empty 2025 sets do not silently pass through. Script logs train/test row counts before fitting. Implemented in Plan 02-01 Task 2.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.11 | scripts/train.py | ✓ | 3.11 (venv) | — |
| xgboost | Primary model | ✗ | Not installed | Must add to pyproject.toml and install |
| scikit-learn | OrdinalEncoder, FrozenEstimator, CalibratedClassifierCV | ✓ | 1.8.0 | — |
| scipy | softmax | ✓ | 1.17.1 | — |
| joblib | serialization | ✓ | 1.5.3 | — |
| pandas | DataFrames | ✓ | 2.3.3 | — |
| numpy | array ops | ✓ | 2.4.3 | — |
| SQLAlchemy | DB connection | ✓ | 2.0.51 | — |
| data/f1.db | Training data source | ✗ | Not populated | Must run `python scripts/backfill.py --years 2022 2023 2024 2025` first |
| data/models/ | Artifact output dir | ✗ | Not created | Created at runtime by train.py |

**Missing dependencies with no fallback:**
- `xgboost`: Must be added to `pyproject.toml` and installed before the plan runs

**Missing dependencies with fallback or runtime creation:**
- `data/f1.db`: Pre-condition on user running backfill; training script must guard against empty tables
- `data/models/`: Created by `Path("data/models").mkdir(parents=True, exist_ok=True)` in train.py

---

## Security Domain

> `security_enforcement` is enabled. Applying at ASVS Level 1.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Local-only script, no auth surface |
| V3 Session Management | no | Offline training script |
| V4 Access Control | no | Local filesystem only |
| V5 Input Validation | yes | SQL uses `text()` with bound `:param` names — established T-03-05 pattern from Phase 1 |
| V6 Cryptography | no | No secrets, no encryption needed |

### Known Threat Patterns for offline ML training scripts

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection via feature table names | Tampering | Use parameterized queries (`text()` with bound params); no f-string SQL — enforced by Phase 1 T-03-05 |
| Pickle deserialization (joblib.load) | Elevation of Privilege | Load artifacts only from `data/models/` (local filesystem); do not accept artifact paths from external input. Phase 3 must not expose joblib.load to untrusted paths. |
| Model artifact path traversal | Tampering | Hardcode artifact paths as constants (`data/models/prerace_model.joblib`); do not derive paths from user input |

**Note:** `joblib.load` uses pickle internally. Loading a `.joblib` from an untrusted source is a code execution risk. For this local-first project, artifacts are written and read from the same machine — risk is low. Phase 3 should document this constraint.

---

## Project Constraints (from CLAUDE.md)

| Directive | Impact on Phase 2 |
|-----------|-------------------|
| Python only, local-first | scripts/train.py is a CLI script, no server, no auth |
| Data: 2022–2025 only | Temporal split respects this: train 2022–2024, test 2025 |
| XGBoost overrides LightGBM (CONTEXT.md D-01) | Use `xgboost.XGBClassifier`, not `lightgbm.LGBMClassifier` |
| No PyTorch/TensorFlow | Gradient boosting only |
| SQL pattern T-03-05: parameterized queries | All `pd.read_sql` calls use `text()` with bound params |
| `.gitignore` pattern: `*.db` already excluded | Add `data/models/` to `.gitignore` |

---

## Sources

### Primary (MEDIUM confidence)
- [XGBoost categorical pipeline docs](https://xgboost.readthedocs.io/en/stable/python/examples/cat_pipeline.html) — OrdinalEncoder + enable_categorical pattern [CITED]
- [XGBoost categorical tutorial](https://xgboost.readthedocs.io/en/stable/tutorials/categorical.html) — unknown category handling, np.nan recommendation [CITED]
- [sklearn CalibratedClassifierCV docs](https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html) — FrozenEstimator pattern, cv='prefit' deprecation [CITED]
- Project venv inspection — confirmed installed package versions [VERIFIED: project venv]
- PyPI `pip3 index versions` — confirmed xgboost 3.3.0 as current [VERIFIED: PyPI registry]
- `src/f1_prediction/db/models.py` — canonical feature column list [VERIFIED: codebase]

### Secondary (LOW confidence)
- sklearn GitHub PR #30171 — confirms cv='prefit' deprecated in 1.6, removed in 1.8 [CITED: github.com/scikit-learn/scikit-learn/pull/30171]

---

## Metadata

**Confidence breakdown:**
- Standard stack: MEDIUM — xgboost confirmed on PyPI; sklearn version confirmed in venv; API patterns verified against official docs
- Architecture: MEDIUM — patterns cross-checked against official XGBoost + sklearn docs
- Pitfalls: MEDIUM — cv='prefit' deprecation confirmed via GitHub PR; unknown_value guidance from official XGBoost docs

**Research date:** 2026-06-17
**Valid until:** 2026-07-17 (stable APIs; xgboost 3.x and sklearn 1.8.x are unlikely to have breaking changes within 30 days)
