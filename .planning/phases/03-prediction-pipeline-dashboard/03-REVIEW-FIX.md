---
phase: 03-prediction-pipeline-dashboard
fixed_at: 2026-07-09T01:51:37Z
review_path: .planning/phases/03-prediction-pipeline-dashboard/03-REVIEW.md
iteration: 1
findings_in_scope: 6
fixed: 6
skipped: 0
status: all_fixed
---

# Phase 03: Code Review Fix Report

**Fixed at:** 2026-07-09T01:51:37Z
**Source review:** .planning/phases/03-prediction-pipeline-dashboard/03-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 6 (1 Critical, 5 Warning — `fix_scope: critical_warning`, Info findings IN-01/02/03 excluded)
- Fixed: 6
- Skipped: 0

## Fixed Issues

### CR-01: replay_race.py crash-recovery restore can be interrupted, causing the finally block to delete the only backup and permanently lose race data

**Files modified:** `scripts/replay_race.py`
**Commit:** `973e1c3`
**Applied fix:** Replaced the boolean-less `finally: _clear_staging(...)` with a `safe_to_clear_staging` flag that is only set `True` when either (a) the replay loop completes normally, or (b) the restore loop (triggered on interrupt) has attempted every lap in `lap_numbers`. Changed the restore loop's per-lap exception handler from `except Exception` to `except BaseException` so a second `KeyboardInterrupt`/`SystemExit` during the restore itself cannot abort the loop early. The staging backup is now only deleted once it is verified safe; otherwise an error is logged instructing the user to re-run replay_race or manually inspect the staging session.

### WR-01: `predictions` table has no DB-level uniqueness constraint — duplicate rows possible

**Files modified:** `src/f1_prediction/db/models.py`
**Commit:** `25217d4`
**Applied fix:** Added `UniqueConstraint("session_key", "driver_number", "prediction_type", "lap_number", name="uq_predictions_session_driver_type_lap")` to `Prediction.__table_args__`, exactly as suggested, alongside the existing `CheckConstraint` and `Index`. Verified schema creation and existing prerace/inrace pipeline tests still pass with the new constraint in place.

### WR-02: `compute_and_store_inrace_latest_lap` deletes and inserts in separate transactions — dashboard can observe a stale/empty lap mid-run

**Files modified:** `src/f1_prediction/inference/inrace.py`
**Commit:** `1323b28`
**Applied fix:** Replaced the two-connection delete-then-`bulk_insert_rows` sequence with a single `engine.begin()` transaction that runs the `DELETE` and the `to_sql(..., if_exists="append")` insert on the same connection, so the swap is atomic and dashboard readers never observe an empty/stale window. Removed the now-unused `bulk_insert_rows` import from this module (its own separate-connection insert path is no longer used here).

### WR-03: Dashboard query layer imports `pipeline.py`, coupling the read-only dashboard process to APScheduler/live-poller/inference modules

**Files modified:** `src/f1_prediction/db/queries.py` (new), `src/f1_prediction/pipeline.py`, `src/f1_prediction/dashboard_lib/queries.py`
**Commit:** `34bac88`
**Applied fix:** Extracted `get_latest_race_session_key` (a pure read query, no scheduler/inference dependency) into a new shared module `src/f1_prediction/db/queries.py`. `pipeline.py` now imports it from there (re-exported via its existing `__all__` for backward compatibility — nothing else in the codebase needed to change). `dashboard_lib/queries.py` now imports directly from `db/queries.py` instead of `pipeline.py`. Verified by import-time inspection that `f1_prediction.dashboard_lib.queries` no longer pulls in `apscheduler`, `f1_prediction.pipeline`, or `f1_prediction.live.poller`.

### WR-04: No test coverage for replay_race.py's in-place staging/crash-recovery path (the mechanism behind CR-01)

**Files modified:** `tests/test_replay_race.py`
**Commit:** `05755a3`
**Applied fix:** Added two new tests exercising the in-place staging/restore path directly against `_stage_source_snapshot`, `_reset_target_for_inplace_replay`, `_clear_staging`, and the fixed restore-loop semantics: (1) `test_inplace_replay_restores_all_laps_after_second_interrupt` seeds a source session, stages + clears it for in-place replay, injects a simulated second `KeyboardInterrupt` on one lap's restore call, and asserts the restore loop still attempts every remaining lap (not aborted early) and that staging is only cleared once that full attempt completes; (2) `test_inplace_replay_keeps_staging_if_restore_incomplete` asserts the staging backup remains fully intact when the restore loop does not run to completion. Both tests pass against the CR-01-fixed code.

### WR-05: pipeline.py's shutdown handling doesn't cover SIGTERM

**Files modified:** `src/f1_prediction/pipeline.py`
**Commit:** `2df699f`
**Applied fix:** Added `import signal`, a `_handle_sigterm(signum, frame)` handler that raises `SystemExit(0)`, and registered it via `signal.signal(signal.SIGTERM, _handle_sigterm)` at the top of `main()`, before the scheduler starts. Verified in isolation (direct call raises `SystemExit(0)` as expected) and via a subprocess integration test sending a real `SIGTERM` after imports settle — the process exits cleanly (code 0) instead of being killed by the OS default SIGTERM behavior.

## Verification Notes

- All 6 fixes passed Tier 1 (re-read modified sections, confirmed intact) and Tier 2 (Python `ast.parse` syntax check on every modified file) verification.
- Ran the full test suite after each fix: `tests/test_pipeline_prerace.py`, `tests/test_pipeline_inrace.py`, and `tests/test_replay_race.py` all pass (including the 2 new WR-04 tests). Full suite run: 62 passed (excluding `tests/test_backfill_extended.py`, which has 6 pre-existing failures — `no such table: features_prerace` — confirmed unrelated to this work via `git stash` comparison against the pre-fix tree).
- None of these findings involve a disputed/ambiguous logic judgment call requiring human sign-off beyond normal code review — all 6 are marked `fixed` (not `fixed: requires human verification`).

---

_Fixed: 2026-07-09T01:51:37Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
