---
phase: 02-model-training
verified: 2026-06-17T00:00:00Z
status: human_needed
score: 6/6 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Run python scripts/train.py against a populated data/f1.db and confirm two output lines are printed: 'prerace  log_loss=...' and 'inrace  log_loss=... top3_hit_rate_final_lap=...' and both joblib artifacts are written"
    expected: "Exit code 0; data/models/prerace_model.joblib and data/models/inrace_model.joblib both present; two metric lines on stdout"
    why_human: "Requires a populated database from Phase 1 backfill. Tests use synthetic fixtures only."
  - test: "Evaluate whether the code-review-flagged critical issues (CR-01, CR-02, CR-03) are acceptable risks before advancing to Phase 3"
    expected: "Developer reviews 02-REVIEW.md and decides: (a) accept the issues as-is, (b) apply /gsd-code-review 2 --fix, or (c) document overrides. No Phase 3 plan should import pipeline.py or load joblib artifacts without confirming the fix/override decision."
    why_human: "CR-01 is a framework deviation (XGBoost vs LightGBM mandate), CR-02 is a silent categorical handling defect, CR-03 is inflated evaluation metrics — all require human judgment on severity and timeline."
---

# Phase 2: Model Training Verification Report

**Phase Goal:** Two trained and calibrated XGBoost models exist on disk — one for pre-race win probability from qualifying data, one for in-race win probability from live lap state
**Verified:** 2026-06-17T00:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Pre-race model trained on qualifying gap to pole, practice pace, circuit history, constructor performance, championship standings; artifact at data/models/prerace_model.joblib | VERIFIED | `PRERACE_FEATURE_COLS` contains all 10 required columns including qualifying_delta_to_pole, practice_pace_fp2/fp3, circuit_win_rate, circuit_avg_finish, constructor_pace_delta, championship_position. `PRERACE_MODEL_PATH = Path("data/models/prerace_model.joblib")`. |
| 2 | Pre-race model outputs valid win probability distribution summing to 1.0 ± 0.001 | VERIFIED | `test_train_prerace_softmax_sums_to_one` passes (8/8 tests GREEN). `per_session_softmax` uses groupby.transform(softmax). PRERACE-02 requirement met. |
| 3 | In-race model trained on mid-race state (position, gaps, laps remaining, tyre, pit history); artifact at data/models/inrace_model.joblib | VERIFIED | `INRACE_FEATURE_COLS` = [lap_number, gap_to_leader, tyre_compound, tyre_age_laps, pit_stop_count, position, interval_ahead, interval_behind, pit_stop_delta, laps_remaining]. `INRACE_MODEL_PATH = Path("data/models/inrace_model.joblib")`. |
| 4 | In-race model outputs updated probability distribution that reflects lap-by-lap changes | VERIFIED | `test_train_inrace_softmax_sums_to_one_per_lap` passes. Per-(session_key, lap_number) composite key groups softmax, satisfying INRACE-02. |
| 5 | Both models are produced by a single `python scripts/train.py` invocation | VERIFIED | `scripts/train.py` lines 60–61 call `train_prerace_model(engine)` then `train_inrace_model(engine)` sequentially. No TODO(02-02) marker remains. |
| 6 | Artifacts are serialized joblib dicts in the Phase 3 contract format with 6 keys | VERIFIED | `save_artifact()` dumps `{model, feature_cols, cat_cols, encoder, trained_on, tested_on}`. Tests assert exact key set. Both prerace (tested_on="2025", cat_cols=["team_name"]) and inrace (cat_cols=["tyre_compound"]) confirmed. |

