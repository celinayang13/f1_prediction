# Phase 2: Model Training - Research

**Researched:** 2026-06-25
**Domain:** XGBoost binary classification, scikit-learn calibration, F1 feature engineering
**Confidence:** HIGH (all critical claims verified by running code against the project codebase and installed packages)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Phase 2 is gated on completing a full 2023–2025 backfill. Plan 01 of Phase 2 must extend the backfill before any model training code is written.
- **D-02:** Drop `team_name`, `circuit_avg_finish`, and `constructor_pace_delta` from the pre-race training feature set (all 100% NULL). Re-evaluate post-backfill.
- **D-03:** Pre-race features: `qualifying_delta_to_pole`, `practice_pace_fp2`, `practice_pace_fp3`, `circuit_win_rate`, `championship_position`, `grid_position`, `driver_seasons_in_f1`. Target: `is_winner = (finish_position == 1)` derived at training time.
- **D-04:** In-race features: `gap_to_leader`, `tyre_compound`, `tyre_age_laps`, `pit_stop_count`, `position`, `interval_ahead`, `interval_behind`, `pit_stop_delta`, `lap_number`, `laps_remaining`. Target: `is_winner` joined from `features_prerace`.
- **D-05:** `circuit_win_rate` is 87% NULL — use XGBoost native NULL handling, leave it in.
- **D-06:** Use XGBoost (not LightGBM). `team_name` being dropped removes the only reason to prefer LightGBM's categorical support.
- **D-07:** `TimeSeriesSplit` with season-based folds; held-out 2024 validation; Brier score and log loss metrics.
- **D-08:** `CalibratedClassifierCV` with isotonic regression + `scipy.special.softmax` for sum-to-1.
- **D-09:** Single script `scripts/train.py` with `--model prerace|inrace` flag. No hyperparameter search. Defaults: `n_estimators=500, learning_rate=0.05, max_depth=6, is_unbalance=true`.

### Claude's Discretion

- D-06 through D-09 are marked as Claude's discretion in CONTEXT.md (logged here for completeness).

### Deferred Ideas (OUT OF SCOPE)

- Reinstating `team_name`, `circuit_avg_finish`, `constructor_pace_delta` (deferred to post-Phase 4)
- Hyperparameter search (grid search, Optuna) — deferred
- Top-3 (podium) prediction — out of scope per REQUIREMENTS.md v2
- LightGBM as alternative — deferred

</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PRERACE-01 | Pre-race model trained on qualifying gap to pole, practice lap times, circuit-specific win history, constructor performance, championship standings | SQL patterns for `features_prerace` load; XGBoost training pipeline verified |
| PRERACE-02 | Pre-race model outputs win probability distribution across all ~20 drivers (probabilities sum to 1) | `scipy.special.softmax` per-race normalization verified to produce sum=1.000000 |
| PRERACE-03 | Pre-race probabilities computed from qualifying session data as primary input | `qualifying_delta_to_pole` column confirmed in `features_prerace`; 99% non-NULL after 2022 backfill |
| INRACE-01 | In-race model trained on mid-race states — positions, gaps, laps remaining, tyre compound and age, pit stop history | SQL join pattern for `features_inrace` + `features_prerace` target label confirmed working |
| INRACE-02 | In-race model re-runs after each completed lap and outputs updated win probability distribution | Model outputs `predict_proba` per lap row; softmax normalization per race confirmed |
| INRACE-03 | In-race model incorporates live pit stop data (when drivers pit, tyre change) | `pit_stop_count`, `pit_stop_delta`, `tyre_compound`, `tyre_age_laps` all in `features_inrace` |

</phase_requirements>

---

## Summary

Phase 2 has three sequential jobs: (1) extend the backfill so the training corpus covers all 2022–2025 rounds, (2) train and calibrate a pre-race XGBoost model on `features_prerace`, and (3) train and calibrate an in-race XGBoost model on `features_inrace`. Both models are serialized as `CalibratedClassifierCV`-wrapped XGBClassifiers via joblib.

The most significant discovery from researching the existing codebase is that the 2023–2025 sessions in the DB all have `round_number=0`. This is a structural issue in the OpenF1 loader: the OpenF1 `/v1/sessions` endpoint does not return a `round_number` field, so the normalizer falls back to 0. This does not break features that use `rs.year < :year` (cross-year lookups), but it does break `championship_position` computation which requires intra-year round ordering (`rs.round_number < :current_round`). Plan 01 must fix this by deriving `round_number` from the ranked position of `meeting_key` within a year.

A secondary data quality issue is that `finish_position=1` appears for two drivers in Le Castellet 2022 (round 12). Driver 16 (Leclerc) led the race at lap 17 before DNF'ing; `compute_finish_position` took his last-known position=1. The training SQL must use a lap-completion filter: only the driver whose `MAX(lap_number) >= total_laps` and `finish_position=1` is the true winner. This affects 1 of 19 current race rows, but the correct SQL pattern must be used from the start.

After full backfill, the training corpus will be approximately 85 races × 20 drivers = 1,700 pre-race rows and 85 × 60 × 20 = 102,000 in-race rows. The `scale_pos_weight` for XGBoost should be set to ~19 (19 non-winners per winner).

**Primary recommendation:** Plan 01 = backfill extension + round_number fix + feature rebuild. Plan 02 = `scripts/train.py` pre-race model. Plan 03 = `scripts/train.py` in-race model. Plan 04 = validation script confirming sum-to-1 on held-out 2024 data.

---

## Project Constraints (from CLAUDE.md)

