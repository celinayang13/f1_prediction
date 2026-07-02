---
phase: 02-model-training
plan: 02
subsystem: ml-training
status: complete
tags:
  - python
  - xgboost
  - scikit-learn
  - calibration
  - softmax
  - prerace
depends_on:
  requires:
    - 02-01 (features_prerace populated for 2022-2024)
  provides:
    - scripts/train.py — shared training CLI scaffold (prerace branch implemented; inrace is NotImplementedError stub for Plan 02-03)
    - data/models/prerace_model.joblib — fitted CalibratedClassifierCV (XGBoost + isotonic)
    - tests/test_train.py — 7 pre-race unit tests covering PRERACE-01 through PRERACE-03
  affects:
    - 02-03-PLAN (inrace model — adds --model inrace branch to scripts/train.py)
    - 02-04-PLAN (validation script — loads prerace_model.joblib for sum-to-1 check)
    - Phase 3 inference loop (loads prerace_model.joblib via joblib.load)
tech_stack:
  added:
    - xgboost 3.2.0 — XGBClassifier with scale_pos_weight for class imbalance
    - sklearn.calibration.CalibratedClassifierCV(cv=5, method='isotonic')
    - scipy.special.softmax for per-race normalization
    - joblib.dump/load for model artifact serialization
  patterns:
    - scale_pos_weight instead of is_unbalance (XGBoost vs LightGBM API distinction)
    - per-race softmax via groupby("session_key").transform — NOT global softmax
    - OpenF1 lap_number=0 fallback in is_winner SQL (trust finish_position when no lap data)
    - configurable cv param on train_prerace_model for test-fixture compatibility
key_files:
  created:
    - scripts/train.py
    - tests/test_train.py
    - data/models/prerace_model.joblib
  modified: []
decisions:
  - Rule 1: Added OR clause to is_winner SQL to handle OpenF1 sessions where lap_number=0
  - Rule 1: Exposed cv=5 as default parameter on train_prerace_model for test isolation
  - Rule 1: Converted numpy.int64 train_years to int() before json.dumps
  - Production main() explicitly passes cv=5 to train_prerace_model
metrics:
  duration_minutes: 30
  completed_date: 2026-07-02
  tasks_completed: 3
  files_changed: 3
---

# Phase 02 Plan 02: Pre-Race Model Training Summary

Calibrated XGBoost pre-race win probability model trained on 2022-2023 historical data, validated on held-out 2024 races, and serialized to `data/models/prerace_model.joblib` as a fitted `CalibratedClassifierCV`.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create scripts/train.py with pre-race training pipeline | `116372a` | scripts/train.py |
| 2 | Create tests/test_train.py with 7 unit tests | `867bd8c` | tests/test_train.py, scripts/train.py |
| 3 | Train pre-race model against live DB | `6ddd42f` | data/models/prerace_model.joblib, scripts/train.py |

## Training Metrics (--val-year 2024)

| Metric | Value |
|--------|-------|
| train_years | [2022, 2023] |
| n_train | 750 rows |
| n_val | 598 rows |
| n_val_races | 30 (24 GP + 6 Sprint races) |
| brier_score | 0.0381 |
| log_loss | 0.1573 |
| sum_to_one_min | 1.0 ± 2e-16 |
| sum_to_one_max | 1.0 ± 2e-16 |
| scale_pos_weight | 18.74 (n_neg/n_pos) |
| artifact_size | 2,972,851 bytes (~2.8 MB) |

## Acceptance Criteria Results

