---
phase: 03-prediction-pipeline-dashboard
fixed_at: 2026-07-09T03:59:23Z
review_path: .planning/phases/03-prediction-pipeline-dashboard/03-REVIEW.md
iteration: 1
findings_in_scope: 2
fixed: 2
skipped: 0
status: all_fixed
---

# Phase 03: Code Review Fix Report

**Fixed at:** 2026-07-09T03:59:23Z
**Source review:** .planning/phases/03-prediction-pipeline-dashboard/03-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 2 (Warning-tier; `fix_scope: critical_warning` excludes Info findings IN-01/02/03)
- Fixed: 2
- Skipped: 0

This is a re-review pass. The prior 03-REVIEW.md's 6 findings (CR-01, WR-01 through WR-05) were already fixed and are documented in git history (commits `973e1c3`, `25217d4`, `1323b28`, `34bac88`, `05755a3`, `2df699f`). This report covers only the 2 new findings surfaced by the follow-up re-review.

## Fixed Issues

### WR-01: `compute_and_store_prerace` is not safe to call twice — will raise IntegrityError under the new UniqueConstraint

**Files modified:** `src/f1_prediction/inference/prerace.py`
**Commit:** `81205c0`
**Applied fix:** Replaced the plain `bulk_insert_rows(engine, "predictions", rows)` append with an atomic delete-then-insert inside a single `engine.begin()` transaction — `DELETE FROM predictions WHERE session_key = :sk AND prediction_type = 'prerace'` followed by `rows_df.to_sql(..., if_exists="append", ...)`. This mirrors `compute_and_store_inrace_latest_lap`'s existing pattern exactly, so `compute_and_store_prerace` is now safe to call more than once for the same session (no longer relies solely on the external `prerace_already_computed` guard in `pipeline.py`). Also removed the now-unused `bulk_insert_rows` import from `prerace.py` (it remains imported/used unchanged in every other call site across the codebase). Verified with `pytest tests/test_pipeline_prerace.py tests/test_pipeline_inrace.py tests/test_replay_race.py` (9 passed).

### WR-02: `replay_race.py` docstring claims "no f-string SQL" but several queries interpolate column-list constants via f-string

**Files modified:** `scripts/replay_race.py`
**Commit:** `5292bec`
**Applied fix:** Rewrote the module docstring's security claim to be precise: values are always passed via bound `:param` names and never f-string-interpolated, while the SELECT column-list constants (`_LAP_COLS`/`_POSITION_COLS`/`_STINT_COLS`/`_PIT_COLS`) are f-string-interpolated but are hardcoded module constants, never derived from CLI args or external input. No behavioral/code change — documentation-only fix. Kept the shared-constant pattern rather than duplicating each SELECT literally, since the review offered de-duplication removal only as a secondary alternative, not the primary recommended fix.

## Skipped Issues

None — both in-scope findings (WR-01, WR-02) were fixed. Info findings (IN-01, IN-02, IN-03) were excluded per `fix_scope: critical_warning` and remain open in the source REVIEW.md for future triage:
- IN-01: degenerate single-driver softmax distribution not logged (`src/f1_prediction/inference/features.py`)
- IN-02: bare `assert` for sum-to-1 validation in `scripts/train.py` (stripped under `python -O`)
- IN-03: inconsistent `engine.connect()`/`engine.begin()` style in `scripts/replay_race.py`

## Verification Notes

- Both fixes passed Tier 1 (re-read modified sections, confirmed intact) and Tier 2 (Python `ast.parse` syntax check on every modified file) verification.
- Ran `tests/test_pipeline_prerace.py`, `tests/test_pipeline_inrace.py`, and `tests/test_replay_race.py` after applying the fixes: 9 passed, no regressions.
- Neither finding involves a disputed/ambiguous logic judgment call requiring extra human sign-off beyond normal review — both are marked `fixed` (not `fixed: requires human verification`).

---

_Fixed: 2026-07-09T03:59:23Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