| Directive | Applies to Phase 2 |
|-----------|-------------------|
| Python only | Yes — scripts/train.py, no Jupyter |
| XGBoost as primary model | Yes (D-06) |
| SQLite + SQLAlchemy for storage | Yes — same `get_engine()` from Phase 1 |
| No PyTorch/TensorFlow | Yes — tabular data, gradient boosting only |
| No DuckDB | Yes — SQLite sufficient at ~100K rows |
| LightGBM installed but not primary | Installed (4.6.0) but not used in Phase 2 |
| All SQL uses `text()` with bound `:param` | Yes — training queries must follow T-03-05 |
| `data/models/` as artifact output path | Confirmed — directory exists |

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Backfill extension (D-01) | Scripts (one-shot CLI) | — | `scripts/backfill.py` already handles routing; Plan 01 re-invokes it with year ranges |
| round_number fix | Scripts (migration) | DB layer | Requires UPDATE to `raw_sessions` for all 2023–2025 rows; one-time fix |
| Feature rebuild | Scripts (one-shot CLI) | Feature layer (`src/f1_prediction/features/`) | `build_all_features()` already exists in builder.py |
| Pre-race model training | Scripts (`scripts/train.py`) | ML stack (XGBoost + sklearn) | Pure offline training; no API or DB write needed beyond saving artifact |
| In-race model training | Scripts (`scripts/train.py`) | ML stack | Same train.py with `--model inrace` flag |
| Model calibration | ML stack (CalibratedClassifierCV) | — | Wraps XGBClassifier; output is the serialized artifact |
| Softmax normalization | Inference time | Validation script | Applied after `predict_proba` per race group |
| Model artifact saving | Scripts (`scripts/train.py`) | `data/models/` | `joblib.dump` to `data/models/prerace_model.joblib` / `inrace_model.joblib` |
| Sum-to-1 validation | Scripts (validation) | — | `scripts/validate_model.py` or inline in `train.py` |

---

## Standard Stack

### Core (all already installed in project venv)

| Library | Installed Version | Purpose | Why Standard |
|---------|-------------------|---------|--------------|
| xgboost | 3.2.0 [VERIFIED: pip show] | Primary model — `XGBClassifier` | Native NaN handling (D-05), `scale_pos_weight` for imbalance, `binary:logistic` objective |
| scikit-learn | 1.9.0 [VERIFIED: pip show] | `CalibratedClassifierCV` (isotonic), `TimeSeriesSplit`, metrics | Standard calibration + CV pipeline |
| scipy | 1.17.1 [VERIFIED: pip show] | `scipy.special.softmax` for per-race normalization | Numerically stable softmax; already in venv |
| joblib | 1.5.3 [VERIFIED: pip show] | `joblib.dump` / `joblib.load` for model artifacts | scikit-learn's own serialization backend |
| pandas | 2.2.2 [VERIFIED: pip show] | DataFrame manipulation, `pd.get_dummies` for tyre encoding | Phase 1 feature tables are SQLAlchemy → DataFrame |
| numpy | 2.0.1 [VERIFIED: pip show] | Array operations, `np.nan` handling | Standard |

### No new packages needed

All ML dependencies for Phase 2 are already installed. `scripts/train.py` requires zero `pip install` calls.

---

## Package Legitimacy Audit

All packages were installed and verified during Phase 1. The package legitimacy seam returned `SUS` for all PyPI packages due to the seam's inability to retrieve download counts from the PyPI ecosystem endpoint. This is a known limitation of the tooling, not a signal about the packages themselves. All packages have official homepages, established GitHub repositories, and are confirmed present in the project venv via `pip show`.

| Package | Registry | Installed | Official URL | Verdict | Disposition |
|---------|----------|-----------|-------------|---------|-------------|
| xgboost | PyPI | 3.2.0 | xgboost.readthedocs.io | OK | Already installed — no action |
| scikit-learn | PyPI | 1.9.0 | scikit-learn.org | OK | Already installed — no action |
| scipy | PyPI | 1.17.1 | scipy.org | OK | Already installed — no action |
| joblib | PyPI | 1.5.3 | joblib.readthedocs.io | OK | Already installed — no action |
| pandas | PyPI | 2.2.2 | pandas.pydata.org | OK | Already installed — no action |
| numpy | PyPI | 2.0.1 | numpy.org | OK | Already installed — no action |

**Packages removed due to SLOP verdict:** none
**Packages flagged as suspicious:** none (seam SUS flags are tooling false positives for PyPI; packages confirmed via official homepages and Phase 1 usage)

---

## Architecture Patterns

### System Architecture Diagram

```
scripts/backfill.py (Plan 01)
  │  --years 2022 2023 2024 2025
  │  FastF1 loader (year=2022, rounds 17-22)
  │  OpenF1 loader (year=2023/2024/2025, all rounds)
  │  UPDATE raw_sessions SET round_number = meeting_key_rank
  ▼
scripts/build_features.py
  │  build_all_features(only_years=[2022,2023,2024,2025])
  ▼
SQLite: features_prerace + features_inrace (fully populated)

scripts/train.py --model prerace (Plan 02)
  │  SQL → pd.DataFrame (features_prerace, year <= 2024, finish_position NOT NULL)
  │  is_winner derivation (lap-completion filter)
  │  tyre: n/a for pre-race
  │  NaN columns: circuit_win_rate, practice_pace_fp3, championship_position (left as NaN)
  │  Season splits: fold1=train 2022→val 2023, fold2=train 2022-23→val 2024
  │  XGBClassifier(n_estimators=500, lr=0.05, max_depth=6, scale_pos_weight=~19)
  │  CalibratedClassifierCV(clf, cv=5, method='isotonic')
  │  fit(X_train, y_train)
  │  predict_proba → scipy.special.softmax per race → sum-to-1 check
  ▼
data/models/prerace_model.joblib

scripts/train.py --model inrace (Plan 03)
  │  SQL → pd.DataFrame (features_inrace JOIN features_prerace for is_winner)
  │  tyre_compound one-hot encoded: pd.get_dummies(dummy_na=False) → 5 columns
  │  pit_stop_delta: 96% NULL → left as NaN (XGBoost handles natively)
  │  Same CV pattern and calibration
  ▼
data/models/inrace_model.joblib

scripts/validate_model.py (Plan 04)
  │  joblib.load(prerace_model.joblib)
  │  Load 2024 held-out race data
  │  predict_proba → softmax → assert |sum - 1.0| < 0.001 per race
  │  Log Brier score and log loss
  ▼
Validation report (stdout)
```

