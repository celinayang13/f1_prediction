---
phase: 02-model-training
plan: 01
subsystem: ml
tags: [xgboost, ml-training, calibration, prerace, tdd]
dependency_graph:
  requires:
    - 01-03: features_prerace table populated by Phase 1 backfill
  provides:
    - src/f1_prediction/ml/ package (loader, pipeline, evaluate)
    - scripts/train.py CLI entry point
    - data/models/prerace_model.joblib artifact (runtime output)
  affects:
    - 02-02: in-race model plan imports from ml/ package and extends train.py
    - 03-xx: Phase 3 loads data/models/prerace_model.joblib for live inference
tech_stack:
  added:
    - xgboost>=3.0,<4.0 (3.2.0 installed)
    - scikit-learn>=1.8,<2.0 (1.9.0 installed)
    - scipy>=1.17,<2.0 (1.17.1 installed)
    - joblib>=1.5,<2.0 (1.5.3 installed)
    - libomp (macOS Homebrew, required by xgboost dylib)
  patterns:
    - OrdinalEncoder + enable_categorical (RESEARCH Pattern 1)
    - FrozenEstimator + CalibratedClassifierCV sigmoid (RESEARCH Pattern 2)
    - per-session softmax via groupby.transform (RESEARCH Pattern 3)
    - temporal train/test split year <= 2024 / year == 2025 (RESEARCH Pattern 4)
    - joblib artifact dict with 6-key Phase 3 contract (RESEARCH Pattern 5)
key_files:
  created:
    - src/f1_prediction/ml/__init__.py
    - src/f1_prediction/ml/loader.py
    - src/f1_prediction/ml/pipeline.py
    - src/f1_prediction/ml/evaluate.py
    - scripts/train.py
    - tests/test_train_prerace.py
  modified:
    - pyproject.toml (4 ML dependencies added)
    - .gitignore (data/models/ excluded)
decisions:
  - "D-01 honored: XGBoost used instead of LightGBM per user explicit preference"
  - "D-07 + RESEARCH: unknown_value=np.nan (not -1) to align with XGBoost NaN sentinel"
  - "D-09 corrected: FrozenEstimator used instead of cv=prefit (removed in sklearn 1.8)"
  - "cv clamped to min_class_count: handles small test fixtures without breaking production path"
metrics:
  duration_minutes: 12
  completed_date: "2026-06-18"
  tasks_completed: 3
  tasks_total: 3
  files_created: 6
  files_modified: 2
---

# Phase 02 Plan 01: Pre-Race Model Training Summary

**One-liner:** XGBoost binary classifier with Platt scaling via FrozenEstimator, trained on 2022-2024 features, calibrated on 2025 test set, serialized as a 6-key joblib artifact dict for Phase 3 inference.

## What Was Built

Three tasks completed the full pre-race model training vertical slice:

**Task 1 (RED):** Added xgboost, scikit-learn, scipy, joblib to pyproject.toml; created `src/f1_prediction/ml/__init__.py`; added `data/models/` to .gitignore; wrote three failing tests in `tests/test_train_prerace.py`.

**Task 2 (GREEN):** Implemented `src/f1_prediction/ml/loader.py`, `pipeline.py`, and `evaluate.py` until all three tests passed. The training pipeline follows RESEARCH patterns exactly: OrdinalEncoder fit on train-only, FrozenEstimator calibration, per-session softmax normalization.

**Task 3:** Created `scripts/train.py` as a thin CLI entry point mirroring `build_features.py`, with a `# TODO(02-02)` insertion point for the in-race model call.

## Key Files

| File | Purpose |
|------|---------|
| `src/f1_prediction/ml/loader.py` | `load_prerace_df()` with text() parameterized SQL JOIN; `PRERACE_FEATURE_COLS` (10 cols), `PRERACE_CAT_COLS` |
| `src/f1_prediction/ml/pipeline.py` | `train_prerace_model()` orchestration; `build_encoder`, `train_xgb_classifier`, `calibrate_model`, `save_artifact` |
| `src/f1_prediction/ml/evaluate.py` | `compute_log_loss`, `per_session_softmax` (groupby pattern), `compute_top3_hit_rate` |
| `scripts/train.py` | CLI entry point, `python scripts/train.py` produces prerace_model.joblib |
| `tests/test_train_prerace.py` | 3 end-to-end tests covering artifact creation, per-session softmax, and empty-DB guard |

## Artifact Contract (Phase 3)

`data/models/prerace_model.joblib` is a Python dict with keys:

```
{
    "model": CalibratedClassifierCV(FrozenEstimator(xgb_model), method="sigmoid"),
    "feature_cols": ["qualifying_delta_to_pole", ..., "team_name"],  # 10 cols, ordered
    "cat_cols": ["team_name"],
    "encoder": fitted OrdinalEncoder,
    "trained_on": "2022-2024",
    "tested_on": "2025",
}
```

Phase 3 must call `encoder.transform(X[cat_cols])` before `model.predict_proba(X[feature_cols])`.

## Test Outcomes

```
tests/test_train_prerace.py::test_train_prerace_end_to_end        PASSED
tests/test_train_prerace.py::test_train_prerace_softmax_sums_to_one PASSED
tests/test_train_prerace.py::test_train_prerace_empty_features_raises PASSED
```

