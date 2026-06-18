---
phase: 02-model-training
plan: 02
subsystem: ml
tags: [xgboost, ml-training, calibration, inrace, tdd]
dependency_graph:
  requires:
    - 02-01: ml/ package (loader, pipeline, evaluate) + prerace model established
    - 01-03: features_inrace and features_prerace tables populated by Phase 1 backfill
  provides:
    - src/f1_prediction/ml/loader.py: INRACE_FEATURE_COLS, INRACE_CAT_COLS, INRACE_TARGET, load_inrace_df()
    - src/f1_prediction/ml/pipeline.py: INRACE_MODEL_PATH, train_inrace_model()
    - scripts/train.py: now calls both train_prerace_model and train_inrace_model
    - data/models/inrace_model.joblib: runtime artifact (gitignored, path hardcoded)
  affects:
    - 03-xx: Phase 3 loads data/models/inrace_model.joblib for per-lap inference
tech_stack:
  added: []
  patterns:
    - INNER JOIN features_inrace x features_prerace for finish_position label (RESEARCH Open Q1)
    - Separate OrdinalEncoder for INRACE_CAT_COLS (not shared with prerace encoder)
    - Per-(session_key, lap_number) softmax grouping for INRACE-02 per-lap distribution
    - Same FrozenEstimator + CalibratedClassifierCV sigmoid pattern as Plan 01 (D-09)
    - D-16 stdout format: inrace log_loss + top3_hit_rate_final_lap
key_files:
  created:
    - tests/test_train_inrace.py
  modified:
    - src/f1_prediction/ml/loader.py (appended INRACE_FEATURE_COLS, INRACE_CAT_COLS, INRACE_TARGET, load_inrace_df)
    - src/f1_prediction/ml/pipeline.py (appended INRACE_MODEL_PATH, train_inrace_model)
    - scripts/train.py (replaced TODO(02-02) with train_inrace_model(engine) call)
decisions:
  - "RESEARCH Open Question Q1 resolved: INNER JOIN features_prerace for finish_position — drivers without label excluded cleanly"
  - "Per-(session_key, lap_number) softmax grouping for INRACE-02 — each lap is an independent inference moment"
  - "Separate OrdinalEncoder instance for tyre_compound (not shared with prerace team_name encoder)"
  - "D-06 unknown_value=np.nan inherited from Plan 01 build_encoder() — aligns with XGBoost NaN sentinel (Pitfall 7)"
metrics:
  duration_minutes: 5
  completed_date: "2026-06-18"
  tasks_completed: 2
  tasks_total: 2
  files_created: 1
  files_modified: 3
---

# Phase 02 Plan 02: In-Race Model Training Summary

**One-liner:** XGBoost in-race classifier trained on per-lap state (position, gap, tyre, pit history) with per-(session, lap) softmax normalization, INNER JOIN label derivation from features_prerace, and serialized as a 6-key joblib artifact dict matching the Phase 3 inference contract.

## What Was Built

Two TDD tasks delivered the full in-race model vertical slice on top of Plan 01:

**Task 1 (RED):** Created `tests/test_train_inrace.py` with five failing tests covering the in-race model contract. All failed with ImportError at collection time because `train_inrace_model`, `INRACE_MODEL_PATH`, `INRACE_FEATURE_COLS`, `INRACE_CAT_COLS`, and `load_inrace_df` did not yet exist. Tests seed both `features_inrace` and `features_prerace` for the INNER JOIN label path, include per-(session, lap) softmax assertion, pit-stop feature presence check, empty-table guard, and label-join exclusion verification.

**Task 2 (GREEN):** Extended `src/f1_prediction/ml/loader.py` with in-race constants (`INRACE_FEATURE_COLS`, `INRACE_CAT_COLS`, `INRACE_TARGET`) and `load_inrace_df()` using a three-table INNER JOIN. Extended `src/f1_prediction/ml/pipeline.py` with `INRACE_MODEL_PATH` and `train_inrace_model()` reusing all Plan 01 building blocks (`build_encoder`, `train_xgb_classifier`, `calibrate_model`, `save_artifact`). Replaced the `TODO(02-02)` marker in `scripts/train.py` with the `train_inrace_model(engine)` call. All 8 tests (5 inrace + 3 prerace regression) pass GREEN.

## Key Files

