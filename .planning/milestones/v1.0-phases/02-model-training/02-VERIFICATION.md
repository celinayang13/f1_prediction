---
phase: 02-model-training
verified: 2026-07-07T00:00:00Z
status: passed
score: 7/8 must-haves verified
behavior_unverified: 0
overrides_applied: 1
overrides:
  - must_have: "Pre-race model trained on constructor performance (ROADMAP SC1)"
    reason: "D-02 planning decision (02-CONTEXT.md) explicitly dropped constructor_pace_delta because it is 100% NULL — the feature builder never computes it and was not implemented in this phase. After the full backfill completed, constructor_pace_delta remains 0/1753 non-null. This matches the CONTEXT 'Deferred Ideas' note: reinstating constructor_pace_delta deferred to post-Phase 4. The ROADMAP SC1 clause is aspirational; D-02 is the binding execution contract."
    accepted_by: "celinayang13"
    accepted_at: "2026-07-07T00:00:00Z"
gaps:
  - truth: "Pre-race model trained on constructor performance (ROADMAP SC1)"
    status: overridden
    reason: >
      ROADMAP SC1 requires the pre-race model to be trained on "constructor
      performance". The features_prerace table has a constructor_pace_delta
      column but it is 100% NULL (0 of 1753 rows populated) and the column
      is NOT in PRERACE_FEATURE_COLS. The D-03 planning decision intentionally
      excluded it, but the data was never populated in the feature builder
      either. The ROADMAP contract is unmet on this clause.
    artifacts:
      - path: "scripts/train.py"
        issue: "PRERACE_FEATURE_COLS does not include constructor_pace_delta"
      - path: "src/f1_prediction/features/prerace.py"
        issue: "constructor_pace_delta column is all-NULL in features_prerace (0/1753 non-NULL rows)"
    missing:
      - "Populate constructor_pace_delta in the feature builder (e.g., team constructor pace delta vs field at circuit over last 2 seasons)"
      - "Add constructor_pace_delta to PRERACE_FEATURE_COLS in scripts/train.py once populated"
      - "Alternatively, accept this deviation via override if D-03 scope narrowing is intentional (see override suggestion below)"
---

# Phase 2: Model Training Verification Report

**Phase Goal:** Train and serialize a calibrated XGBoost win-probability model on 2022-2025 historical data. Produce two artifacts: prerace_model.joblib (qualifying/practice features → win probability) and inrace_model.joblib (mid-race state features → lap-by-lap win probability). Both artifacts must produce valid probability distributions (sum to 1.0 ± 0.001 per race/lap group) on held-out 2024 data.
**Verified:** 2026-07-07
**Status:** gaps_found
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | prerace_model.joblib exists as a fitted CalibratedClassifierCV with predict_proba | ✓ VERIFIED | File at data/models/prerace_model.joblib, 2,972,835 bytes; `type(m).__name__ == 'CalibratedClassifierCV'` and `hasattr(m, 'predict_proba')` both True |
| 2 | Pre-race model is trained on constructor performance (ROADMAP SC1) | ✗ FAILED | `constructor_pace_delta` column exists in features_prerace but is 100% NULL (0/1753 rows populated). Column is absent from `PRERACE_FEATURE_COLS` in scripts/train.py. |
| 3 | Pre-race model probabilities sum to 1.0 ± 0.001 per race on held-out 2024 data (ROADMAP SC2) | ✓ VERIFIED | validate_model.py: prerace 30 race groups, min_sum=0.9999999999999998, max_sum=1.0000000000000002, violations=[] |
| 4 | inrace_model.joblib exists as a fitted CalibratedClassifierCV with predict_proba | ✓ VERIFIED | File at data/models/inrace_model.joblib, 7,021,603 bytes; CalibratedClassifierCV with predict_proba confirmed |
| 5 | In-race model uses mid-race features including tyre compound, tyre age, and pit stop data (ROADMAP SC3 / INRACE-03) | ✓ VERIFIED | INRACE_NUMERIC_FEATURE_COLS includes pit_stop_count, tyre_age_laps; tyre_compound one-hot encodes to 5 columns (HARD/INTERMEDIATE/MEDIUM/SOFT/WET); features_inrace has 84,258 rows with non-NULL values |
| 6 | In-race model probabilities sum to 1.0 ± 0.001 per (session_key, lap_number) on held-out 2024 (INRACE-02) | ✓ VERIFIED | validate_model.py: 1566 lap groups, min_sum=0.9999999999999997, max_sum=1.0000000000000004, violations=[] |
| 7 | In-race model outputs distinguishably different probability distributions across laps (ROADMAP SC4 / INRACE-02) | ✓ VERIFIED | test_inrace_lap_by_lap_variation passes (pivot.std(axis=1).sum() > 0 confirmed); 1566 unique (session_key, lap_number) groups in validation |
| 8 | Training corpus is complete: 2022-2025 data with no NULL round_numbers for 2023+ sessions | ✓ VERIFIED | DB query: 0 raw_sessions rows with year>=2023 and NULL/0 round_number; features_prerace: 1,753 total rows (1,035 for 2023+2024 race sessions); features_inrace: 84,258 rows; championship_position NULL rate for round>=2 races in 2023+ = 0.0% |

