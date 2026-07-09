---
phase: 03-prediction-pipeline-dashboard
plan: 02
subsystem: ml-inference
tags: [xgboost, streamlit, plotly, sqlalchemy, sqlite-wal, softmax]

requires:
  - phase: 03-prediction-pipeline-dashboard
    provides: "Plan 01's inference/features.py (PRERACE_FEATURE_COLS, load_model, predict_with_softmax), Prediction/RawDriver ORM models, OpenF1Client.drivers()/fetch_drivers(), working Streamlit 1.59.0 install"
provides:
  - "First end-to-end user-visible slice: python -m src.f1_prediction.pipeline computes and stores pre-race win probabilities; streamlit run src/f1_prediction/dashboard.py displays them as a ranked, team-colored bar chart at localhost:8501"
  - "pipeline.py entrypoint with get_latest_race_session_key (D-01), prerace_already_computed (D-11), sync_drivers_for_session (D-06) — reusable by Plan 03's in-race poll loop"
  - "dashboard_lib/queries.py and charts.py — the read-only query + chart-building layer Plan 03's in-race tab will extend"
affects: [03-03-inrace-dashboard, 03-04-replay-script]

tech-stack:
  added: []
  patterns:
    - "WR-02 two-connection split (read features in one connection, close it, open a fresh connection for the bulk insert) extended from features/builder.py into inference/prerace.py"
    - "Streamlit read layer: st.cache_resource-cached Engine (connection pool only) + fresh engine.connect() per query call (Pattern 4) — never hold a Connection across reruns"
    - "LEFT JOIN + Python-side fillna fallback (driver_number as label, neutral gray #888888) when raw_drivers has no matching row for a driver — dashboard degrades gracefully instead of hiding/erroring on missing OpenF1 drivers data"

key-files:
  created:
    - tests/test_pipeline_prerace.py
    - src/f1_prediction/inference/prerace.py
    - src/f1_prediction/pipeline.py
    - src/f1_prediction/dashboard.py
    - src/f1_prediction/dashboard_lib/__init__.py
    - src/f1_prediction/dashboard_lib/queries.py
    - src/f1_prediction/dashboard_lib/charts.py
  modified: []

key-decisions:
  - "dashboard_lib/queries.py imports pipeline.get_latest_race_session_key rather than duplicating the D-01 query — one source of truth for 'latest race' resolution shared by both processes"
  - "Dropped the literal string '0.0.0.0' from dashboard.py's own docstring (rephrased to avoid it) after discovering the plan's own grep-based verification check (T-03-02 gate) matches any occurrence of that string, including in comments — not just an actual --server.address flag"
  - "Bar chart sorts ascending by win_probability before building the Plotly trace, because Plotly renders horizontal bar categories bottom-to-top by default — ascending sort is what puts the highest-probability driver visually on top"

patterns-established:
  - "inference/<kind>.py modules (prerace.py, and a future inrace.py) load a joblib model, read features via one connection, reuse inference/features.py's predict_*_with_softmax verbatim, then write predictions via a second fresh connection (WR-02) — do not reimplement softmax or reorder feature columns"

requirements-completed: [DASH-02, DASH-04]

coverage:
  - id: D1
    description: "compute_and_store_prerace inserts one 'prerace' row per driver (lap_number NULL) with win_probability summing to ~1.0 per session (D-09, PRERACE-02)"
    requirement: DASH-02
    verification:
      - kind: unit
        ref: "tests/test_pipeline_prerace.py::test_compute_and_store_prerace_sums_to_one"
        status: pass
    human_judgment: false
  - id: D2
    description: "pipeline.prerace_already_computed gates a second startup call from duplicating prerace rows (D-11 idempotent restart)"
    requirement: DASH-02
    verification:
      - kind: unit
        ref: "tests/test_pipeline_prerace.py::test_prerace_idempotent_on_second_startup"
        status: pass
    human_judgment: false
  - id: D3
    description: "get_latest_race_session_key resolves the latest Race-type session_key with no manual --session-key flag (D-01)"
    requirement: DASH-02
    verification:
      - kind: unit
        ref: "tests/test_pipeline_prerace.py::test_prerace_idempotent_on_second_startup (exercises get_latest_race_session_key via manual smoke test in this session; no dedicated unit test written for this helper alone)"
        status: pass
    human_judgment: true
    rationale: "No dedicated automated test isolates get_latest_race_session_key's ORDER BY session_key DESC behavior against multiple competing Race sessions — verified manually via an ad hoc smoke script in this session, not a committed test. A human/verifier should confirm this ordering behavior against a real multi-session DB before relying on it in Phase 4."
  - id: D4
    description: "Dashboard Pre-Race tab renders a horizontal bar chart of drivers ranked by win probability, color-coded by team, with '#'-prefixed marker colors (Pitfall 3, D-05)"
    requirement: DASH-04
    verification:
      - kind: unit
        ref: "plan verify command: build_win_prob_bar_chart marker.color all start with '#' (manual invocation, this session)"
        status: pass
      - kind: manual_procedural
        ref: "streamlit run src/f1_prediction/dashboard.py at localhost:8501 — visual confirmation deferred to Plan 04 checkpoint per plan's own acceptance criteria"
        status: unknown
    human_judgment: true
    rationale: "Visual rendering of the chart in an actual browser session is explicitly deferred to Plan 04's checkpoint per this plan's acceptance criteria; only the underlying chart-builder logic and static source checks were verified this session."
  - id: D5
    description: "Dashboard binds to localhost only and requires no authentication (DASH-04) — no --server.address override anywhere in dashboard.py"
    requirement: DASH-04
    verification:
      - kind: unit
        ref: "plan verify command: grep-equivalent check '0.0.0.0' not in dashboard.py source (this session)"
        status: pass
    human_judgment: false

