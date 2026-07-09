---
phase: 02-model-training
plan: 03
subsystem: ml-training
status: complete
tags:
  - python
  - xgboost
  - scikit-learn
  - calibration
  - softmax
  - inrace
  - tyre-encoding
depends_on:
  requires:
    - 02-01 (features_inrace populated for 2022-2024)
    - 02-02 (scripts/train.py scaffold + prerace branch)
  provides:
    - scripts/train.py — extended with in-race training pipeline (--model inrace branch fully implemented)
    - data/models/inrace_model.joblib — fitted CalibratedClassifierCV (XGBoost + isotonic) for in-race prediction
    - tests/test_train.py — expanded to 15 tests (7 prerace + 8 inrace)
  affects:
    - 02-04-PLAN (validation script — loads inrace_model.joblib)
    - Phase 3 inference loop (loads inrace_model.joblib via joblib.load per lap)
tech_stack:
  added:
    - pd.get_dummies with reindex to canonical 5 columns — stable tyre one-hot encoding across DB snapshots
  patterns:
    - per-(session_key, lap_number) softmax — each lap is a separate inference event (INRACE-02)
    - encode_tyre_compound reindexes to TYRE_COMPOUND_VALUES so absent compounds still produce zero columns
    - scale_pos_weight=17.49 for in-race class imbalance (1 winning driver per ~18.5 rows)
    - lap-varying position/gap_to_leader in test fixture forces model to produce lap-varying predictions
key_files:
  created:
    - data/models/inrace_model.joblib
  modified:
    - scripts/train.py
    - tests/test_train.py
decisions:
  - Rule 1 fix: lap-varying position and gap_to_leader in inrace_train_engine fixture (constant features caused XGBoost to ignore lap snapshots with cv=2 small fixture)
  - in-race SQL reuses the same OR clause from Plan 02-02 (OpenF1 lap_number=0 fallback) — is_winner correctly identified for 2023/2024 sessions
  - scale_pos_weight=17.49 (in-race winner rate: 1988 winners in 36756 train rows — higher than expected because some sessions have lap-aggregated rows not full-lap coverage)
metrics:
  duration_minutes: 20
  completed_date: 2026-07-02
  tasks_completed: 3
  files_changed: 3
---

# Phase 02 Plan 03: In-Race Model Training Summary

Calibrated XGBoost in-race win probability model trained on 36756 lap-level rows (2022-2023), validated on 28952 held-out 2024 rows across 1566 (session_key, lap_number) groups, and serialized to `data/models/inrace_model.joblib` as a fitted `CalibratedClassifierCV`. Per-lap softmax sums to 1.0 ± 1e-6 across all validation lap groups.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Extend scripts/train.py with in-race loader, encoder, trainer, and main() branch | `5f4a085` | scripts/train.py |
| 2 | Extend tests/test_train.py with 8 in-race tests | `101b63e` | tests/test_train.py |
| 3 | Train in-race model against live DB | `f9d1954` | data/models/inrace_model.joblib |

## Training Metrics (--val-year 2024)

| Metric | Value |
|--------|-------|
| train_years | [2022, 2023] |
| n_train | 36,756 rows |
| n_val | 28,952 rows |
| n_winners_in_train | 1,988 |
| n_val_lap_groups | 1,566 (unique session_key + lap_number pairs) |
| brier_score | 0.0507 |
| log_loss | 0.2125 |
| scale_pos_weight | 17.49 (n_neg / n_pos) |
| sum_to_one_min | 0.9999999999999997 |
| sum_to_one_max | 1.0000000000000004 |
| artifact_size | 7,021,603 bytes (~6.7 MB) |

## Acceptance Criteria Results

