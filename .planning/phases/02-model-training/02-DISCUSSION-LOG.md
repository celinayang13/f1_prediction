# Phase 2: Model Training - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-24
**Phase:** 02-model-training
**Areas discussed:** Data gaps

---

## Data Gaps

### NULL columns (team_name, circuit_avg_finish, constructor_pace_delta)

| Option | Description | Selected |
|--------|-------------|----------|
| Drop them from training | Remove the 3 100%-NULL columns entirely — no signal, simpler model, add back after full backfill | ✓ |
| Fix Phase 1 code first | Investigate why circuit_avg_finish and constructor_pace_delta are 100% NULL despite Phase 1 passing | |
| Keep them, let XGBoost handle it | XGBoost can learn default split for NULLs; 100% NULLs contribute nothing but don't break training | |

**User's choice:** Drop them from training
**Notes:** User agreed these contribute no signal at 100% NULL. Decision is revisable after backfill populates them.

---

### Sparse 2023–2025 coverage (1 race per year)

| Option | Description | Selected |
|--------|-------------|----------|
| Train on 2022 data only | Use the 16 2022 races; model is biased to 2022 but satisfies Phase 2 goal | |
| Extend backfill before training | Pause Phase 2, add full 2023–2025 coverage, then train | ✓ |
| Include 2023–2025 sparse rows as-is | Keep all 378 prerace rows; sparse 2023–2025 rows add marginal signal | |

**User's choice:** Extend backfill before training
**Notes:** Full 2023–2025 backfill makes cross-season circuit features (circuit_avg_finish, constructor_pace_delta) computable.

---

### Backfill scope

| Option | Description | Selected |
|--------|-------------|----------|
| All available races 2023–2025 | Full backfill, ~60–70 races; maximizes training data and circuit diversity | ✓ |
| 2023 + 2024 only | Skip 2025 in-progress season | |
| Minimum to get circuit_avg_finish populated | Add just 2023 (~22 races) | |

**User's choice:** All available races 2023–2025
**Notes:** Resumable backfill script already handles partial runs.

---

## Claude's Discretion

- **Model framework:** XGBoost (consistent with ROADMAP.md; team_name dropped so LightGBM categorical advantage is moot)
- **Validation strategy:** TimeSeriesSplit by season; Brier score + log loss as metrics; no minimum threshold required for Phase 2 (ROADMAP success criterion is valid distribution only)
- **Calibration method:** CalibratedClassifierCV isotonic + softmax normalization per race
- **Training script:** Single `scripts/train.py --model prerace|inrace`; fixed hyperparameter defaults for v1 (n_estimators=500, lr=0.05, max_depth=6)

## Deferred Ideas

- LightGBM as alternative — deferred; XGBoost stays per roadmap
- Hyperparameter search (Optuna/grid) — deferred to post-Phase 4 iteration
- Top-3 (podium) prediction — deferred to REQUIREMENTS.md v2
- team_name, circuit_avg_finish, constructor_pace_delta as features — deferred; re-evaluate after full backfill