**Score:** 6/6 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `pyproject.toml` | xgboost, scikit-learn, scipy, joblib in dependencies | VERIFIED | Lines 14–17: xgboost>=3.0,<4.0; scikit-learn>=1.8,<2.0; scipy>=1.17,<2.0; joblib>=1.5,<2.0 |
| `.gitignore` | data/models/ excluded | VERIFIED | `grep -F "data/models/" .gitignore` returns match |
| `src/f1_prediction/ml/__init__.py` | Package marker | VERIFIED | File exists |
| `src/f1_prediction/ml/loader.py` | Exports PRERACE_FEATURE_COLS, PRERACE_CAT_COLS, load_prerace_df, INRACE_FEATURE_COLS, INRACE_CAT_COLS, load_inrace_df | VERIFIED | All 6 symbols confirmed importable. INNER JOIN for inrace label. text() parameterized SQL. |
| `src/f1_prediction/ml/pipeline.py` | Exports build_encoder, train_xgb_classifier, calibrate_model, save_artifact, train_prerace_model, PRERACE_MODEL_PATH, train_inrace_model, INRACE_MODEL_PATH | VERIFIED | All 8 symbols confirmed importable. FrozenEstimator used (not cv='prefit'). |
| `src/f1_prediction/ml/evaluate.py` | Exports compute_log_loss, per_session_softmax, compute_top3_hit_rate | VERIFIED | All 3 symbols importable. groupby.transform(softmax) for per-session normalization. |
| `scripts/train.py` | CLI entry point producing both artifacts | VERIFIED | Syntactically valid. --help works. No --prerace-only/--inrace-only flags. Both model calls present. |
| `tests/test_train_prerace.py` | 3 end-to-end tests including test_train_prerace_end_to_end | VERIFIED | 3 tests; all pass. |
| `tests/test_train_inrace.py` | 5 end-to-end tests including test_train_inrace_end_to_end | VERIFIED | 5 tests; all pass. |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| scripts/train.py | f1_prediction.ml.pipeline.train_prerace_model | import + call line 60 | WIRED | Confirmed at lines 34, 60 |
| scripts/train.py | f1_prediction.ml.pipeline.train_inrace_model | import + call line 61 | WIRED | Confirmed at lines 34, 61 |
| pipeline.calibrate_model | sklearn.frozen.FrozenEstimator | FrozenEstimator wrapping | WIRED | Lines 171–176: `CalibratedClassifierCV(FrozenEstimator(fitted_model), method="sigmoid", cv=cv)`. cv='prefit' count: 0. |
| pipeline.save_artifact | data/models/prerace_model.joblib | joblib.dump of 6-key dict | WIRED | lines 217–227: path.parent.mkdir + joblib.dump(artifact, path) |
| loader.load_inrace_df | features_prerace (label JOIN) | INNER JOIN on (session_key, driver_number) | WIRED | Lines 138–164: explicit 3-table JOIN. test_train_inrace_label_join_to_prerace confirms INNER semantics. |
| loader.load_prerace_df | raw_sessions (year column) | JOIN on session_key | WIRED | Lines 66–75: confirmed parameterized text() query |

---

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| prerace_model.joblib | `df` from load_prerace_df | features_prerace + raw_sessions JOIN via text() | Yes — SQL query with WHERE clause; empty guard raises RuntimeError | FLOWING |
| inrace_model.joblib | `df` from load_inrace_df | features_inrace + features_prerace + raw_sessions 3-table JOIN | Yes — INNER JOIN excludes missing labels; empty guard raises RuntimeError | FLOWING |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 8 tests pass (3 prerace + 5 inrace) | `PYTHONPATH=src python3.11 -m pytest tests/test_train_prerace.py tests/test_train_inrace.py -q` | `8 passed in 1.43s` | PASS |
| `python scripts/train.py --help` exits 0, no selective flags | `python3.11 scripts/train.py --help` | Exit 0; help text shows no --prerace-only / --inrace-only / --force | PASS |
| PRERACE_FEATURE_COLS contains all PRERACE-01 required columns | Import check | qualifying_delta_to_pole, practice_pace_fp2/fp3, circuit_win_rate, circuit_avg_finish, constructor_pace_delta, championship_position all present | PASS |
| INRACE_FEATURE_COLS contains pit_stop_count AND pit_stop_delta (INRACE-03) | Import check | Both confirmed present | PASS |
| FrozenEstimator used, cv='prefit' absent | grep on pipeline.py | FrozenEstimator count: 9; cv='prefit' count: 0 | PASS |
| Temporal split uses year <= 2024 / year == 2025 literals | grep on pipeline.py lines 284–285, 421–422 | Both functions use exact literals | PASS |
| train.py against real DB | Requires populated data/f1.db | Not runnable without Phase 1 data | SKIP |

---

### Probe Execution

