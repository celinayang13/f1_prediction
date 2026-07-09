---
phase: 03-prediction-pipeline-dashboard
reviewed: 2026-07-08T00:00:00Z
depth: standard
files_reviewed: 17
files_reviewed_list:
  - .streamlit/config.toml
  - scripts/replay_race.py
  - scripts/train.py
  - src/f1_prediction/dashboard_lib/__init__.py
  - src/f1_prediction/dashboard_lib/charts.py
  - src/f1_prediction/dashboard_lib/queries.py
  - src/f1_prediction/dashboard.py
  - src/f1_prediction/db/models.py
  - src/f1_prediction/db/queries.py
  - src/f1_prediction/inference/__init__.py
  - src/f1_prediction/inference/features.py
  - src/f1_prediction/inference/inrace.py
  - src/f1_prediction/inference/prerace.py
  - src/f1_prediction/ingest/openf1_client.py
  - src/f1_prediction/pipeline.py
  - tests/test_pipeline_inrace.py
  - tests/test_pipeline_prerace.py
  - tests/test_replay_race.py
findings:
  critical: 0
  warning: 2
  info: 3
  total: 5
status: issues_found
---

# Phase 03: Code Review Report

**Reviewed:** 2026-07-08T00:00:00Z
**Depth:** standard
**Files Reviewed:** 17
**Status:** issues_found

## Summary

This is a re-review after the prior 03-REVIEW.md's 6 findings (CR-01, WR-01 through WR-05) were fixed. Extra attention was paid to the two files that changed out-of-band since that fix pass: `scripts/replay_race.py` (new `_handle_sigterm` handler) and `src/f1_prediction/db/queries.py` (new module extracted for WR-03).

Both of those changes are sound: `_handle_sigterm` correctly converts SIGTERM into `SystemExit`, is covered by a direct unit test (`test_handle_sigterm_raises_systemexit`), and correctly reuses the existing `except BaseException` restore path — this mirrors `pipeline.py`'s own WR-05 fix exactly. `db/queries.py` is a clean, minimal extraction with no scheduler/inference dependencies, satisfying the WR-03 decoupling goal; `dashboard_lib/queries.py` and `pipeline.py` both import from it correctly and no circular import was introduced.

Across the full file set, no critical (security, crash, or data-loss) issues were found on this pass. The most notable finding is a real robustness gap introduced by the WR-01 fix itself: adding a DB-level `UniqueConstraint` on `predictions` means `compute_and_store_prerace` — which was never updated to delete-before-insert the way `compute_and_store_inrace_latest_lap` was — will now raise `IntegrityError` on any second direct call for the same session, instead of silently duplicating rows (the previous, more benign failure mode). The pipeline's own call site is guarded against this via `prerace_already_computed`, but the function itself is not safe to call twice, unlike its in-race counterpart, and no test exercises that path.

A second finding covers a minor doc/code mismatch in `replay_race.py` (the module docstring claims "no f-string SQL" but several queries interpolate whitelisted column-name constants via f-string — not a security issue since the values are hardcoded module constants, not user input, but worth tightening for consistency with the T-03-05 policy statement).

## Warnings

### WR-01: `compute_and_store_prerace` is not safe to call twice — will raise IntegrityError under the new UniqueConstraint

**File:** `src/f1_prediction/inference/prerace.py:104`
**Issue:** `compute_and_store_prerace` inserts prediction rows via `bulk_insert_rows(engine, "predictions", rows)` — a plain append, with no delete-then-insert step. This was previously harmless (a second call would just create duplicate rows), but the prior review's WR-01 fix added `UniqueConstraint("session_key", "driver_number", "prediction_type", "lap_number")` to the `predictions` table (`src/f1_prediction/db/models.py:356-362`). Any second direct call to `compute_and_store_prerace` for a session that already has prerace rows will now raise `sqlalchemy.exc.IntegrityError` and crash the caller, instead of the previous (also-wrong, but non-crashing) duplicate-row behavior.

`pipeline.main()` happens to be safe today because it gates the call behind `prerace_already_computed()` (`src/f1_prediction/pipeline.py:190-196`), but:
- This is the only call site that has that guard. Any other caller (retry logic, a manual re-run script, a future replay/backfill helper, or two pipeline instances racing on startup) that calls `compute_and_store_prerace` directly without first checking `prerace_already_computed` will crash.
- `compute_and_store_inrace_latest_lap` (the sibling function) already solves this correctly with an atomic DELETE-then-INSERT in the same transaction (`src/f1_prediction/inference/inrace.py:144-161`) specifically for idempotent re-runs — `compute_and_store_prerace` should follow the same pattern for consistency and defense-in-depth, rather than relying entirely on an external caller remembering to check first.
- No test exercises `compute_and_store_prerace` called twice back-to-back without the `prerace_already_computed` guard in between (`tests/test_pipeline_prerace.py:220-223` always re-checks the guard before the second call), so this regression is not caught by the current suite.