| File | Purpose |
|------|---------|
| `src/f1_prediction/ml/loader.py` | Added `INRACE_FEATURE_COLS` (10 cols), `INRACE_CAT_COLS`, `INRACE_TARGET`, `load_inrace_df()` with INNER JOIN to features_prerace |
| `src/f1_prediction/ml/pipeline.py` | Added `INRACE_MODEL_PATH` constant and `train_inrace_model()` orchestration with per-lap softmax |
| `scripts/train.py` | Replaced `TODO(02-02)` with `train_inrace_model(engine)` — both models trained in sequence |
| `tests/test_train_inrace.py` | 5 end-to-end tests: artifact creation, per-lap softmax, pit-stop features, empty guard, label-join exclusion |

## Label-Join Approach (RESEARCH Open Question Q1 Resolution)

`FeaturesInrace` does not store `finish_position`. The INNER JOIN to `features_prerace` on `(session_key, driver_number)` with `WHERE fp.finish_position IS NOT NULL` resolves Q1: drivers without a known finish_position are cleanly excluded from training. This handles retirements and avoids silent zero-label bias. Verified by `test_train_inrace_label_join_to_prerace`.

## Per-(Session, Lap) Softmax Decision

INRACE-02 requires a valid probability distribution updated after each completed lap. The pre-race model uses per-session_key grouping (one group per race). The in-race model uses a composite `session_lap_key = session_key + "_" + lap_number` grouping so each lap independently sums to 1.0 ± 1e-3. This is stricter than pre-race and correct for live lap-by-lap re-ranking.

## Stdout Metrics Format (D-16)

`scripts/train.py` now prints two metric lines:
```
prerace  log_loss=X.XXXX  top3_hit_rate=X.XXX  (N/M races)
inrace  log_loss=X.XXXX  top3_hit_rate_final_lap=X.XXX  (N/M races)
```
The `top3_hit_rate_final_lap` metric filters to the final lap per session before computing hit rate, measuring prediction quality at the moment most relevant to race outcome.

## Test Outcomes

```
tests/test_train_inrace.py::test_train_inrace_end_to_end                    PASSED
tests/test_train_inrace.py::test_train_inrace_softmax_sums_to_one_per_lap   PASSED
tests/test_train_inrace.py::test_train_inrace_includes_pit_stop_features    PASSED
tests/test_train_inrace.py::test_train_inrace_empty_features_raises         PASSED
tests/test_train_inrace.py::test_train_inrace_label_join_to_prerace         PASSED
tests/test_train_prerace.py::test_train_prerace_end_to_end                  PASSED (regression)
tests/test_train_prerace.py::test_train_prerace_softmax_sums_to_one         PASSED (regression)
tests/test_train_prerace.py::test_train_prerace_empty_features_raises       PASSED (regression)
```

## Deviations from Plan

None — plan executed exactly as written. All RESEARCH pitfalls avoided:

| Pitfall | Status |
|---------|--------|
| Pitfall 1: cv=prefit removed in sklearn 1.8 | Avoided — calibrate_model() uses FrozenEstimator throughout; reused from Plan 01 |
| Pitfall 3: OrdinalEncoder fitted on full dataset | Avoided — encoder.fit_transform on X_train only; separate encoder from prerace |
| Pitfall 4: softmax applied globally | Avoided — per_(session_key, lap_number) grouping via composite key |
| Pitfall 5: empty DataFrame not guarded | Implemented — RuntimeError with exact message tested by test_train_inrace_empty_features_raises |
| Pitfall 6: finish_position in feature_cols | Avoided — INRACE_FEATURE_COLS explicitly excludes it; joined only for label derivation |
| Pitfall 7: unknown_value=-1 mismatch | Inherited fix from Plan 01 — build_encoder() uses np.nan throughout |

## Known Stubs

None — no stubs or placeholders in any modified file.

## Threat Flags

No new threat surface beyond the plan's registered STRIDE entries. All mitigations implemented:
- `INRACE_MODEL_PATH` is hardcoded `Path("data/models/inrace_model.joblib")` — no user-input derivation (T-02.2-01)
- `load_inrace_df` uses `text()` with no f-string SQL interpolation (T-02.2-02)
- No PII or secrets emitted to logs (T-02.2-03)
- INNER JOIN enforced — drivers without finish_position excluded (T-02.2-04)

## Self-Check: PASSED

Files created/exist:
- `tests/test_train_inrace.py` — exists
- `src/f1_prediction/ml/loader.py` contains `INRACE_FEATURE_COLS`, `load_inrace_df` — verified
- `src/f1_prediction/ml/pipeline.py` contains `INRACE_MODEL_PATH`, `train_inrace_model` — verified
- `scripts/train.py` contains `train_inrace_model(engine)`, no `TODO(02-02)` — verified

Commits verified:
- ecb5198 test(02-02): add failing end-to-end in-race training tests (RED)
- f237b5d feat(02-02): implement in-race model loader, pipeline, and wire train.py (GREEN)