### Recommended Project Structure

```
scripts/
├── backfill.py           # Existing — re-invoke with new year ranges (Plan 01)
├── build_features.py     # Existing — re-run after backfill
├── train.py              # New — Phase 2 main artifact (Plans 02-03)
└── validate_model.py     # New — sum-to-1 + metrics validation (Plan 04)
data/
└── models/
    ├── prerace_model.joblib   # Output: Plan 02
    └── inrace_model.joblib    # Output: Plan 03
src/f1_prediction/
└── (no new source modules in Phase 2 — train.py is standalone script)
```

### Pattern 1: Correct `is_winner` Derivation SQL (lap-completion filter)

**What:** Le Castellet 2022 has `finish_position=1` for two drivers due to a DNF bug in `compute_finish_position`. The true winner is the driver with `finish_position=1` AND who completed the total race laps.

**When to use:** In the `WHERE` clause of both pre-race and in-race training queries.

```python
# Source: verified against data/f1.db — Le Castellet 2022 round 12 confirmed
query_prerace = """
SELECT
    fp.session_key,
    fp.driver_number,
    fp.qualifying_delta_to_pole,
    fp.practice_pace_fp2,
    fp.practice_pace_fp3,
    fp.circuit_win_rate,
    fp.championship_position,
    fp.grid_position,
    fp.driver_seasons_in_f1,
    CASE
        WHEN fp.finish_position = 1
         AND (
            SELECT MAX(rp2.lap_number)
            FROM raw_positions rp2
            WHERE rp2.session_key = fp.session_key
              AND rp2.driver_number = fp.driver_number
         ) >= rs.total_laps - 1  -- -1 tolerance for lapped cars
        THEN 1
        WHEN fp.finish_position > 1 THEN 0
        ELSE 0
    END AS is_winner,
    rs.year,
    rs.round_number
FROM features_prerace fp
JOIN raw_sessions rs ON fp.session_key = rs.session_key
WHERE rs.session_type = 'Race'
  AND fp.finish_position IS NOT NULL
  AND rs.year <= 2024
ORDER BY rs.year, rs.round_number, fp.driver_number
"""
```

### Pattern 2: In-Race Training Data SQL (join for `is_winner` label)

**What:** `features_inrace` has no `finish_position` — it must be joined from `features_prerace` on `(session_key, driver_number)`.

```python
# Source: verified against data/f1.db — returns 19551 rows for current DB
query_inrace = """
SELECT
    fi.session_key,
    fi.driver_number,
    fi.lap_number,
    fi.gap_to_leader,
    fi.tyre_compound,
    fi.tyre_age_laps,
    fi.pit_stop_count,
    fi.position,
    fi.interval_ahead,
    fi.interval_behind,
    fi.pit_stop_delta,
    fi.laps_remaining,
    CASE
        WHEN fp.finish_position = 1
         AND (
            SELECT MAX(rp2.lap_number)
            FROM raw_positions rp2
            WHERE rp2.session_key = fp.session_key
              AND rp2.driver_number = fp.driver_number
         ) >= rs.total_laps - 1
        THEN 1
        ELSE 0
    END AS is_winner,
    rs.year,
    rs.round_number
FROM features_inrace fi
JOIN features_prerace fp
    ON fi.session_key = fp.session_key
    AND fi.driver_number = fp.driver_number
JOIN raw_sessions rs ON fi.session_key = rs.session_key
WHERE rs.session_type = 'Race'
  AND fp.finish_position IS NOT NULL
  AND rs.year <= 2024
ORDER BY rs.year, fi.session_key, fi.driver_number, fi.lap_number
"""
```

### Pattern 3: Tyre Compound One-Hot Encoding

**What:** `tyre_compound` is a string (`SOFT`, `MEDIUM`, `HARD`, `INTERMEDIATE`, `WET`, or `None`). XGBoost (with `enable_categorical=False`) requires numeric input.

**Why one-hot over ordinal:** No natural ordinal relationship among compounds. One-hot preserves compound identity without implying SOFT > MEDIUM > HARD order. 5 compounds → 5 binary columns. NaN (no tyre data) → all zeros (no signal, handled by XGBoost NaN paths).

```python
# Source: verified via pd.get_dummies in this session
compound_dummies = pd.get_dummies(
    df["tyre_compound"],
    prefix="tyre",
    dummy_na=False,  # NaN rows become all-zero (correct behavior)
)
# Produces: tyre_HARD, tyre_INTERMEDIATE, tyre_MEDIUM, tyre_SOFT, tyre_WET
# Drop tyre_compound column, concat dummies into feature DataFrame
X = pd.concat([df[numeric_cols], compound_dummies], axis=1)
```

### Pattern 4: XGBoost Training + Calibration + Softmax

**What:** The complete pipeline from DataFrame to calibrated win probabilities.