**Fix:**
```python
# src/f1_prediction/inference/prerace.py
from sqlalchemy import text
...
    computed_at = datetime.now(timezone.utc).isoformat()
    rows = [...]

    rows_df = pd.DataFrame(rows)
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM predictions "
                "WHERE session_key = :sk AND prediction_type = 'prerace'"
            ),
            {"sk": session_key},
        )
        rows_df.to_sql(
            "predictions", conn, if_exists="append", index=False,
            method="multi", chunksize=500,
        )
    inserted = len(rows)
```
This mirrors `compute_and_store_inrace_latest_lap`'s existing atomic delete+insert pattern and makes `compute_and_store_prerace` safe to call more than once for the same session, matching its sibling function's contract.

---

### WR-02: `replay_race.py` docstring claims "no f-string SQL" but several queries interpolate column-list constants via f-string

**File:** `scripts/replay_race.py:58-60, 194, 198, 202, 206, 304, 369`
**Issue:** The module docstring states: "Security: no f-string SQL — all SQL uses text() with bound :param names (T-03-05)." However, six queries build their SELECT column list via an f-string:
```python
text(f"SELECT {_LAP_COLS} FROM raw_laps WHERE session_key = :sk")
```
`_LAP_COLS`, `_POSITION_COLS`, `_STINT_COLS`, and `_PIT_COLS` are hardcoded module-level string constants (not derived from any request/CLI/user input), so this is not an actual SQL-injection vector — the values are never attacker-controlled. However, the docstring's blanket claim is inaccurate as written, and the pattern (f-string interpolation into `text()`) is exactly the shape that a future edit could turn into a real injection if one of these "constants" were ever parameterized from a CLI flag or config value. The WHERE-clause parameters are correctly bound via `:sk`/`:ln` throughout — only the column list itself is f-string-interpolated.

**Fix:** Tighten the docstring to be precise about what "no f-string SQL" means here, e.g.:
```
Security: all *values* are passed via bound :param names — no value is ever
f-string-interpolated into SQL (T-03-05). Column-list constants
(_LAP_COLS/_POSITION_COLS/_STINT_COLS/_PIT_COLS) are f-string-interpolated
into SELECT clauses, but these are hardcoded module constants, never
derived from CLI args or external input.
```
Alternatively, avoid the pattern entirely by writing out each SELECT column list literally instead of interpolating a shared constant, at the cost of some duplication.

## Info

### IN-01: `predict_with_softmax` / `predict_inrace_with_softmax` silently produce a degenerate distribution if a group has exactly one row

**File:** `src/f1_prediction/inference/features.py:122-124, 154-156`
**Issue:** `softmax([x])` for a single-element array always returns `[1.0]` regardless of the raw score. If `features_prerace` (or `features_inrace` for a given lap) ever contains only one driver row for a session — e.g., a partially-ingested session, or an in-race rebuild racing with a partial poll — that lone driver is assigned win_probability = 1.0 with no signal from the model at all. This is mathematically correct softmax behavior and not a bug in the function, but it can produce a misleading dashboard display (100% for one driver) with no defensive logging to indicate the group was degenerate. Not observed to occur in current data flows (the callers pull full driver sets), but worth a debug-level log when a group size is 1, to make this state debuggable if the dashboard ever shows a suspicious 100% bar.
**Fix:** Optionally add a length check + `logger.debug` inside the `transform` lambda or immediately after, e.g. `if len(x) == 1: logger.debug(...)`. Low priority — informational only.

### IN-02: `_train_prerace` / `_train_inrace` use bare `assert` for a production data-integrity check

**File:** `scripts/train.py:451-454, 511-514`
**Issue:** The per-race/per-lap sum-to-1 validation is implemented as a bare `assert` statement. Since Python strips `assert` statements when run with `-O` (optimized mode), this validation would silently vanish if the script were ever invoked with `python -O scripts/train.py ...` — a training run could complete and serialize a broken model artifact with no indication anything went wrong. This is a CLI/offline training script (not a hot path), so the risk is low, but it's inconsistent with `ValueError` used elsewhere in this same file (`train_prerace_model`/`train_inrace_model` correctly raise `ValueError` for the "no winner rows" check).
**Fix:**
```python
if not ((sums - 1.0).abs() < 0.001).all():
    bad = sums[(sums - 1.0).abs() >= 0.001].to_dict()
    raise ValueError(f"Per-race sum-to-1 assertion failed! Violating sessions: {bad}")
```

### IN-03: Inconsistent SQLAlchemy connection commit style within `scripts/replay_race.py`

**File:** `scripts/replay_race.py:210-215` vs `232-236, 264-274`
**Issue:** `_stage_source_snapshot` opens a plain `engine.connect()` context and calls `conn.commit()` explicitly at the end of its DELETE block (line 215), while `_clear_staging` and `_reset_target_for_inplace_replay` use `engine.begin()` (auto-commit-on-success context manager) for equivalent DELETE operations. Both approaches are individually correct, but the inconsistency makes the file harder to audit for "did this write get committed" at a glance, especially in a file whose correctness depends heavily on precise transactional boundaries (staging/restore crash-safety is the whole point of this script).
**Fix:** Standardize on `engine.begin()` throughout for write blocks, reserving `engine.connect()` for read-only blocks, matching the convention already used by `_clear_staging`/`_reset_target_for_inplace_replay`.

---

_Reviewed: 2026-07-08T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