duration: 8min
completed: 2026-07-08
status: complete
---

# Phase 3 Plan 2: Pre-Race Prediction Pipeline & Dashboard Summary

**`python -m src.f1_prediction.pipeline` auto-detects the latest race and computes idempotent pre-race win probabilities (XGBoost + per-race softmax) into SQLite; `streamlit run src/f1_prediction/dashboard.py` reads them read-only and renders a ranked, team-colored horizontal bar chart at localhost:8501.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-07-08T04:13:08Z
- **Completed:** 2026-07-08T04:21:00Z
- **Tasks:** 3/3 completed
- **Files modified:** 7 (all newly created)

## Accomplishments
- Delivered the first end-to-end user-visible slice of Phase 3: pipeline → SQLite `predictions` table → dashboard, closing the loop DASH-02/DASH-04 require.
- `inference/prerace.py::compute_and_store_prerace` reuses `inference/features.py`'s `load_model`/`predict_with_softmax` verbatim (no reimplemented softmax) and follows the WR-02 two-connection read-then-write split from `features/builder.py`.
- `pipeline.py` resolves the latest Race session by `session_key DESC` alone (D-01, avoiding the `round_number` staleness risk flagged in RESEARCH A4), skips recomputation when `'prerace'` rows already exist (D-11), and best-effort-populates `raw_drivers` from OpenF1 `/v1/drivers` without ever crashing startup (D-06).
- `dashboard.py` + `dashboard_lib/` render the Pre-Race tab: a Plotly horizontal bar chart color-coded by team (`#`-prefixed hex per Pitfall 3), a UI-SPEC-matching empty state and no-session error, and a `"Pre-race · computed HH:MM:SS"` freshness caption — all while remaining strictly read-only against SQLite and binding to localhost only.
- Verified end-to-end with an ad hoc smoke script (train tiny model → compute predictions → dashboard queries → chart build) covering both the happy path and the LEFT JOIN fallback for a driver with no `raw_drivers` match.

## Task Commits

Each task was committed atomically:

1. **Task 1: RED — failing end-to-end test for the prerace prediction path** - `b819695` (test)
2. **Task 2: GREEN — prerace inference module + pipeline startup path (D-01, D-06, D-09, D-11)** - `1f51ad7` (feat)
3. **Task 3: Dashboard Pre-Race tab — ranked team-colored bar chart at localhost (DASH-02, DASH-04, D-05)** - `228bce2` (feat)

**Plan metadata:** committed in this final docs commit.

_Note: Task 1/2 form a RED/GREEN TDD pair (`tdd="true"`); no REFACTOR commit was needed — Task 2's implementation passed cleanly without further cleanup._

## Files Created/Modified
- `tests/test_pipeline_prerace.py` - Seeds historical Race sessions via `tmp_engine`, trains a tiny prerace model (`cv=2`), and asserts `compute_and_store_prerace`'s sum-to-1 behavior plus `pipeline.prerace_already_computed`'s idempotency gate
- `src/f1_prediction/inference/prerace.py` - `compute_and_store_prerace(session_key, model_path, *, engine=None)` — loads the model, reads `features_prerace`, reuses `predict_with_softmax`, bulk-inserts `predictions(prediction_type='prerace')`
- `src/f1_prediction/pipeline.py` - Entrypoint (`python -m src.f1_prediction.pipeline`); `get_latest_race_session_key`, `prerace_already_computed`, `sync_drivers_for_session`, `main()`, `_DRIVER_FIELDS` whitelist
- `src/f1_prediction/dashboard.py` - Streamlit entrypoint; `st.tabs(["Pre-Race", "In-Race"])`, `render_prerace_tab()`, no-session error state, In-Race placeholder
- `src/f1_prediction/dashboard_lib/__init__.py` - New empty package init
- `src/f1_prediction/dashboard_lib/queries.py` - `_engine` (cached), `get_latest_race_session_key`, `load_prerace_predictions` (LEFT JOIN + fallback), `get_race_display_name`
- `src/f1_prediction/dashboard_lib/charts.py` - `build_win_prob_bar_chart(df)` — Plotly `go.Bar(orientation="h")` with `#`-prefixed team colors

