---
quick_id: 260807-lo4
description: Regularize XGBoost hyperparameters to fix overfitting in prerace/inrace models
completed: 2026-08-07
status: complete
commits:
  - 9805648 fix(260807-lo4): regularize XGBoost hyperparameters to reduce overfitting
  - bb8ba73 chore(260807-lo4): retrain model artifacts with regularized hyperparameters
---

# Quick Task 260807-lo4: Summary

## What was done

1. **Benchmark** (scratchpad, not committed): compared the production calibrated XGBoost
   (n_estimators=500, max_depth=6) against LightGBM (default + shallow), logistic
   regression, and a regularized shallow XGBoost on two chronological splits —
   train 2022–23 → val 2024 and train 2022–24 → val 2025 (2025 data was previously
   unused for validation). Metrics: Brier, log loss on calibrated probabilities, plus
   post-softmax winner NLL and top-1 winner pick accuracy.
2. **Finding**: the depth-6/500-tree config overfits. Shallow XGBoost
   (n_estimators=200, max_depth=3, subsample=0.8, colsample_bytree=0.8) won on both
   splits for both models. LightGBM-shallow tied in-race but lost pre-race; logistic
   regression competitive pre-race only. min_child_weight=5 tested and dropped —
   identical real-data metrics, and it broke the tiny-fixture lap-variation test.
3. **Change**: updated both `XGBClassifier` configs in scripts/train.py; retrained both
   artifacts (train 2022–23, val 2024); validated with scripts/validate_model.py.

## Results (val 2024, current DB snapshot)

| Model | Config | Brier | Log loss |
|---|---|---|---|
| prerace | old (depth 6, 500 trees) | 0.0433 | 0.3148 |
| prerace | new (depth 3, 200 trees) | 0.0423 | 0.2483 |
| inrace | old | 0.0503 | 0.2163 |
| inrace | new | 0.0464 | 0.1836 |

On val 2025 (train 2022–24) improvements were larger: prerace Brier 0.0310→0.0276,
inrace Brier 0.0306→0.0248, inrace winner top-1 0.771→0.808.

Note: the 02-02/02-03 SUMMARY baselines (prerace 0.0381/0.1573) were computed on an
older DB snapshot with fewer 2024 races and are not directly comparable; the old-config
numbers above were re-measured on today's DB for a like-for-like comparison.

## Verification

- `pytest -m "not slow"`: 67 passed, 2 deselected.
- `scripts/validate_model.py`: exit 0, violations [] for both models, sum-to-1 within 1e-15.