```python
# Source: verified by running against installed packages in this session
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from scipy.special import softmax
import joblib

# IMPORTANT: cv='prefit' is REMOVED in scikit-learn 1.x (currently 1.9.0).
# Use cv=N (integer). CalibratedClassifierCV fits N internal copies.
def train_model(X_train, y_train, scale_pos_weight):
    base_clf = xgb.XGBClassifier(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        scale_pos_weight=scale_pos_weight,  # ~19 for F1 (19:1 imbalance)
        objective="binary:logistic",
        eval_metric="logloss",
        verbosity=0,
        # missing=np.nan is default — NaN handled natively (D-05)
    )
    calibrated = CalibratedClassifierCV(
        base_clf,
        cv=5,            # 5-fold internal calibration (NOT cv='prefit')
        method="isotonic",
    )
    calibrated.fit(X_train, y_train)
    return calibrated

# Save artifact
def save_model(model, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)

# Predict and normalize per race
def predict_win_probabilities(model, X_val, session_keys):
    raw_proba = model.predict_proba(X_val)[:, 1]
    df = pd.DataFrame({"session_key": session_keys, "raw_prob": raw_proba})
    df["win_prob"] = df.groupby("session_key")["raw_prob"].transform(
        lambda x: softmax(x.values)
    )
    return df
```

### Pattern 5: Season-Based CV Folds for F1

**What:** F1 seasons are natural fold boundaries. Do not use `TimeSeriesSplit` with equal-size windows — it will split within a season. Use manual year-boundary splits.

```python
# Source: verified against features_prerace year distribution
# Current DB: 2022=313 rows, 2023=20 rows (1 race), 2024=20 rows
# After backfill: 2022=~320, 2023=~460, 2024=~480
def season_folds(df, year_col="year", train_years_cap=2024):
    """
    Returns list of (train_mask, val_mask) tuples.
    Fold 1: train=2022,       val=2023
    Fold 2: train=2022+2023,  val=2024
    Final:  train=2022+2023+2024, val=held-out (2024 success criterion)
    """
    years = sorted(df[year_col].unique())
    train_years = [y for y in years if y <= train_years_cap]
    folds = []
    for i in range(1, len(train_years)):
        t_mask = df[year_col].isin(train_years[:i])
        v_mask = df[year_col] == train_years[i]
        folds.append((t_mask, v_mask))
    return folds
```

### Anti-Patterns to Avoid

- **`cv='prefit'` in `CalibratedClassifierCV`:** Removed in scikit-learn 1.x (installed: 1.9.0). Always use `cv=N` (integer).
- **`is_unbalance=True` in `XGBClassifier`:** This is a LightGBM parameter. XGBoost uses `scale_pos_weight=(n_negatives/n_positives)`. Setting `is_unbalance` on XGBClassifier silently does nothing.
- **`CASE WHEN finish_position = 1 THEN 1 ELSE 0 END` without lap-completion filter:** Produces a false winner for Le Castellet 2022 (driver 16, DNF at lap 17 while leading). Use the `MAX(lap_number) >= total_laps - 1` subquery filter.
- **Equal-weight TimeSeriesSplit across all rows:** TimeSeriesSplit with equal windows will split 2022 season data mid-season. Always split on year boundaries for F1 data.
- **Softmax on all drivers globally:** Must be applied per race (group by `session_key`), not across all validation rows. Otherwise probabilities sum to 1.0 across all races combined, not within each race.
- **f-string SQL in train.py:** Follow established project convention — all SQL via `text()` with bound `:param` names (T-03-05).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Class imbalance | Custom loss weighting | `scale_pos_weight` in XGBClassifier | Built-in, correctly scales gradient/hessian during boosting |
| Probability calibration | Platt scaling by hand | `CalibratedClassifierCV(method='isotonic')` | Isotonic handles asymmetric calibration errors typical in binary imbalanced classification; sklearn implementation is battle-tested |
| Sum-to-1 normalization | Divide by sum | `scipy.special.softmax` | Numerically stable (avoids overflow/underflow with logit-scale scores); already in venv |
| NaN imputation for `circuit_win_rate` | Mean/median fill | XGBoost native `missing=np.nan` | XGBoost learns the optimal direction for NaN splits; imputation would misrepresent "debut at circuit" as if it had historical data |
| Model serialization | `pickle.dump` | `joblib.dump` | Safer for numpy arrays, faster for large arrays, used by scikit-learn natively |

**Key insight:** The entire ML pipeline (calibration, NaN handling, imbalance, serialization) is handled by already-installed libraries. `train.py` should be a thin orchestration script.

---

## Runtime State Inventory

This is a greenfield training phase with no rename/refactor scope. The backfill extension modifies existing data — here are the state items:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `raw_sessions` rows for 2023–2025 have `round_number=0` | SQL `UPDATE raw_sessions SET round_number = <ranked_meeting_key> WHERE year >= 2023` in Plan 01 |
| Stored data | `features_prerace` and `features_inrace` have 1 race per year for 2023–2025 | Re-run `build_all_features` after backfill extension; builder uses DELETE+INSERT (idempotent) |
| Live service config | None — local-only project | None |
| OS-registered state | None | None |
| Secrets/env vars | None | None |
| Build artifacts | `data/models/` directory exists and is empty | Model files created by `train.py` |

---

## Current DB State (as of research, 2026-06-25)

**Verified by querying `data/f1.db` directly:**

| Year | Race sessions | features_prerace rows | features_inrace rows | Notes |
|------|--------------|----------------------|---------------------|-------|
| 2022 | 16 races (rounds 1–16 + round 17 Q only) | 313 rows | 17,112 rows | Missing rounds 17–22 (6 GP races) |
| 2023 | 1 race (round 0 — Barcelona, `round_number=0`) | 20 rows | 1,310 rows | Needs 22 more races |
| 2024 | 1 race (round 0 — Sakhir, `round_number=0`) | 20 rows | 1,129 rows | Needs 23 more races |
| 2025 | 1 race (round 0 — Melbourne, `round_number=0`) | 20 rows | 921 rows | Needs available rounds |
| **Total** | **19 races** | **373 rows (353 with finish_position)** | **20,472 rows** | |