| Criterion | Status |
|-----------|--------|
| scripts/train.py defines all 7 inrace functions/constants | PASS |
| INRACE_NUMERIC_FEATURE_COLS has 9 entries | PASS |
| TYRE_COMPOUND_VALUES has 5 entries (HARD, INTERMEDIATE, MEDIUM, SOFT, WET) | PASS |
| encode_tyre_compound reindexes to canonical 5 columns | PASS |
| in-race SQL contains JOIN features_prerace on (session_key, driver_number) | PASS |
| in-race SQL contains lap-completion filter (total_laps, MAX(rp2.lap_number)) | PASS |
| groupby(["session_key", "lap_number"]) in predict_inrace_with_softmax | PASS |
| No NotImplementedError in inrace branch of main() | PASS |
| No cv='prefit' in AST | PASS |
| No is_unbalance in AST | PASS |
| --help shows --model {prerace,inrace}, exits 0 | PASS |
| 15 pytest tests all PASS (7 prerace + 8 inrace) | PASS |
| inrace_model.joblib exists, size 7.0 MB | PASS |
| prerace_model.joblib from Plan 02-02 still exists (regression) | PASS |
| joblib.load returns CalibratedClassifierCV with predict_proba | PASS |
| sum_to_one within [0.999, 1.001] | PASS |
| --model prerace --val-year 2024 still exits 0 (regression) | PASS |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] inrace_train_engine fixture: constant features per driver caused XGBoost to predict identical probabilities across all laps**
- **Found during:** Task 2 test run — `test_inrace_lap_by_lap_variation` failed with all laps producing 0.576117/0.211942/0.211942 for drivers 1/11/44 at every lap.
- **Issue:** The fixture seeded constant `gap_to_leader` and `position` for each driver across all 4 lap snapshots. With cv=2 and only 34 training rows, XGBoost learned a near-stump based on the dominant driver-constant features (position, gap) and ignored lap-varying features (lap_number, tyre_compound, laps_remaining). The model correctly learned from the data it was given — but the data was uninformative about lap-to-lap changes.
- **Fix:** Redesigned the fixture so `position` and `gap_to_leader` vary dramatically across laps: driver 11 leads at laps 1/10 (position=1, gap=0), driver 1 takes over at laps 20/30 (position=1, gap=0). This creates clear lap-varying signal that forces the model to use position/gap features, which are available at each lap snapshot.
- **Files modified:** `tests/test_train.py`
- **Commit:** `101b63e`

## Known Stubs

None. All in-race functions are fully implemented. The `--model inrace` branch in main() no longer raises NotImplementedError (Plan 02-02 stub fully replaced). Both model artifacts exist and are Phase-3-ready.

## Threat Flags

No new threat surface introduced. All SQL in `load_inrace_training` uses `sqlalchemy.text()` with bound `:max_year` param. Both model artifacts are local-only (never loaded from an untrusted source in Phase 2). See plan threat register T-02-03-01 through T-02-03-04.

## Self-Check: PASSED

- `scripts/train.py` verified: all 8 required functions present, INRACE_NUMERIC_FEATURE_COLS and TYRE_COMPOUND_VALUES defined, no NotImplementedError raise, no cv='prefit', no is_unbalance
- `tests/test_train.py` verified: 15 tests present, all 8 inrace test names confirmed
- `data/models/inrace_model.joblib` exists at expected path: FOUND (7,021,603 bytes)
- `data/models/prerace_model.joblib` still exists (regression): FOUND (2,972,851 bytes)
- Commits verified: 5f4a085, 101b63e, f9d1954 all present in `git log`
- `PYTHONPATH=src python3.11 -m pytest tests/test_train.py -v` → 15 passed
- `PYTHONPATH=src python3.11 -m pytest tests/ -v` → 55 passed (0 regressions)
- `python3.11 scripts/train.py --model inrace --val-year 2024` → exit 0, sum_to_one within tolerance
- `python3.11 scripts/train.py --model prerace --val-year 2024` → exit 0, regression PASS
- `joblib.load('data/models/inrace_model.joblib')` → CalibratedClassifierCV with predict_proba