**Score:** 7/8 truths verified (0 present, behavior-unverified)

---

### Gap: Constructor Performance Not in Pre-Race Model

**This gap looks intentional.** The D-03 planning decision (recorded in 02-CONTEXT.md) explicitly narrowed the pre-race feature set to 7 columns. `constructor_pace_delta` is in the features_prerace schema but the feature builder never populated it (100% NULL). To formally accept this deviation:

```yaml
overrides:
  - must_have: "Pre-race model trained on constructor performance (ROADMAP SC1)"
    reason: "D-03 planning decision scoped PRERACE_FEATURE_COLS to 7 features. constructor_pace_delta column was never populated in the feature builder and was intentionally excluded from the initial model. Feature can be added in a future iteration when the builder is extended."
    accepted_by: "your-username"
    accepted_at: "2026-07-07T00:00:00Z"
```

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `scripts/train.py` | CLI with --model prerace/inrace; 10 functions; PRERACE_FEATURE_COLS, INRACE_NUMERIC_FEATURE_COLS, TYRE_COMPOUND_VALUES constants | ✓ VERIFIED | All 10 functions found (load_prerace_training, train_prerace_model, save_model, predict_with_softmax, load_inrace_training, train_inrace_model, build_inrace_feature_matrix, encode_tyre_compound, predict_inrace_with_softmax, main); all 3 constants present |
| `scripts/validate_model.py` | CLI with 6 flags; 5 functions including _assert_sums_within_tolerance | ✓ VERIFIED | All 5 functions found; all 6 CLI flags present; imports from train correctly |
| `scripts/backfill.py` | fix_round_numbers function wired into main() | ✓ VERIFIED | fix_round_numbers defined; imports defaultdict, sqlalchemy.text, get_engine; UPDATE raw_sessions SET round_number present; fix_round_numbers(get_engine()) called in main() |
| `tests/test_backfill_extended.py` | 6 integration tests; real_engine fixture | ✓ VERIFIED | All 6 required test functions present; real_engine fixture confirmed |
| `tests/test_train.py` | 15 tests: 7 prerace + 8 inrace | ✓ VERIFIED | All 15 required test functions present; 15 passed in last run |
| `tests/test_validate_model.py` | 5 tests; imports _assert_sums_within_tolerance | ✓ VERIFIED | All 5 tests present; uses pytest.raises(AssertionError) directly against manufactured pd.Series |
| `data/models/prerace_model.joblib` | Fitted CalibratedClassifierCV; size 1KB–50MB | ✓ VERIFIED | 2,972,835 bytes; type=CalibratedClassifierCV; predict_proba=True |
| `data/models/inrace_model.joblib` | Fitted CalibratedClassifierCV; size 1KB–200MB | ✓ VERIFIED | 7,021,603 bytes; type=CalibratedClassifierCV; predict_proba=True |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| scripts/train.py | src/f1_prediction/db/engine.py | `from f1_prediction.db.engine import get_engine` | ✓ WIRED | Import confirmed in source |
| scripts/train.py | data/models/prerace_model.joblib | `joblib.dump(model, path)` in save_model() | ✓ WIRED | Pattern present; artifact produced at expected path |
| scripts/train.py | data/models/inrace_model.joblib | `joblib.dump(model, path)` in save_model() | ✓ WIRED | Pattern present; artifact produced at expected path |
| scripts/train.py (inrace) | features_inrace JOIN features_prerace | SQL JOIN on (session_key, driver_number) | ✓ WIRED | `JOIN features_prerace fp` and `fi.session_key = fp.session_key` both confirmed in source |
| scripts/validate_model.py | scripts/train.py | `from train import load_prerace_training, load_inrace_training, build_inrace_feature_matrix, PRERACE_FEATURE_COLS` | ✓ WIRED | Import confirmed in source |
| scripts/validate_model.py | data/models/prerace_model.joblib | `joblib.load(prerace_model)` in validate_prerace_model() | ✓ WIRED | `prerace_model.joblib` referenced; artifact loads successfully |
| scripts/validate_model.py | data/models/inrace_model.joblib | `joblib.load(inrace_model)` in validate_inrace_model() | ✓ WIRED | `inrace_model.joblib` referenced; artifact loads successfully |
| tests/test_train.py | scripts/train.py | `from train import ...` via sys.path insert | ✓ WIRED | 15 tests import and exercise train.py functions |
| tests/test_validate_model.py | scripts/validate_model.py | `from validate_model import validate_sum_to_one, _assert_sums_within_tolerance` | ✓ WIRED | Both functions imported and exercised |
| scripts/backfill.py | src/f1_prediction/db/engine.py | `from f1_prediction.db.engine import get_engine` in fix_round_numbers | ✓ WIRED | Import and call site confirmed |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| prerace_model.joblib | X_train from PRERACE_FEATURE_COLS | features_prerace table (1,753 rows) | Yes — 750 train rows, 598 val rows | ✓ FLOWING |
| inrace_model.joblib | X_train from INRACE_NUMERIC_FEATURE_COLS + tyre one-hot | features_inrace JOIN features_prerace (84,258 rows) | Yes — 36,756 train rows, 28,952 val rows | ✓ FLOWING |
| validate_model.py JSON output | raw_proba from predict_proba() | Both model artifacts via joblib.load | Yes — prerace n_groups=30, inrace n_groups=1566 | ✓ FLOWING |
| features_prerace.constructor_pace_delta | constructor pace delta | Feature builder — NOT POPULATED | No — all 1,753 rows are NULL | ✗ DISCONNECTED |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| validate_model.py runs and produces per-race sum-to-1 | `python3.11 scripts/validate_model.py --skip-inrace --val-year 2024` | prerace: n_groups=30, min_sum=0.9999999999999998, max_sum=1.0000000000000002, violations=[] | ✓ PASS |
| joblib.load returns CalibratedClassifierCV | Python: `type(joblib.load('data/models/prerace_model.joblib')).__name__` | `CalibratedClassifierCV` | ✓ PASS |
| train.py CLI shows both model choices | `python3.11 scripts/train.py --help` | Shows `--model {prerace,inrace}` | ✓ PASS |
| validate_model.py CLI shows all 6 flags | `python3.11 scripts/validate_model.py --help` | All 6 flags confirmed | ✓ PASS |
| 15 test_train.py tests pass | `PYTHONPATH=src python3.11 -m pytest tests/test_train.py -q` | 15 passed in 1.35s | ✓ PASS |
| 5 test_validate_model.py tests pass | `PYTHONPATH=src python3.11 -m pytest tests/test_validate_model.py -q` | 5 passed in 0.85s | ✓ PASS |
| 6 test_backfill_extended.py tests pass | `PYTHONPATH=src python3.11 -m pytest tests/test_backfill_extended.py -q` | 6 passed in 0.85s | ✓ PASS |

