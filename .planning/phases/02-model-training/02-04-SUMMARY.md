---
phase: 02-model-training
plan: 04
subsystem: ml-validation
status: complete
tags:
  - python
  - validation
  - softmax
  - metrics
  - joblib
depends_on:
  requires:
    - 02-02 (prerace_model.joblib)
    - 02-03 (inrace_model.joblib)
  provides:
    - scripts/validate_model.py — standalone CLI to validate saved model artifacts on held-out 2024 data
    - tests/test_validate_model.py — 5 unit tests for validate_sum_to_one helper
  affects:
    - Phase 3 inference loop (confirms artifacts are Phase-3-ready)
tech_stack:
  added:
    - validate_sum_to_one + _assert_sums_within_tolerance pattern — decouples violation-path testing from softmax
  patterns:
    - _assert_sums_within_tolerance extracted as module-level helper for direct unit-test access without mocking softmax
    - per-race groupby session_key for prerace; per-(session_key, lap_number) for inrace — mirrors train.py patterns
    - joblib.load → predict_proba → validate_sum_to_one → JSON report flow
key_files:
  created:
    - scripts/validate_model.py
    - tests/test_validate_model.py
  modified: []
decisions:
  - _assert_sums_within_tolerance extracted as module-level helper (not nested in validate_sum_to_one) so violation path is directly testable with manufactured pd.Series inputs — no softmax mock needed
  - tolerance=0.0 correctly triggers float-precision violations (~2e-16) — default 0.001 is the right production threshold
  - validate_sum_to_one accepts raw_proba + group_keys args (not a model) so the math is testable without DB or model load
metrics:
  duration_minutes: 13
  completed_date: 2026-07-07
  tasks_completed: 3
  files_changed: 2
---

# Phase 02 Plan 04: Model Validation Summary

Standalone validation CLI (`scripts/validate_model.py`) that loads both saved model artifacts, runs `predict_proba` on held-out 2024 data, applies per-group softmax, and asserts sum-to-1 within 0.001 tolerance — confirming ROADMAP success criteria PRERACE-02 and INRACE-02 end-to-end.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create scripts/validate_model.py CLI for sum-to-1 validation | `d33b67b` | scripts/validate_model.py |
| 2 | Create tests/test_validate_model.py for validate_sum_to_one unit tests | `245da92` | tests/test_validate_model.py |
| 3 | Run validate_model.py against saved artifacts and confirm success criterion | `b1c0bae` | (read-only; model artifacts unchanged) |

## Validation Metrics (--val-year 2024 --tolerance 0.001)

| Model | n_val_rows | n_groups | brier_score | log_loss | min_sum | max_sum | violations |
|-------|-----------|----------|-------------|----------|---------|---------|------------|
| prerace | 598 | 30 | 0.0381 | 0.1573 | 0.9999999999999998 | 1.0000000000000002 | [] |
| inrace | 28,952 | 1,566 | 0.0507 | 0.2125 | 0.9999999999999997 | 1.0000000000000004 | [] |

Both models: sums within 2e-16 of 1.0 (well within 0.001 tolerance).

## ROADMAP Success Criteria Confirmation

| Criterion | Status |
|-----------|--------|
| PRERACE-02: prerace artifact produces per-race sum-to-1 on held-out 2024 (±0.001) | PASS |
| INRACE-02: inrace artifact produces per-(session_key, lap_number) sum-to-1 on held-out 2024 (±0.001) | PASS |
| data/models/prerace_model.joblib exists and is loadable | PASS |
| data/models/inrace_model.joblib exists and is loadable | PASS |
| Brier score > 0 for both models (non-trivial predictions) | PASS |
| Log loss > 0 for both models (non-trivial predictions) | PASS |
| --skip-inrace flag: exits 0 with exactly 1 entry in validations | PASS |
| Validator is read-only (no model artifact modifications) | PASS |

## Test Results

| Test | Status |
|------|--------|
| test_validate_sum_to_one_passes_for_uniform_distribution | PASS |
| test_validate_sum_to_one_passes_for_varied_distribution | PASS |
| test_validate_sum_to_one_raises_for_artificial_violation | PASS |
| test_validate_sum_to_one_grouping_by_multiple_columns | PASS |
| test_validate_sum_to_one_returns_min_max_sums | PASS |
| Full test suite (60 tests total, 0 regressions) | PASS |

## Acceptance Criteria Results

| Criterion | Status |
|-----------|--------|
| scripts/validate_model.py exists and defines all 5 required functions | PASS |
| _assert_sums_within_tolerance is module-level (not nested) | PASS |
| Imports load_prerace_training, load_inrace_training, build_inrace_feature_matrix, PRERACE_FEATURE_COLS from train | PASS |
| Imports softmax from scipy.special | PASS |
| No f-string SQL (inherits parameterized text() from train.py) | PASS |
| --help shows all 6 flags, exits 0 | PASS |
| PYTHONPATH=src python3.11 -m pytest tests/test_validate_model.py -v shows 5 PASS | PASS |
| test_validate_sum_to_one_raises_for_artificial_violation calls _assert_sums_within_tolerance directly | PASS |
| Full run exits 0 with 2-element validations list, no violations | PASS |

## Deviations from Plan

None — plan executed exactly as written. The tolerance=0.0 sanity check (Step 3 of Task 3) correctly triggered violations due to float64 precision (~2e-16 deviation from 1.0) — this is expected behavior and confirms the default 0.001 tolerance is correctly calibrated.

## Known Stubs

None. All functions are fully implemented. Phase 2 is complete — both model artifacts exist and pass end-to-end validation; Phase 3 inference integration may begin.

## Threat Flags

No new threat surface introduced. All SQL (transitively via load_prerace_training / load_inrace_training) uses sqlalchemy.text() with bound params. Model artifacts loaded from local trusted paths only (data/models/). Stdout JSON contains only metrics (Brier, log_loss, sums) — no PII. See plan threat register T-02-04-01 through T-02-04-04.

## Self-Check: PASSED

- scripts/validate_model.py exists: FOUND
- tests/test_validate_model.py exists: FOUND
- All 5 required functions in validate_model.py: validate_sum_to_one, _assert_sums_within_tolerance, validate_prerace_model, validate_inrace_model, main — FOUND
- Commits verified: d33b67b, 245da92, b1c0bae all present in git log
- PYTHONPATH=src python3.11 -m pytest tests/test_validate_model.py -v → 5 passed
- PYTHONPATH=src python3.11 -m pytest tests/ -v → 60 passed (0 regressions)
- python3.11 scripts/validate_model.py --val-year 2024 --tolerance 0.001 → exit 0, both models pass
- python3.11 scripts/validate_model.py --skip-inrace → exit 0, 1 entry in validations
- data/models/prerace_model.joblib unchanged (timestamp: Jul 2 19:24): VERIFIED
- data/models/inrace_model.joblib unchanged (timestamp: Jul 2 19:24): VERIFIED
