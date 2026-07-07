---
status: complete
phase: 02-model-training
source: [02-01-SUMMARY.md, 02-02-SUMMARY.md, 02-03-SUMMARY.md, 02-04-SUMMARY.md]
started: 2026-06-18T00:00:00Z
updated: 2026-07-07T00:00:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Test Suite Passes
expected: Run `PYTHONPATH=src python3.11 -m pytest tests/test_train_prerace.py tests/test_train_inrace.py -v`. All 8 tests pass: 3 prerace + 5 inrace. No failures, no errors.
result: pass
note: initially failed with ImportError (lightgbm not installed); fixed by pip3.11 install lightgbm==4.6.0; all 8 tests now pass

### 2. train.py Runs and Prints Metrics
expected: Run `/usr/local/bin/python3.11 scripts/train.py --model prerace` then `--model inrace`. Each completes with exit code 0 and prints a one-line JSON metrics object to stdout including `brier_score`, `log_loss`, and `sum_to_one_min`/`sum_to_one_max` both ≈ 1.0.
note: expectation updated 2026-07-07 — original expectation (no-arg run, plain-text metric lines, top3_hit_rate) was drafted from early plans; final CLI requires --model and prints JSON (per 02-03/02-04 SUMMARYs)
result: pass

### 3. Both Model Artifacts Created
expected: After running train.py, both `data/models/prerace_model.joblib` and `data/models/inrace_model.joblib` exist on disk.
result: pass

### 4. Model Artifacts Load and Predict
expected: Running `/usr/local/bin/python3.11 -c "import joblib; m=joblib.load('data/models/prerace_model.joblib'); print(type(m).__name__, hasattr(m,'predict_proba'))"` prints `CalibratedClassifierCV True`. Same for inrace_model.joblib.
note: expectation updated 2026-07-07 — original 6-key dict format was from an early plan; final artifact is a fitted CalibratedClassifierCV dumped directly (per 02-03 SUMMARY; validate_model.py loads it this way)
result: pass

## Summary

total: 4
passed: 4
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none]