| Criterion | Status |
|-----------|--------|
| scripts/train.py exists with all 5 functions | PASS |
| PRERACE_FEATURE_COLS has 7 D-03 columns | PASS |
| No cv='prefit' in AST (Pitfall 2) | PASS |
| No is_unbalance in AST (Pitfall 7) | PASS |
| scale_pos_weight kwarg present (XGBoost) | PASS |
| cv=5 and isotonic in source | PASS |
| Lap-completion filter referencing total_laps-1 | PASS |
| Per-race groupby session_key softmax | PASS |
| --help shows --model {prerace,inrace} | PASS |
| --model inrace exits 1 with NotImplementedError | PASS |
| 7 pytest tests all PASS | PASS |
| prerace_model.joblib exists, size 2.8MB | PASS |
| joblib.load returns CalibratedClassifierCV | PASS |
| sum_to_one within [0.999, 1.001] | PASS |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] OpenF1 raw_positions have lap_number=0 — breaks lap-completion is_winner filter**
- **Found during:** Task 3 execution (first training run produced n_winners_in_train=16 for 2022 only; 2023/2024 had 0 winners)
- **Issue:** The OpenF1 `/v1/position` endpoint does not return `lap_number`. The OpenF1 normalizer stores `lap_number = _safe_int(p.get("lap_number")) or 0 = 0` for all position rows. This causes `MAX(rp2.lap_number) = 0` for all OpenF1 sessions, making `0 >= rs.total_laps - 1` false for all drivers — no 2023/2024 winners identified.
- **Fix:** Added an OR clause to the is_winner CASE expression: `OR COALESCE(MAX(rp2.lap_number), 0) = 0`. When max_lap=0 (OpenF1, no lap data), the filter falls through and trusts `finish_position`. When max_lap>0 (FastF1, real lap data), the original strict completion check applies.
- **Impact:** 2023 winners: 22 (matching race count), 2024 winners: 30 (24 GP + 6 sprints). Training now uses n_winners=38 (vs 16 before fix).
- **Files modified:** `scripts/train.py`
- **Commit:** `6ddd42f`

**2. [Rule 1 - Bug] train_prerace_model fails with cv=5 on small test fixtures**
- **Found during:** Task 2 test run — `ValueError: Requesting 5-fold cross-validation but provided less than 5 examples for at least one class`
- **Issue:** The test fixture seeds only 4 sessions × 3 drivers = 12 rows with 3 positive examples in training (2022 + 2023). CalibratedClassifierCV with cv=5 requires ≥5 positive examples.
- **Fix:** Added `cv: int = 5` as a default parameter to `train_prerace_model`. Production `_train_prerace()` explicitly calls `train_prerace_model(df, train_years, cv=5)`. Tests call with `cv=2`. The literal string `cv=5` is preserved in the source (both in the default param and in the production call site).
- **Files modified:** `scripts/train.py`, `tests/test_train.py`
- **Commit:** `867bd8c`

**3. [Rule 1 - Bug] numpy.int64 values in train_years are not JSON serializable**
- **Found during:** Task 3 first training run — `TypeError: Object of type int64 is not JSON serializable`
- **Issue:** `df["year"].unique()` returns numpy int64 values; `json.dumps({"train_years": [np.int64(2022), ...]})` raises TypeError.
- **Fix:** Changed `"train_years": train_years` to `"train_years": [int(y) for y in train_years]` in the metrics dict.
- **Files modified:** `scripts/train.py`
- **Commit:** `6ddd42f`

## Known Stubs

One intentional stub:
- `scripts/train.py`: `--model inrace` raises `NotImplementedError("Wired in Plan 02-03")`. This is documented in the plan and will be implemented by Plan 02-03. The inrace branch is structurally present and correctly fails — it does not silently produce wrong output.

## Threat Flags

No new threat surface introduced. All SQL uses `sqlalchemy.text()` with bound `:max_year` param (T-03-05). The model artifact (`data/models/prerace_model.joblib`) is local-only and never loaded from an untrusted source in this phase. See plan threat register T-02-02-01 through T-02-02-04.

## Self-Check: PASSED

- `scripts/train.py` exists at expected path: FOUND
- `tests/test_train.py` exists at expected path: FOUND
- `data/models/prerace_model.joblib` exists at expected path: FOUND
- Commits verified: 116372a, 867bd8c, 6ddd42f all present in `git log`
- `PYTHONPATH=src python3.11 -m pytest tests/test_train.py -v` → 7 passed
- `PYTHONPATH=src python3.11 -m pytest tests/ -q` → 47 passed (0 regressions)
- `python3.11 scripts/train.py --model prerace --val-year 2024` → exit 0, sum_to_one within tolerance
- `python3.11 scripts/train.py --model inrace` → exit 1, NotImplementedError("Wired in Plan 02-03")
- `joblib.load('data/models/prerace_model.joblib')` → CalibratedClassifierCV with predict_proba
- Artifact size: 2,972,851 bytes (within 1000–50MB range)