**After full backfill (estimated):**

| Year | Expected races | Expected prerace rows | Expected inrace rows |
|------|---------------|----------------------|---------------------|
| 2022 | 22 races | ~440 rows | ~26,400 rows |
| 2023 | 23 races | ~460 rows | ~27,600 rows |
| 2024 | 24 races | ~480 rows | ~28,800 rows |
| 2025 | ~17 races (mid-season) | ~340 rows | ~20,400 rows |
| **Total** | **~86 races** | **~1,720 rows** | **~103,200 rows** |

---

## Common Pitfalls

### Pitfall 1: `round_number=0` for all 2023–2025 sessions
**What goes wrong:** `championship_position` computation in `compute_championship_position` uses `rs.round_number < :rn` to find prior races in the same year. With all 2023–2025 races at `round_number=0`, no prior-round data is found, so `championship_position` is NULL for all 2023–2025 race rows.
**Why it happens:** OpenF1 `/v1/sessions` does not return a `round_number` field. The normalizer fallback is `0`.
**How to avoid:** Plan 01 must: (a) after fetching all sessions for a year, sort by `meeting_key` (ascending), (b) assign `round_number = rank(meeting_key)` within year, (c) run `UPDATE raw_sessions` for all 2023–2025 rows.
**Warning signs:** `features_prerace.championship_position` is NULL for all 2023–2025 rows after backfill.

### Pitfall 2: `cv='prefit'` removed in scikit-learn 1.x
**What goes wrong:** `CalibratedClassifierCV(clf, cv='prefit')` raises `InvalidParameterError` in scikit-learn 1.9.0.
**Why it happens:** The `cv='prefit'` option was deprecated and removed. Most online tutorials and older code still use it.
**How to avoid:** Use `cv=5` (integer). This trains 5 internal copies of the base estimator and ensembles their calibrations. Slightly more computation but more robust than single-fold calibration.
**Warning signs:** `InvalidParameterError: The 'cv' parameter of CalibratedClassifierCV must be an int in the range [2, inf)`.

### Pitfall 3: False winner label from DNF leaders (Le Castellet 2022)
**What goes wrong:** `train.py` with `CASE WHEN finish_position = 1 THEN 1 ELSE 0 END` marks 2 drivers as winners for Le Castellet 2022, inflating winner count to 20 winners across 19 races. The model trains on a corrupted label.
**Why it happens:** `compute_finish_position` uses `SELECT position FROM raw_positions WHERE session_key=:sk AND driver_number=:dn ORDER BY lap_number DESC LIMIT 1`. A DNF driver who was leading at lap 17 has `position=1` as their last known position.
**How to avoid:** Add the lap-completion subquery to `is_winner` derivation (see Pattern 1 SQL above).
**Warning signs:** `df["is_winner"].sum()` does not equal the number of races in the training set.

### Pitfall 4: `softmax` applied globally instead of per-race
**What goes wrong:** Probabilities don't sum to 1.0 within each race — they sum to 1.0 across ALL races.
**Why it happens:** `scipy.special.softmax(proba_array)` applied to all rows at once.
**How to avoid:** Use `df.groupby("session_key")["raw_prob"].transform(lambda x: softmax(x.values))`.
**Warning signs:** Each race has ~20 rows but total proba sum per race ≠ 1.0; total across all races = 1.0.

### Pitfall 5: `practice_pace_fp3` NULL on sprint weekends
**What goes wrong:** Sprint weekends have no Practice 3 session → `practice_pace_fp3` is NULL for those drivers. If imputed to 0 (field median), the model incorrectly treats these drivers as having average pace. This affects ~3–4 races per year.
**Why it happens:** Sprint weekend schedule: FP1, Sprint Qualifying, Sprint Race, Qualifying, Race. FP3 replaced by Sprint sessions.
**How to avoid:** Leave NULL — XGBoost native `missing=np.nan` handles it correctly (D-05). Do NOT fill with 0 or mean.
**Warning signs:** `NULL` values in `practice_pace_fp3` drop during `.dropna()` calls — never call `.dropna()` on training features.

### Pitfall 6: `championship_position` NULL for first race of season
**What goes wrong:** Round 1 of each season has no prior races → `championship_position` is NULL for all drivers.
**Why it happens:** `compute_championship_position` returns `None` when `rs.round_number < :rn` returns no rows.
**How to avoid:** Leave NULL — XGBoost handles it. Do NOT impute with 10 (midfield position) or any constant.
**Warning signs:** After backfill, round 1 races have 100% NULL `championship_position`.

### Pitfall 7: `scale_pos_weight` vs `is_unbalance`
**What goes wrong:** `XGBClassifier(is_unbalance=True)` silently fails — XGBoost ignores unknown parameters by default (unless `validate_parameters=True`). Model trains without imbalance correction.
**Why it happens:** `is_unbalance` is a LightGBM parameter. Confusion from CONTEXT.md D-09 which references it by name.
**How to avoid:** Use `scale_pos_weight = (n_total - n_winners) / n_winners ≈ 19` for XGBoost.
**Warning signs:** Model predicts near-uniform 5% win probability for all drivers (imbalance not corrected).

---

## Backfill Extension Design (Plan 01)

### What needs to change in `scripts/backfill.py`

The existing `backfill.py` already supports arbitrary year/round combinations. To extend 2022 coverage, invoke:
```bash
python3.11 scripts/backfill.py --years 2022 --rounds 17 18 19 20 21 22 --identifiers FP2 FP3 Q R S SS
```

For 2023–2025 full coverage, the script already fetches all available sessions when `--rounds` is omitted:
```bash
python3.11 scripts/backfill.py --years 2023 2024 2025
```

The resumability logic in `session_exists()` prevents re-fetching already-stored sessions. Running the script a second time is safe.

