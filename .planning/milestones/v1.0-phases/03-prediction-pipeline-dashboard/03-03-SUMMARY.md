---
phase: 03-prediction-pipeline-dashboard
plan: 03
subsystem: live-inference-dashboard
tags: [xgboost, streamlit, apscheduler, sqlalchemy, sqlite-wal, softmax]

requires:
  - phase: 03-prediction-pipeline-dashboard
    provides: "Plan 01's inference/features.py (INRACE_NUMERIC_FEATURE_COLS, load_model, predict_inrace_with_softmax), Plan 02's pipeline.py entrypoint + dashboard_lib/ query/chart layer, Phase 1's live/poller.py (poll_once, BackgroundScheduler pattern), features/builder.py::build_features_for_session(kind='inrace')"
provides:
  - "The phase's headline live slice: pipeline.py's poll loop now runs inrace inference after every poll cycle that inserts new laps, writing softmax-normalized per-lap win probabilities into predictions"
  - "dashboard.py's In-Race tab auto-refreshes every 15 seconds via st.fragment, showing a LIVE badge, freshness caption, team-colored bar chart, and a position/tyre/gap side table"
  - "dashboard_lib/queries.py::load_inrace_predictions/get_latest_inrace_lap — the read layer Plan 04's replay checkpoint will exercise against a simulated live race"
affects: [03-04-replay-script]

tech-stack:
  added: []
  patterns:
    - "Rebuild-then-slice (Pattern 1): compute_and_store_inrace_latest_lap always calls build_features_for_session(kind='inrace') for a full-session rebuild, then slices to MAX(lap_number) — never hand-rolls an incremental per-lap join (avoids re-introducing the 01-REVIEW-FIX CR-03 bug class)"
    - "Single BackgroundScheduler in pipeline.py's main() (not a second scheduler) runs poll_once() then run_inference_after_poll() in the same job, reusing live/poller.py's max_instances=1/misfire_grace_time=15/EVENT_JOB_ERROR conventions"
    - "st.fragment(run_every=15) scoped only to render_inrace_tab; the Pre-Race tab stays un-decorated and static (Pattern 3); every dashboard_lib query opens a fresh connection per call (Pattern 4), never holding one across fragment reruns"
    - "DELETE-then-INSERT per (session_key, lap_number) in compute_and_store_inrace_latest_lap keeps re-runs of the same lap idempotent (T-03-07)"

key-files:
  created:
    - src/f1_prediction/inference/inrace.py
  modified:
    - tests/test_pipeline_inrace.py
    - src/f1_prediction/pipeline.py
    - src/f1_prediction/dashboard.py
    - src/f1_prediction/dashboard_lib/queries.py

key-decisions:
  - "Committed a pre-existing uncommitted tests/test_pipeline_inrace.py (286 lines) left behind by a prior interrupted attempt, after independently verifying it against 03-03-PLAN.md's Task 1 spec (behavior, fixture shape, RED-confirmation) — it matched exactly and was used as-is rather than rewritten"
  - "INRACE_NUMERIC_FEATURE_COLS already includes lap_number as one of its 9 columns — the inrace.py column reindex selects it only once (not once explicitly plus once via the constant list), since selecting a duplicate-named column twice silently produces a 15-wide (not 14-wide) feature matrix and an XGBoost 'Feature shape mismatch' error"
  - "pipeline.py imports compute_and_store_inrace_latest_lap at module level (not inside run_inference_after_poll) so monkeypatch.setattr(pipeline_module, 'compute_and_store_inrace_latest_lap', ...) in tests replaces the exact name resolved at call time"
  - "Reworded a new dashboard.py docstring away from the literal phrase 'refresh now' (mirroring Plan 02's '0.0.0.0' docstring lesson) after the plan's own grep-based D-08 verification gate flagged the phrase in a comment, not actual code"

patterns-established:
  - "inference/inrace.py mirrors inference/prerace.py's structure exactly (WR-02 two-connection split, reuse of predict_*_with_softmax verbatim) but adds a per-lap idempotent DELETE-then-INSERT step, since inrace predictions are re-computed every poll cycle rather than once per session"

requirements-completed: [DASH-01, DASH-03]