## Decisions Made
- `dashboard_lib/queries.py` imports `pipeline.get_latest_race_session_key` instead of duplicating the D-01 query — keeps "latest race" resolution as a single source of truth shared by both processes.
- `load_prerace_predictions` uses a `LEFT JOIN` to `raw_drivers` (not an inner join), with Python-side `fillna` to a `driver_number` label and neutral gray `#888888` bar color when no match exists — verified via smoke test with a driver intentionally missing from `raw_drivers`.
- Bar chart sorts ascending by `win_probability` before building the trace, because Plotly renders horizontal bar categories bottom-to-top by default; ascending sort is what visually places the highest-probability driver on top.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed the literal string "0.0.0.0" from dashboard.py's own docstring**
- **Found during:** Task 3 (Dashboard Pre-Race tab), while running the plan's own verification command
- **Issue:** The module docstring documented the localhost-bind contract using the phrase "no --server.address 0.0.0.0 anywhere". The plan's verification command (and the T-03-02 threat-model mitigation: "Enforced by grep gate (no `0.0.0.0` in dashboard.py)") does a literal substring check on the whole file, which matched the docstring text itself — a false positive that would have failed the DASH-04 safety gate despite the file containing no actual bind-widening code.
- **Fix:** Rephrased the docstring to describe the same contract ("never pass a --server.address flag that widens the bind beyond localhost") without using the literal digit sequence, so the grep-based gate reflects only real code risk.
- **Files modified:** `src/f1_prediction/dashboard.py`
- **Verification:** Re-ran the plan's exact verify command (`assert '0.0.0.0' not in src`) — passes; full test suite (62 passed) unaffected.
- **Committed in:** `228bce2` (Task 3 commit)

---

**Total deviations:** 1 auto-fixed (1 bug fix, Rule 1)
**Impact on plan:** No scope creep — the fix only reworded a comment to satisfy the plan's own literal-string safety gate; no behavior changed.

## Issues Encountered
None beyond the docstring false-positive above — all three tasks executed cleanly; the RED test failed exactly as expected (ModuleNotFoundError for not-yet-built symbols) before Task 2's implementation made it pass.

## User Setup Required
None - no external service configuration required. Both entrypoints (`python -m src.f1_prediction.pipeline`, `streamlit run src/f1_prediction/dashboard.py`) run against the existing local SQLite DB and the already-fixed Streamlit 1.59.0 install from Plan 01.

## Next Phase Readiness
- `pipeline.py` and `dashboard_lib/queries.py` both expose `get_latest_race_session_key` — Plan 03's in-race poll loop and dashboard tab can reuse either without re-deriving the D-01 query.
- `inference/prerace.py`'s WR-02 two-connection pattern and reuse of `predict_with_softmax` is the template Plan 03's `inference/inrace.py` should follow for `predict_inrace_with_softmax`.
- `dashboard_lib/charts.py::build_win_prob_bar_chart` is generic over any DataFrame with `label`/`win_probability`/`team_colour` columns — Plan 03's in-race tab can reuse it directly for the lap-by-lap chart.
- The In-Race tab in `dashboard.py` is currently a static placeholder (`render_inrace_tab()`) — Plan 03 replaces its body with the `st.fragment(run_every=15)`-wrapped live-refresh implementation (D-07/D-08).
- No blockers for Plan 03. One item worth a verifier's attention: `get_latest_race_session_key`'s `ORDER BY session_key DESC` behavior across multiple competing Race sessions was only checked via an ad hoc manual smoke test in this session (see coverage `D3`), not a committed automated test — Plan 03 or a later verify-work pass should consider adding one if multi-session ordering becomes load-bearing.

---
*Phase: 03-prediction-pipeline-dashboard*
*Completed: 2026-07-08*

## Self-Check: PASSED

All 7 created files confirmed present on disk. All 3 task commit hashes (b819695, 1f51ad7, 228bce2) confirmed in git log.