---

### Probe Execution

No probe scripts declared in PLAN or SUMMARY files. Phase 2 uses pytest + direct CLI invocation as the verification mechanism. Step 7c: SKIPPED (no probe-*.sh files declared).

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| PRERACE-01 | 02-02-PLAN.md | Pre-race model trained on qualifying gap, practice times, circuit history, championship standings | ✓ SATISFIED | PRERACE_FEATURE_COLS covers all listed features except constructor; 7 features confirmed in model; constructor deviation noted in gap |
| PRERACE-02 | 02-02-PLAN.md, 02-04-PLAN.md | Pre-race probabilities sum to 1.0 across all drivers | ✓ SATISFIED | validate_model.py: 30 race groups, violations=[], sum within 2e-16 of 1.0 |
| PRERACE-03 | 02-02-PLAN.md | Pre-race artifact available before lights out, qualifying data as input | ✓ SATISFIED | prerace_model.joblib exists; PRERACE_FEATURE_COLS uses qualifying/practice features |
| INRACE-01 | 02-03-PLAN.md | In-race model trained on mid-race state (positions, gaps, laps remaining, tyre, pit history) | ✓ SATISFIED | INRACE_NUMERIC_FEATURE_COLS = 9 features including gap_to_leader, position, laps_remaining, tyre_age_laps, pit_stop_count; tyre_compound one-hot encoded |
| INRACE-02 | 02-03-PLAN.md, 02-04-PLAN.md | In-race model re-runs after each lap with updated distribution | ✓ SATISFIED | per-(session_key, lap_number) softmax confirmed; test_inrace_lap_by_lap_variation passes; 1566 unique lap groups validated |
| INRACE-03 | 02-03-PLAN.md | In-race model incorporates live pit stop data (tyre changes) | ✓ SATISFIED | pit_stop_count, tyre_compound, tyre_age_laps all in INRACE_NUMERIC_FEATURE_COLS; test_inrace_pit_tyre_features_present passes |