coverage:
  - id: D1
    description: "compute_and_store_inrace_latest_lap rebuilds in-race features via build_features_for_session(kind='inrace') (Pattern 1), slices to MAX(lap_number), and inserts one 'inrace' row per driver with win_probability summing to ~1.0 per (session_key, lap_number)"
    requirement: DASH-01
    verification:
      - kind: unit
        ref: "tests/test_pipeline_inrace.py::test_compute_and_store_inrace_latest_lap_sums_to_one"
        status: pass
    human_judgment: false
  - id: D2
    description: "pipeline.run_inference_after_poll triggers inrace inference only when poll_result['laps_inserted'] > 0 (D-09); a laps_inserted==0 result is a no-op"
    requirement: DASH-03
    verification:
      - kind: unit
        ref: "tests/test_pipeline_inrace.py::test_run_inference_after_poll_gates_on_laps_inserted"
        status: pass
    human_judgment: false
  - id: D3
    description: "dashboard.py's In-Race tab auto-refreshes every 15s via @st.fragment(run_every=15) with no manual refresh control (D-07/D-08)"
    requirement: DASH-03
    verification:
      - kind: unit
        ref: "plan verify command: 'run_every=15' and 'fragment' present in dashboard.py source; 'refresh now' absent (this session)"
        status: pass
      - kind: manual_procedural
        ref: "streamlit run src/f1_prediction/dashboard.py — visual confirmation of live lap-by-lap updates during a replay is explicitly deferred to Plan 04's checkpoint per this plan's own acceptance criteria"
        status: unknown
    human_judgment: true
    rationale: "Visual confirmation that probabilities update lap-by-lap without a page reload requires a running replay/live session, which is Plan 04's scope. This session verified the source-level fragment/gate contract and the underlying query logic via an ad hoc smoke script (seeded predictions + features_inrace rows, confirmed load_inrace_predictions and get_latest_inrace_lap return correct joined data), not a live browser session."
  - id: D4
    description: "load_inrace_predictions joins features_inrace for position/tyre_compound/tyre_age_laps/gap_to_leader (D-04) and slices to the latest inrace lap"
    requirement: DASH-01
    verification:
      - kind: unit
        ref: "plan verify command: 'load_inrace_predictions' and 'features_inrace' present in dashboard_lib/queries.py source; ad hoc smoke script (this session) confirms correct joined output against seeded predictions/features_inrace/raw_drivers rows"
        status: pass
    human_judgment: false

duration: ~10min (this session; test file itself was written by a prior interrupted attempt and verified rather than authored fresh)
completed: 2026-07-08
status: complete
---

# Phase 3 Plan 3: Live In-Race Inference & Dashboard Auto-Refresh Summary

**After each poll cycle that ingests new laps, `pipeline.py` rebuilds in-race features (rebuild-then-slice, Pattern 1), runs the calibrated XGBoost in-race model with per-lap softmax normalization, and writes the result to SQLite; `dashboard.py`'s In-Race tab reads it back every 15 seconds via `st.fragment`, rendering a LIVE badge, freshness caption, team-colored bar chart, and a position/tyre/gap side table — no manual refresh, no page reload.**

## Performance

- **Duration:** ~10 min (this execution session)
- **Completed:** 2026-07-08
- **Tasks:** 3/3 completed
- **Files modified:** 4 (1 created, 3 modified)

## Context Note

A previous attempt at this plan was interrupted mid-task by an account session/usage limit before it committed anything, leaving an uncommitted, untracked `tests/test_pipeline_inrace.py` (286 lines). This session independently verified that file against `03-03-PLAN.md`'s Task 1 spec (fixture design, behavior assertions, RED-confirmation requirement) before trusting it — it matched exactly (rebuild-then-slice via raw_* seeding for the target session, tiny trained model via `train_inrace_model(cv=2)`, sum-to-1 assertion, hook-gating test), was confirmed to genuinely fail RED (`ModuleNotFoundError`/`AttributeError`), and was committed as-is rather than rewritten.