Step 7c: SKIPPED — no probe-*.sh files declared in PLAN or present in scripts/tests/.

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| PRERACE-01 | 02-01-PLAN | Pre-race model trained on qualifying gap, practice pace, circuit history, constructor perf, championship standings | SATISFIED | PRERACE_FEATURE_COLS contains all 10 columns; test passes |
| PRERACE-02 | 02-01-PLAN | Win probability distribution sums to 1.0 | SATISFIED | per_session_softmax verified; test_train_prerace_softmax_sums_to_one passes |
| PRERACE-03 | 02-01-PLAN | Pre-race artifact loadable with fitted OrdinalEncoder for Phase 3 inference | SATISFIED | save_artifact stores encoder in dict; test asserts key presence |
| INRACE-01 | 02-02-PLAN | In-race model trained on position, gaps, laps remaining, tyre compound/age, pit history | SATISFIED | INRACE_FEATURE_COLS verified; all 9 required columns present |
| INRACE-02 | 02-02-PLAN | In-race model re-runs per lap with valid probability distribution | SATISFIED | per-(session_key, lap_number) softmax; test_train_inrace_softmax_sums_to_one_per_lap passes |
| INRACE-03 | 02-02-PLAN | In-race model incorporates live pit stop data | SATISFIED | pit_stop_count AND pit_stop_delta both in INRACE_FEATURE_COLS; test_train_inrace_includes_pit_stop_features asserts this |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| src/f1_prediction/ml/pipeline.py | 121 | `model.fit(X_train.values, y_train.values)` — numpy array bypasses XGBoost categorical metadata | WARNING (CR-02 from code review) | `enable_categorical=True` is silently ignored; ordinal-encoded columns treated as numeric float. Model trains without error but categoricals are not handled natively. |
| src/f1_prediction/ml/pipeline.py | 176 | `calibrated.fit(X_test.values, y_test.values)` — same issue | WARNING (CR-02 from code review) | Same as above for calibration step. |
| src/f1_prediction/ml/pipeline.py | 325-331, 464-470 | Calibration data and evaluation data are identical | WARNING (CR-03 from code review) | Reported log_loss and top3_hit_rate values are optimistic — calibration was fitted on the same data that metrics are computed on. Stdout metrics are not reliable performance indicators. |
| src/f1_prediction/ml/pipeline.py | 53-54 | CWD-relative artifact paths | WARNING (CR-02 from code review) | `Path("data/models/prerace_model.joblib")` resolves against CWD. Running from subdirectory writes artifact to wrong location. |
| CLAUDE.md vs pipeline.py | N/A | XGBoost used instead of project-mandated LightGBM | WARNING (CR-01 from code review, accepted by D-01) | D-01 in CONTEXT.md explicitly overrides CLAUDE.md with user preference for XGBoost. Framework deviation is intentional and documented. Not a blocker for goal achievement, but 02-REVIEW.md flags it as a critical deviation to monitor. |

No `TBD`, `FIXME`, or `XXX` debt markers found in any Phase 2 modified file.

---

### Human Verification Required

#### 1. End-to-End Training Run Against Real Data

**Test:** Run `python scripts/train.py` from the project root with a populated `data/f1.db` (produced by Phase 1 backfill).
**Expected:** Exit code 0; two metric lines printed to stdout (`prerace  log_loss=X.XXXX  top3_hit_rate=X.XXX  (N/M races)` and `inrace  log_loss=X.XXXX  top3_hit_rate_final_lap=X.XXX  (N/M races)`); `data/models/prerace_model.joblib` and `data/models/inrace_model.joblib` both written; `joblib.load("data/models/prerace_model.joblib")` returns a dict with keys {model, feature_cols, cat_cols, encoder, trained_on, tested_on}.
**Why human:** Requires a populated database from Phase 1. Synthetic test fixtures confirm the code paths work; a real-data run confirms the data contracts, artifact sizes, and metric values are plausible.

#### 2. Code Review Decision: CR-01, CR-02, CR-03 Before Phase 3

**Test:** Developer reads `02-REVIEW.md` critical issues section and makes an explicit go/no-go decision for each finding before Phase 3 begins importing or loading Phase 2 artifacts.
**Expected:**
- CR-01 (XGBoost vs LightGBM): Either (a) accept as intentional per D-01 and document override, or (b) switch to LightGBM per CLAUDE.md mandate.
- CR-02 (`.values` bypasses `enable_categorical`): Either (a) pass DataFrame directly with CategoricalDtype columns, or (b) document that ordinal encoding provides equivalent signal and accept as-is.
- CR-03 (calibration/evaluation data overlap): Either (a) add a calibration hold-out split, or (b) document that reported metrics are approximate and add a warning log.
**Why human:** These three issues affect model correctness and metric reliability. They were identified by the code review that ran during Phase 2 but no REVIEW-FIX file was created. Phase 3 should not ship inference code against artifacts whose calibration approach and metric reliability are unresolved.

---

### Gaps Summary

No blocking gaps found. All 6 must-have truths are verified. All 9 tests pass. All required artifacts exist and are substantive. All key links are wired.

Two human verification items exist that prevent `status: passed`:

1. **Real-data run** — the tests exercise the full code path with synthetic fixtures; a populated-DB run confirms end-to-end behavior with real F1 data.

2. **Code review decision** — three critical issues in `02-REVIEW.md` (CR-01 framework deviation, CR-02 silent categorical no-op, CR-03 overlapping calibration/evaluation data) were identified but not fixed before Phase 2 was marked complete. Running `/gsd-code-review 2 --fix` is recommended before advancing to Phase 3.

---

_Verified: 2026-06-17T00:00:00Z_
_Verifier: Claude (gsd-verifier)_