TDD RED→GREEN cycle: 3 tests written in Task 1 (RED), all passed in Task 2 (GREEN).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing error handling] cv clamped to min_class_count for small test sets**
- **Found during:** Task 2 GREEN run
- **Issue:** `CalibratedClassifierCV` defaults to `cv=5` but requires at least 5 examples per class. With the test fixture seeding 2 winners in 2025 data, `cv=5` raises `ValueError: Requesting 5-fold cross-validation but provided less than 5 examples for at least one class.`
- **Fix:** `calibrate_model` now computes `cv = min(5, max(2, min_class_count))` using the actual minority class count. Production data (~480 rows, ~24 winners) always uses `cv=5`. Small test fixtures gracefully reduce to `cv=2`.
- **Files modified:** `src/f1_prediction/ml/pipeline.py`
- **Commit:** 7aa8f19

**2. [Rule 1 - Bug] D-06 unknown_value=-1 overridden by RESEARCH Pitfall 7**
- **Found during:** Task 2 implementation (proactively applied per plan instructions)
- **Issue:** CONTEXT.md D-06 specifies `unknown_value=-1` but RESEARCH Pitfall 7 documents that XGBoost's missing sentinel is NaN by default. Using `-1` without `XGBClassifier(missing=-1)` causes unknown categories to be routed as regular values, producing wrong predictions silently.
- **Fix:** `build_encoder()` uses `unknown_value=np.nan` and `encoded_missing_value=np.nan` so unknown categories are routed through XGBoost's native NaN/missing branch.
- **Files modified:** `src/f1_prediction/ml/pipeline.py`
- **Decision adjustment documented in artifact docstring and SUMMARY**
- **Commit:** 7aa8f19

**3. [Rule 3 - Blocking] libomp not installed on macOS**
- **Found during:** Task 1 xgboost installation
- **Issue:** `xgboost` requires OpenMP runtime (`libomp.dylib`) on macOS. Install failed with `XGBoostError: Library (libxgboost.dylib) could not be loaded... libomp.dylib not found`.
- **Fix:** `brew install libomp` resolved the dependency.
- **Impact:** No code changes — environment setup only.

**4. [Rule 3 - Blocking] Venv has no Python binary**
- **Found during:** Task 1 xgboost installation
- **Issue:** The project `.venv/bin/` directory is empty (no python binary). The venv contains only site-packages stubs. Python 3.11 system install at `/Library/Frameworks/Python.framework/Versions/3.11/` is used instead, with user site-packages at `~/Library/Python/3.11/`.
- **Fix:** Used `python3.11` + `pip3.11` directly; tests run as `PYTHONPATH=src python3.11 -m pytest`.
- **Impact:** No code changes — all functionality works; test commands use explicit Python version.

## RESEARCH Pitfalls Encountered

| Pitfall | Status |
|---------|--------|
| Pitfall 1: cv=prefit removed in sklearn 1.8 | Avoided — FrozenEstimator used throughout |
| Pitfall 2: xgboost not installed | Fixed — added to pyproject.toml; pip3.11 install run |
| Pitfall 3: OrdinalEncoder fit before split | Avoided — encoder.fit_transform on X_train only |
| Pitfall 4: softmax applied globally | Avoided — per_session_softmax uses groupby.transform |
| Pitfall 5: empty DataFrame not guarded | Implemented — RuntimeError raised with clear message |
| Pitfall 6: finish_position in feature_cols | Avoided — PRERACE_FEATURE_COLS explicitly excludes it |
| Pitfall 7: unknown_value=-1 mismatch | Fixed — using np.nan as documented (D-06 override) |

## Known Stubs

The `# TODO(02-02): train_inrace_model(engine)` comment in `scripts/train.py` is an intentional insertion point — not a stub preventing plan goal achievement. The pre-race model is fully functional. Plan 02-02 will add the in-race model call at this marker.

## Threat Flags

No new threat surface beyond the plan's registered STRIDE entries (T-02-01, T-02-02, T-02-03, T-02-04, T-02-SC). All mitigations implemented as designed:
- Artifact path hardcoded as `Path("data/models/prerace_model.joblib")` (T-02-01)
- All SQL uses `text()` with bound `:param` names (T-02-02)
- No PII or secrets emitted to logs (T-02-03)

## Self-Check: PASSED

Files created/exist:
- `src/f1_prediction/ml/__init__.py` — exists
- `src/f1_prediction/ml/loader.py` — exists
- `src/f1_prediction/ml/pipeline.py` — exists
- `src/f1_prediction/ml/evaluate.py` — exists
- `scripts/train.py` — exists
- `tests/test_train_prerace.py` — exists
- `.gitignore` contains `data/models/` — verified
- `pyproject.toml` contains all 4 new dependencies — verified

Commits verified:
- 5eaa016 test(02-01): add failing end-to-end pre-race training tests (RED)
- 7aa8f19 feat(02-01): implement ml package (loader/pipeline/evaluate) — tests GREEN
- 59ab851 feat(02-01): wire scripts/train.py CLI entry point for pre-race model