## Accomplishments
- Delivered the phase's headline live capability (DASH-01/DASH-03, ROADMAP criteria 1/2/4): a user watching a race (or Plan 04's replay) now sees win probabilities shift lap-by-lap without touching the page.
- `inference/inrace.py::compute_and_store_inrace_latest_lap` rebuilds in-race features via `build_features_for_session(kind="inrace")` (Pattern 1 — no hand-rolled incremental per-lap join, avoiding the class of bug fixed in 01-REVIEW-FIX CR-03), slices to `MAX(lap_number)`, reuses `predict_inrace_with_softmax` verbatim, and DELETE-then-INSERTs per (session_key, lap_number) for idempotent re-runs (T-03-07).
- `pipeline.py::run_inference_after_poll` gates inference on `poll_result["laps_inserted"] > 0` (D-09); `main()` now starts a single `BackgroundScheduler` (not a second one) that calls `poll_once()` then `run_inference_after_poll()` in the same 30s job, reusing `live/poller.py`'s `max_instances=1`/`misfire_grace_time=15`/`EVENT_JOB_ERROR` conventions.
- `dashboard_lib/queries.py::load_inrace_predictions` LEFT JOINs `raw_drivers` (same fallback convention as the pre-race query) and JOINs `features_inrace` for `position`/`tyre_compound`/`tyre_age_laps`/`gap_to_leader` (D-04), sliced to the latest inrace lap; `get_latest_inrace_lap` supports the freshness caption.
- `dashboard.py::render_inrace_tab` is now `@st.fragment(run_every=15)` (D-07): renders a plain-text `LIVE` badge (accent `#E10600`, not emoji), an `"LIVE · Lap N · updated HH:MM:SS"` caption, the reused team-colored bar chart, and an `st.dataframe` side table — no manual refresh control anywhere (D-08); the Pre-Race tab remains un-decorated and static (Pattern 3).

## Task Commits

Each task was committed atomically:

1. **Task 1: RED — failing end-to-end test for the post-poll in-race inference hook** - `cb001bb` (test)
2. **Task 2: GREEN — in-race inference (rebuild-then-slice, Pattern 1) wired into the poll loop** - `46e6770` (feat)
3. **Task 3: Dashboard In-Race tab — 15s fragment auto-refresh, live bar chart + side table** - `de7a867` (feat)

**Plan metadata:** this SUMMARY.md and STATE.md/ROADMAP.md updates committed separately as the final docs commit.

_Note: Task 1/2 form a RED/GREEN TDD pair (`tdd="true"`); no REFACTOR commit was needed._

## Files Created/Modified
- `tests/test_pipeline_inrace.py` - Seeds 3 historical Race sessions (features_prerace + features_inrace) to train a tiny inrace model, plus a target "latest race" session at lap 3 via raw_laps/raw_positions/raw_stints only (Pattern 1 input); asserts `compute_and_store_inrace_latest_lap`'s sum-to-1 behavior and `run_inference_after_poll`'s laps_inserted gating
- `src/f1_prediction/inference/inrace.py` - `compute_and_store_inrace_latest_lap(session_key, model_path, *, engine=None)` — full rebuild via `build_features_for_session`, slice to MAX(lap_number), reuse `predict_inrace_with_softmax`, per-lap idempotent DELETE-then-INSERT
- `src/f1_prediction/pipeline.py` - Added `run_inference_after_poll(engine, session_key, poll_result)`; `main()` now starts a `BackgroundScheduler` running `poll_once()` + `run_inference_after_poll()` every 30s
- `src/f1_prediction/dashboard.py` - `render_inrace_tab(session_key)` replaced with an `@st.fragment(run_every=15)`-decorated render: LIVE badge, freshness caption, bar chart, side table
- `src/f1_prediction/dashboard_lib/queries.py` - Added `load_inrace_predictions(session_key)` and `get_latest_inrace_lap(session_key)`