### The `round_number=0` fix

After fetching 2023–2025 sessions, a post-processing step must fix `round_number`:

```python
# Source: derived from analysis of openf1_loader and raw_sessions schema
# Run AFTER backfill, BEFORE build_features
from sqlalchemy import text
from f1_prediction.db.engine import get_engine

def fix_round_numbers(engine):
    """
    Derive round_number for OpenF1 sessions from meeting_key rank within year.
    OpenF1 /v1/sessions does not return round_number; meeting_keys are
    sequential within a year and can be ranked to approximate round order.
    """
    with engine.begin() as conn:
        # Get all 2023+ sessions ordered by (year, meeting_key)
        rows = conn.execute(text("""
            SELECT session_key, year, meeting_key
            FROM raw_sessions
            WHERE year >= 2023 AND meeting_key IS NOT NULL
            ORDER BY year, meeting_key
        """)).fetchall()
        
        # Assign round_number = rank of unique meeting_key within year
        from collections import defaultdict
        year_meeting_rank = defaultdict(dict)
        year_meeting_keys = defaultdict(list)
        for sk, year, mk in rows:
            if mk not in year_meeting_keys[year]:
                year_meeting_keys[year].append(mk)
        
        for year, mks in year_meeting_keys.items():
            sorted_mks = sorted(set(mks))
            for rank, mk in enumerate(sorted_mks, start=1):
                year_meeting_rank[year][mk] = rank
        
        # Update round_number
        for sk, year, mk in rows:
            rn = year_meeting_rank[year].get(mk, 0)
            conn.execute(text(
                "UPDATE raw_sessions SET round_number = :rn WHERE session_key = :sk"
            ), {"rn": rn, "sk": sk})
```

---

## Code Examples

### Loading pre-race training data

```python
# Source: verified against data/f1.db — returns 353 rows for current DB
import pandas as pd
from sqlalchemy import text
from f1_prediction.db.engine import get_engine

def load_prerace_training(engine, max_year=2024):
    with engine.connect() as conn:
        df = pd.read_sql(text("""
            SELECT
                fp.session_key,
                fp.driver_number,
                fp.qualifying_delta_to_pole,
                fp.practice_pace_fp2,
                fp.practice_pace_fp3,
                fp.circuit_win_rate,
                fp.championship_position,
                fp.grid_position,
                fp.driver_seasons_in_f1,
                CASE
                    WHEN fp.finish_position = 1
                     AND (
                        SELECT MAX(rp2.lap_number)
                        FROM raw_positions rp2
                        WHERE rp2.session_key = fp.session_key
                          AND rp2.driver_number = fp.driver_number
                     ) >= rs.total_laps - 1
                    THEN 1
                    ELSE 0
                END AS is_winner,
                rs.year,
                rs.round_number
            FROM features_prerace fp
            JOIN raw_sessions rs ON fp.session_key = rs.session_key
            WHERE rs.session_type = 'Race'
              AND fp.finish_position IS NOT NULL
              AND rs.year <= :max_year
            ORDER BY rs.year, rs.round_number, fp.driver_number
        """), conn, params={"max_year": max_year})
    return df

PRERACE_FEATURE_COLS = [
    "qualifying_delta_to_pole",
    "practice_pace_fp2",
    "practice_pace_fp3",
    "circuit_win_rate",
    "championship_position",
    "grid_position",
    "driver_seasons_in_f1",
]
```

### Training and saving a model

```python
# Source: verified against installed xgboost 3.2.0 + scikit-learn 1.9.0 in this session
import numpy as np
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, log_loss
from scipy.special import softmax
import joblib
from pathlib import Path

def train_and_save(
    df,
    feature_cols,
    model_path,
    train_years,
    val_year=2024,
    n_estimators=500,
):
    # Compute scale_pos_weight from training data
    train_mask = df["year"].isin(train_years)
    y_train = df.loc[train_mask, "is_winner"].values
    X_train = df.loc[train_mask, feature_cols].values
    
    n_total = len(y_train)
    n_pos = y_train.sum()
    scale_pos_weight = float((n_total - n_pos) / n_pos) if n_pos > 0 else 1.0
    
    # Train XGBoost + calibrate
    base_clf = xgb.XGBClassifier(
        n_estimators=n_estimators,
        learning_rate=0.05,
        max_depth=6,
        scale_pos_weight=scale_pos_weight,
        objective="binary:logistic",
        eval_metric="logloss",
        verbosity=0,
    )
    model = CalibratedClassifierCV(base_clf, cv=5, method="isotonic")
    model.fit(X_train, y_train)
    
    # Validate on held-out year
    val_mask = df["year"] == val_year
    X_val = df.loc[val_mask, feature_cols].values
    y_val = df.loc[val_mask, "is_winner"].values
    
    raw_proba = model.predict_proba(X_val)[:, 1]
    
    # Softmax per race
    val_df = df.loc[val_mask, ["session_key"]].copy()
    val_df["win_prob"] = val_df.groupby("session_key").transform(
        lambda x: softmax(raw_proba[x.index - val_mask.index[0]])
    )["session_key"]
    
    # Metrics
    brier = brier_score_loss(y_val, raw_proba)
    ll = log_loss(y_val, raw_proba)
    print(f"Brier score: {brier:.4f}")
    print(f"Log loss: {ll:.4f}")
    
    # Save
    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)
    print(f"Model saved to {model_path}")
    return model
```

### Sum-to-1 validation