**Requirements outside Phase 2 scope (not verified here):**
- DASH-01 through DASH-04: Phase 3 (not yet started)

---

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| — | None found | — | No TBD/FIXME/XXX markers, no f-string SQL, no placeholder implementations, no stubs in delivered functions |

The only notable anti-pattern concern was the `--model inrace` NotImplementedError stub in Plan 02-02, but this was correctly replaced in Plan 02-03. The AST check confirms no `NotImplementedError` raise is present in the current `scripts/train.py`.

---

### Key Technical Decisions Verified

| Decision | Expected | Actual | Status |
|----------|----------|--------|--------|
| XGBoost (not LightGBM) for models | XGBClassifier | XGBClassifier with scale_pos_weight (not is_unbalance) | ✓ CORRECT |
| cv=5 (not cv='prefit') for calibration | CalibratedClassifierCV(cv=5, method='isotonic') | Both confirmed in AST | ✓ CORRECT |
| Per-race softmax for prerace | groupby("session_key") | Present in predict_with_softmax | ✓ CORRECT |
| Per-lap softmax for inrace | groupby(["session_key", "lap_number"]) | Present in predict_inrace_with_softmax | ✓ CORRECT |
| No f-string SQL | All SQL via text() + bound params | Confirmed in train.py and validate_model.py | ✓ CORRECT |
| Lap-completion filter for is_winner | MAX(rp2.lap_number) >= total_laps - 1 | Present in both load_prerace_training and load_inrace_training | ✓ CORRECT |
| encode_tyre_compound stable schema | pd.get_dummies + reindex to canonical 5 columns | tyre_HARD, tyre_INTERMEDIATE, tyre_MEDIUM, tyre_SOFT, tyre_WET confirmed | ✓ CORRECT |

---

### Gaps Summary

One gap was found against ROADMAP SC1:

**Constructor performance feature not in pre-race model.** ROADMAP SC1 specifies "constructor performance" as a required training feature. The `constructor_pace_delta` column exists in `features_prerace` schema but is 100% NULL (never populated by the feature builder). It is absent from `PRERACE_FEATURE_COLS` in `scripts/train.py`. The D-03 planning decision intentionally narrowed the feature set to 7 columns, but neither populated the column nor explicitly documented this as an accepted deviation from SC1.

**Impact:** The pre-race model is trained without any constructor-level signal. The seven features used (qualifying delta, practice pace x2, circuit win rate, championship position, grid position, driver seasons) are meaningful predictors, and the model produces valid calibrated probabilities (Brier=0.0381, log-loss=0.1573). The omission doesn't block Phase 3 but is a gap against the ROADMAP contract.

**Resolution options:**
1. Accept the deviation via override (see override block above) — appropriate if D-03 is considered the binding contract
2. Populate `constructor_pace_delta` in the feature builder (e.g., team mean pace delta vs field at this circuit over last 2 seasons) and retrain

---

_Verified: 2026-07-07_
_Verifier: Claude (gsd-verifier)_
