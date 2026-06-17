# Phase 2: Model Training - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-17
**Phase:** 2-model-training
**Areas discussed:** ML Library, Train/Test Split, Calibration, NULL Handling, Artifact Format, Categorical Encoding, Training Script Design, Hyperparameter Strategy, Evaluation Metrics

---

## ML Library

| Option | Description | Selected |
|--------|-------------|----------|
| LightGBM 4.6.0 | Per CLAUDE.md and STATE.md — native categorical encoding, is_unbalance=True for 1:20 class imbalance, faster iteration | |
| XGBoost | Per ROADMAP overview — 3.x now has categorical support, widely known | ✓ |
| Both — train both and compare | Train LightGBM and XGBoost, pick the better-calibrated one at eval time | |

**User's choice:** XGBoost  
**Notes:** Overrides CLAUDE.md which lists LightGBM as primary. CLAUDE.md will need updating in the plan.

---

## Train/Test Split

| Option | Description | Selected |
|--------|-------------|----------|
| Train 2022–2023, test 2024 | Per original ROADMAP wording | |
| Train 2022–2024, test 2025 | Maximum training data; 2025 is the most recent complete season | ✓ |
| Train 2022–2023, test 2024+2025 | Larger held-out set (2 seasons) | |
| TimeSeriesSplit across all 4 seasons | CV performance but no single held-out test set | |

**User's choice:** Train 2022–2024, test 2025  
**Notes:** User correctly noted 2025 is complete (current date is 2026-06-17). Original ROADMAP wording said "held-out 2024 qualifying session" which predates the 2025 season completing.

---

## Probability Calibration

| Option | Description | Selected |
|--------|-------------|----------|
| Softmax over raw scores | scipy.special.softmax on XGBoost raw scores — already in CLAUDE.md | |
| Platt scaling (CalibratedClassifierCV) | scikit-learn CalibratedClassifierCV with method='sigmoid'. Fits logistic layer | ✓ |
| Isotonic regression calibration | CalibratedClassifierCV method='isotonic'. More flexible but overfits small calibration sets | |

**User's choice:** Platt scaling (CalibratedClassifierCV)  
**Notes:** Softmax normalization over all drivers in a session is still used as the final per-session normalization step (in addition to Platt scaling).

---

## NULL Handling

| Option | Description | Selected |
|--------|-------------|----------|
| XGBoost native NaN handling | XGBoost learns optimal branch direction for NaN — no imputation needed | ✓ |
| Median/mode imputation | Fill NULLs before training, save imputer in artifact | |
| Drop NULL rows | Exclude sprint weekends and debut races from training | |

**User's choice:** XGBoost native NaN handling  
**Notes:** Covers practice_pace_fp3=NULL on sprint weekends and team_name=NULL for debut races (deferred from Phase 1).

---

## Artifact Format

| Option | Description | Selected |
|--------|-------------|----------|
| Joblib dict with model + feature list | {model, feature_cols, cat_cols, encoder, trained_on, tested_on} | ✓ |
| Separate files: model.joblib + features.json | Two files per model | |
| Pickle with full sklearn Pipeline | Wrap preprocessing + model in Pipeline | |

**User's choice:** Joblib dict  
**Notes:** Dict keys are the contract Phase 3 depends on.

---

## Categorical Encoding

| Option | Description | Selected |
|--------|-------------|----------|
| OrdinalEncoder + enable_categorical=True | Standard approach with unknown category handling | ✓ |
| One-hot encode all categoricals | Explodes feature space, risk of unseen team names at inference | |
| Manual label encode dict saved in artifact | Simple but brittle for unseen categories | |

**User's choice:** OrdinalEncoder before XGBoost  
**Notes:** handle_unknown='use_encoded_value', unknown_value=-1 to handle unseen categories at inference.

---

## Training Script Design

| Option | Description | Selected |
|--------|-------------|----------|
| scripts/train.py | Mirrors scripts/backfill.py pattern from Phase 1 | ✓ |
| src/f1_prediction/training/ module | Importable, testable, callable programmatically | |
| Both — thin scripts/train.py calling the module | Clean separation | |

**User's choice:** scripts/train.py  
**Notes:** Always retrain both models on every run (no selective --prerace/--inrace flags).

---

## Hyperparameter Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Fixed sensible defaults | n_estimators=300, max_depth=4, learning_rate=0.05, scale_pos_weight=19 | ✓ |
| Early stopping with eval set | Use early_stopping_rounds=20, automatic n_estimators selection | |
| GridSearchCV with TimeSeriesSplit | Most rigorous, 30–60 min runtime | |

**User's choice:** Fixed sensible defaults  
**Notes:** Data is small (~1,440 pre-race rows), iteration speed matters more than marginal accuracy.

---

## Evaluation Metrics

| Option | Description | Selected |
|--------|-------------|----------|
| Log loss + top-3 hit rate | Log loss for calibration quality + intuitive hit rate | ✓ |
| Brier score + calibration plot | Gold standard, visual output saved as PNG | |
| Just log loss | Minimal output | |

**User's choice:** Log loss + top-3 hit rate on 2025 test set  
**Notes:** No quality gate — print metrics and let developer decide. Hard gate would block Phase 3 on unexpected data quality issues.

---

## Claude's Discretion

None — all areas had explicit user decisions.

## Deferred Ideas

- Hyperparameter tuning (Optuna/GridSearch) — deferred to future improvement phase
- Separate calibration set — acceptable to use test set for Platt fitting at v1 scale
- SHAP feature importance plots — useful for debugging, out of scope for Phase 2
- In-race model retirement handling (variable grid size) — deferred to Phase 3