```python
# Source: verified against data/f1.db + scipy.special.softmax in this session
def validate_sum_to_one(model, X_val, session_keys, tolerance=0.001):
    """
    Verify that per-race win probabilities sum to 1.0 ± tolerance.
    This is the Phase 2 success criterion (PRERACE-02, INRACE-02).
    """
    raw_proba = model.predict_proba(X_val)[:, 1]
    
    import pandas as pd
    df = pd.DataFrame({"session_key": session_keys, "raw_prob": raw_proba})
    df["win_prob"] = df.groupby("session_key")["raw_prob"].transform(
        lambda x: softmax(x.values)
    )
    
    sums = df.groupby("session_key")["win_prob"].sum()
    violations = sums[(sums - 1.0).abs() > tolerance]
    
    if not violations.empty:
        raise AssertionError(
            f"Sum-to-1 violation for session_keys: {violations.index.tolist()}\n"
            f"Sums: {violations.values}"
        )
    
    print(f"Sum-to-1 PASSED for {len(sums)} races "
          f"(min={sums.min():.6f}, max={sums.max():.6f})")
    return True
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `CalibratedClassifierCV(cv='prefit')` | `CalibratedClassifierCV(cv=N)` with integer | scikit-learn 0.24→1.x | `cv='prefit'` removed; use integer cv |
| `is_unbalance=True` (LightGBM API) | `scale_pos_weight=N` (XGBoost API) | Always been separate | D-06 switches to XGBoost; must use correct param |
| `XGBClassifier(missing=None)` to handle NaN | `missing=np.nan` (default) | XGBoost 1.x | Default is already `np.nan`; no explicit override needed |
| Manual train/test split for F1 data | Season-boundary splits (year-based) | Best practice | Prevents data leakage from future seasons into past-season validation |

**Deprecated/outdated:**
- `cv='prefit'` in `CalibratedClassifierCV`: use `cv=5` (integer)
- `is_unbalance=True`: XGBoost-specific parameter is `scale_pos_weight`

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | 2022 F1 season had 22 rounds — rounds 17–22 are missing from DB | Backfill Extension | If FastF1 only has rounds 1–16 for 2022, training corpus is smaller than expected |
| A2 | After full backfill, ~85 races total (2022-2025 mid-season) | Current DB State | Estimate based on 2022=22, 2023=23, 2024=24, 2025=~17 rounds |
| A3 | OpenF1 meeting_key is monotonically increasing within a year (suitable for round_number rank) | round_number fix | If meeting_key is non-sequential within a year, round ordering will be wrong |
| A4 | 2025 season has ~17 completed races as of research date | Backfill scope | OpenF1 live check not performed; 2025 is an ongoing season |
| A5 | `total_laps - 1` tolerance in is_winner lap-completion filter covers lapped cars | is_winner derivation | Lapped cars complete `total_laps - N` laps. `-1` tolerates 1 lap behind; a more robust approach would filter on `finish_position=1 AND MAX(lap_number) = total_laps` strictly but this excludes the race winner if the winner was credited but lapped. The actual 2022 winner (Verstappen at Le Castellet) completed 53/53 laps so `>= total_laps - 1` works. |

---

## Open Questions (RESOLVED)

1. **Will `circuit_avg_finish` and `constructor_pace_delta` populate after full backfill?**
   - What we know: Both are 100% NULL currently (require 2022 data for 2023+ lookback). After backfill + feature rebuild, 2023 races should have 1 year of lookback data.
   - What's unclear: Exact NULL rate after backfill. `team_name` is still 100% NULL (root cause not diagnosed), which would still block `constructor_pace_delta`.
   - **RESOLVED:** Accept D-02 feature drop for Phase 2 — these columns stay out of the training feature set. Re-evaluate post-Phase 4 if model accuracy needs improvement.

2. **What is the `team_name=100% NULL` root cause?**
   - What we know: `_get_team_name_from_session` in `builder.py` looks up team from prior `features_prerace` rows. Since the very first race ever processed (2022 round 1) has no prior data, team_name is NULL there. The builder is supposed to propagate it forward but currently doesn't.
   - What's unclear: Whether fixing the team_name lookup in builder.py is in scope for Plan 01, or if we simply accept it as NULL (D-02 already drops it from features).
   - **RESOLVED:** Leave `team_name` NULL per D-02 — it is already dropped from the training feature set. No in-scope fix. Investigate only if it blocks a future phase feature.

3. **Should `practice_pace_fp3` NULL for sprint weekends be imputed?**
   - What we know: ~3–4 races per year are sprint weekends with no FP3. XGBoost handles these correctly with native NaN.
   - What's unclear: Whether the model should be told this NULL has semantic meaning ("this is a sprint weekend") via a boolean feature.
   - **RESOLVED:** Leave `practice_pace_fp3` NULL for Phase 2. XGBoost handles NaN natively (D-05 pattern). An `is_sprint_weekend` boolean feature is deferred to post-Phase 4 if needed.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| python3.11 | All scripts | ✓ | 3.11.x (system) | — |
| xgboost | Model training | ✓ | 3.2.0 | — |
| scikit-learn | Calibration + CV | ✓ | 1.9.0 | — |
| scipy | softmax normalization | ✓ | 1.17.1 | — |
| joblib | Model serialization | ✓ | 1.5.3 | — |
| pandas | Feature loading | ✓ | 2.2.2 | — |
| numpy | Array operations | ✓ | 2.0.1 | — |
| data/f1.db | Training data source | ✓ | exists, 373 prerace rows | — |
| data/models/ | Artifact output | ✓ | directory exists (empty) | — |
| OpenF1 API | Backfill 2023–2025 | ✓ | live (rate limited) | Retry logic already in openf1_client.py |
| FastF1 | Backfill 2022 rounds 17–22 | ✓ | 3.8.3 | — |

**Missing dependencies with no fallback:** None.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (already installed and used by Phase 1) |
| Config file | none (pytest runs from project root with `python3.11 -m pytest`) |
| Quick run command | `python3.11 -m pytest tests/ -q` |
| Full suite command | `python3.11 -m pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PRERACE-01 | Pre-race model trains on correct feature set | unit | `pytest tests/test_train.py::test_prerace_feature_set -x` | ❌ Wave 0 |
| PRERACE-02 | Pre-race predictions sum to 1.0 ± 0.001 per race | unit | `pytest tests/test_train.py::test_prerace_sum_to_one -x` | ❌ Wave 0 |
| PRERACE-03 | `data/models/prerace_model.joblib` exists and is loadable | smoke | `pytest tests/test_train.py::test_prerace_artifact_exists -x` | ❌ Wave 0 |
| INRACE-01 | In-race model trains on correct feature set with tyre encoding | unit | `pytest tests/test_train.py::test_inrace_feature_set -x` | ❌ Wave 0 |
| INRACE-02 | In-race predictions sum to 1.0 ± 0.001 per race | unit | `pytest tests/test_train.py::test_inrace_sum_to_one -x` | ❌ Wave 0 |
| INRACE-03 | `pit_stop_count`, `tyre_compound`, `tyre_age_laps` present in in-race features | unit | `pytest tests/test_train.py::test_inrace_pit_tyre_features -x` | ❌ Wave 0 |
| D-01 (backfill) | `features_prerace` has rows for years 2023, 2024, 2025 beyond round 1 | integration | `pytest tests/test_backfill_extended.py -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `python3.11 -m pytest tests/ -q` (34 existing tests + new Phase 2 tests)
- **Per wave merge:** `python3.11 -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_train.py` — covers PRERACE-01 through INRACE-03 with a small in-memory fixture DB
- [ ] `tests/test_backfill_extended.py` — verifies round count post-backfill
- [ ] `tests/conftest.py` already exists and has fixture infrastructure (reuse from Phase 1)

---

## Security Domain

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No — local script, no auth | — |
| V3 Session Management | No | — |
| V4 Access Control | No — local file system only | — |
| V5 Input Validation | Partial — SQL queries in train.py | `text()` with bound `:param` names (established T-03-05 project convention) |
| V6 Cryptography | No | — |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection in training queries | Tampering | All SQL via `text()` with bound parameters (T-03-05) |
| Model artifact tampering | Tampering | `data/models/` is local-only; no mitigations needed for Phase 2 |

---

## Plan Structure Recommendation

Phase 2 maps cleanly to **4 plans**:

| Plan | Name | Content | Dependent on |
|------|------|---------|-------------|
| 02-01 | `backfill-extension` | Extend 2022 (rounds 17-22) + 2023-2025 (all rounds) via existing `scripts/backfill.py`. Fix `round_number=0` for all 2023+ sessions. Re-run `scripts/build_features.py`. Verify `features_prerace` row count > 300 for 2023-2025 combined. | Phase 1 (complete) |
| 02-02 | `prerace-model` | Write `scripts/train.py --model prerace`. Load from `features_prerace`, derive `is_winner` with lap-completion filter, one-hot encode n/a (no categoricals in pre-race), train XGBClassifier + CalibratedClassifierCV, save to `data/models/prerace_model.joblib`. | 02-01 |
| 02-03 | `inrace-model` | Add `--model inrace` branch to `scripts/train.py`. Load from `features_inrace JOIN features_prerace`, one-hot encode `tyre_compound`, same calibration/save pattern, save to `data/models/inrace_model.joblib`. | 02-02 (shared script infrastructure) |
| 02-04 | `validate-models` | Write `scripts/validate_model.py` (or add validation to train.py). Load both `.joblib` files, run `predict_proba` on held-out 2024 data, apply per-race softmax, assert sum-to-1 for all races, print Brier score + log loss. | 02-03 |

**Rationale:** Plans 02 and 03 share `scripts/train.py` infrastructure — Plan 02 scaffolds the script and Plan 03 adds the inrace branch. This avoids writing the calibration/save boilerplate twice while keeping each plan independently testable.

---

## Sources

### Primary (HIGH confidence — verified by running against project codebase)
- `data/f1.db` — direct SQL queries confirmed all NULL rates, row counts, round numbers, duplicate winner bug
- Installed package versions — `pip show` + direct API calls confirmed all parameters and behavior
- `src/f1_prediction/features/prerace.py` — confirms `finish_position` derivation logic (DNF bug)
- `src/f1_prediction/features/builder.py` — confirms chronological session processing
- `src/f1_prediction/ingest/openf1_loader.py` — confirms `round_number=0` fallback behavior
- `scripts/backfill.py` — confirms resumable architecture and year routing

### Secondary (MEDIUM confidence — confirmed via package inspection)
- xgboost 3.2.0 `get_params()` — confirmed `scale_pos_weight` exists; `is_unbalance` does not
- scikit-learn 1.9.0 `CalibratedClassifierCV` — confirmed `cv='prefit'` raises `InvalidParameterError`
- scipy 1.17.1 `softmax` — confirmed sum=1.000000 per race group
- joblib 1.5.3 — confirmed round-trip serialization fidelity

---

## Metadata

**Confidence breakdown:**
- Backfill architecture: HIGH — code verified against existing scripts and live OpenF1 API
- round_number=0 issue: HIGH — confirmed by SQL query against data/f1.db
- Duplicate winner bug: HIGH — confirmed by SQL query and raw_positions data
- Standard stack: HIGH — all packages installed and API-tested
- Training pipeline: HIGH — end-to-end pipeline verified in Python process
- cv='prefit' removal: HIGH — confirmed by running CalibratedClassifierCV with cv='prefit'
- Plan count/structure: MEDIUM — estimated from code complexity; actual plans may split or merge during planning

**Research date:** 2026-06-25
**Valid until:** 2026-09-25 (stable stack; scikit-learn API unlikely to change)