## Decisions Made
- Verified and reused the prior interrupted attempt's `tests/test_pipeline_inrace.py` rather than rewriting it, after confirming it matched the plan's Task 1 spec and genuinely failed RED before implementation.
- Selected `lap_number` only once when reindexing the in-race feature DataFrame (it's already one of `INRACE_NUMERIC_FEATURE_COLS`'s 9 columns) — an early implementation attempt double-selected it (once explicitly, once via the constant list), producing a 15-wide feature matrix and an XGBoost `Feature shape mismatch: expected 14, got 15` error; fixed before the GREEN commit.
- Imported `compute_and_store_inrace_latest_lap` at `pipeline.py` module level so `monkeypatch.setattr(pipeline_module, "compute_and_store_inrace_latest_lap", ...)` correctly intercepts the call inside `run_inference_after_poll`.
- Reworded a dashboard.py docstring to avoid the literal phrase "refresh now" after the plan's own grep-based D-08 gate flagged it in a comment (same false-positive pattern documented in Plan 02's SUMMARY for the `0.0.0.0` string) — no behavior changed.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Duplicate `lap_number` column in the in-race feature reindex caused an XGBoost shape mismatch**
- **Found during:** Task 2 (GREEN implementation), first test run
- **Issue:** The initial reindex explicitly listed `"lap_number"` alongside `*INRACE_NUMERIC_FEATURE_COLS`, but `lap_number` is already one of the 9 columns in that constant. Selecting a duplicate-named column via `df[[...]]` returns both, producing a 15-column DataFrame; `build_inrace_feature_matrix` then built a 15-wide (not 14-wide) matrix, and `model.predict_proba` raised `ValueError: Feature shape mismatch, expected: 14, got 15`.
- **Fix:** Removed the redundant explicit `"lap_number"` entry from the reindex list, relying solely on its presence within `INRACE_NUMERIC_FEATURE_COLS`.
- **Files modified:** `src/f1_prediction/inference/inrace.py`
- **Verification:** Re-ran `pytest tests/test_pipeline_inrace.py` — both tests pass; full suite (64 passed) unaffected.
- **Committed in:** `46e6770` (Task 2 commit)

**2. [Rule 1 - Bug] Docstring literal "refresh now" tripped the plan's own D-08 grep gate**
- **Found during:** Task 3, while running the plan's verification command
- **Issue:** `render_inrace_tab`'s new docstring described the D-08 contract using the phrase `no manual "refresh now" control`. The plan's verification command does a literal substring check (`assert 'refresh now' not in low`) against the whole file, matching the docstring text itself rather than actual UI code.
- **Fix:** Reworded the docstring to `No manual refresh control of any kind is rendered (D-08)` — same contract, no literal match.
- **Files modified:** `src/f1_prediction/dashboard.py`
- **Verification:** Re-ran the plan's exact verify command — passes; full suite unaffected.
- **Committed in:** `de7a867` (Task 3 commit)

---

**Total deviations:** 2 auto-fixed (2 bug fixes, Rule 1)
**Impact on plan:** No scope creep — one fix corrects a real feature-matrix-width bug caught by the plan's own test; the other is a wording-only fix to satisfy the plan's own literal-string safety gate.

## Issues Encountered
None beyond the two auto-fixed items above. The inherited RED test failed exactly as expected (`ModuleNotFoundError` for `inference.inrace`, `AttributeError` for `pipeline.run_inference_after_poll`) before Task 2's implementation made it pass. Full test suite (64 tests) passes after all three tasks.

## User Setup Required
None — no external service configuration required. `python -m src.f1_prediction.pipeline` and `streamlit run src/f1_prediction/dashboard.py` continue to run against the existing local SQLite DB.

## Known Stubs
None. All rendered dashboard fields (LIVE badge, freshness caption, bar chart, side table) are wired to real query results from `load_inrace_predictions`; no hardcoded/placeholder data paths were introduced.

## Threat Flags
None. All new surface (the in-race inference hook, the dashboard In-Race tab, and the new queries) was already anticipated and dispositioned in this plan's own `<threat_model>` (T-03-05, T-03-06, T-03-07, T-03-08) — no new trust boundaries were introduced beyond what the plan accounted for.

## Next Phase Readiness
- `pipeline.py`'s poll loop is now fully wired end-to-end: poll → rebuild-then-slice inference → predictions table, ready for Plan 04's replay script to drive by writing into `raw_*` tables on a timer.
- `dashboard.py`'s In-Race tab is feature-complete per this plan's acceptance criteria; the one remaining verification item (visual confirmation of lap-by-lap updates without a page reload) is explicitly deferred to Plan 04's checkpoint, matching this plan's own acceptance criteria language.
- No blockers for Plan 04.

---
*Phase: 03-prediction-pipeline-dashboard*
*Completed: 2026-07-08*

## Self-Check: PASSED

All 4 created/modified files confirmed present on disk. All 3 task commit hashes (cb001bb, 46e6770, de7a867) confirmed in git log.
