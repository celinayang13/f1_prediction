---
phase: 03-prediction-pipeline-dashboard
plan: 04
subsystem: testing
tags: [streamlit, sqlite, sqlalchemy, dev-tooling, replay]

# Dependency graph
requires:
  - phase: 03-prediction-pipeline-dashboard
    provides: "03-02 pre-race pipeline/dashboard, 03-03 in-race inference + auto-refreshing dashboard tab — this plan exercises both end to end"
provides:
  - "scripts/replay_race.py — dev-only CLI that re-inserts a completed historical race's raw_* rows lap-by-lap on a timer, simulating live arrival without a real OpenF1 session"
  - "Human-verified confirmation that the full poll -> inference -> dashboard chain works end to end (Pre-Race tab + auto-refreshing In-Race tab, localhost-only, no auth)"
  - "Fix: get_latest_race_session_key now resolves the true latest race across mixed FastF1/OpenF1 session_key schemes"
  - "Fix: dashboard now provably binds to localhost regardless of launch flags"
affects: [04-live-race-integration]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dev-only replay tooling: stage-then-clear-then-rebuild pattern for safely replaying 'in place' into the same session_key a completed race already occupies, with an exception-handler restore path so an interrupted run never leaves data incomplete"

key-files:
  created:
    - scripts/replay_race.py
    - tests/test_replay_race.py
  modified:
    - src/f1_prediction/pipeline.py
    - .streamlit/config.toml
    - .planning/phases/03-prediction-pipeline-dashboard/deferred-items.md

key-decisions:
  - "get_latest_race_session_key orders by date_start DESC, not session_key DESC — FastF1-synthesized 2022 session_keys are numerically larger than real OpenF1 2023+ session_keys, so raw session_key ordering always resolved to a stale 2022 race"
  - ".streamlit/config.toml pins [server] address = \"localhost\" — streamlit's actual runtime default (observed via lsof) binds *:8501 under --server.headless true despite dashboard.py's docstring claiming a localhost-only default"
  - "replay_race.py in-place replay (default target == source) stages a full backup under session_key + 900_000_000_000 before clearing/rebuilding, so an interrupted replay restores the source race immediately rather than leaving it partially deleted"

patterns-established:
  - "Dev replay tooling calls inference.inrace.compute_and_store_inrace_latest_lap() directly after each lap insert, since pipeline.py's own poll loop gates on the real OpenF1 session_window check and can never observe a replayed historical race on its own"

requirements-completed: [DASH-01, DASH-03]

coverage:
  - id: D1
    description: "scripts/replay_race.py re-inserts a completed race's raw_* rows into the raw_* tables incrementally on a timer (D-02), defaulting to a fast 5s/lap interval with --interval to slow it down (D-03)"
    requirement: "DASH-01"
    verification:
      - kind: unit
        ref: "tests/test_replay_race.py#test_replay_lap_incremental_no_duplicates (and CLI --help assertion)"
        status: pass
    human_judgment: false
  - id: D2
    description: "End-to-end human verification: pipeline + dashboard + replay running together show the In-Race tab auto-refreshing lap-by-lap with a LIVE badge, re-ordering bar chart, and side table (position/tyre/gap), on localhost only with no login prompt"
    requirement: "DASH-03"
    verification:
      - kind: manual_procedural
        ref: "Task 2 checkpoint:human-verify — user visited http://localhost:8501, confirmed Pre-Race tab bar chart and In-Race tab auto-refresh/LIVE badge/side table, confirmed localhost-only URL with no login prompt"
        status: pass
    human_judgment: true
    rationale: "Visual/UX confirmation of live auto-refresh behavior and dashboard security posture cannot be fully proven by unit tests alone — required direct human observation of the running app, which was obtained and approved."

duration: ~25min
completed: 2026-07-08
status: complete
---

# Phase 3 Plan 4: Replay Dev Tool & End-to-End Verification Summary

**Dev-only lap-by-lap replay CLI (`scripts/replay_race.py`) proves the full poll → inference → dashboard chain works live, using a completed historical race in place of a real OpenF1 session — human-verified working on localhost with no auth.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 2 (1 auto + 1 checkpoint:human-verify)
- **Files modified:** 5 (2 created for Task 1; 2 modified as auto-fix deviations; 1 deferred-items log update)

## Accomplishments
- Built `scripts/replay_race.py`: reads a completed race's `raw_laps`/`raw_positions`/`raw_stints`/`raw_pit_stops` rows and re-inserts them lap-by-lap on a `time.sleep(--interval)` loop (default 5.0s, D-03), calling `inference.inrace.compute_and_store_inrace_latest_lap()` after each lap so the dashboard's In-Race tab updates without needing `pipeline.py`'s real OpenF1-gated poll loop to fire.
- In-place replay (default `--target-session-key` = `--source-session-key`) stages a full backup under a scratch session_key before clearing/rebuilding the source race, with an exception handler that restores every remaining lap immediately on interruption (Ctrl-C or crash) — the source race is never left incomplete.
- Ran the pipeline, dashboard, and replay together and obtained explicit human approval (Task 2 checkpoint) that the Pre-Race tab renders a ranked team-colored bar chart and the In-Race tab auto-refreshes lap-by-lap with a LIVE badge, re-ordering bar chart, and side table (position/tyre compound/tyre age/gap to leader) — all on `localhost:8501` with no login prompt.
- Fixed a pre-existing bug (discovered while preparing the checkpoint, not introduced by this plan) in `get_latest_race_session_key` that caused both `pipeline.py` and `dashboard.py` to always resolve a stale 2022 race instead of the true latest session.
- Fixed a pre-existing gap between `dashboard.py`'s documented "localhost-only by default" claim and Streamlit's actual runtime behavior under `--server.headless true` (empirically bound `*:8501` without an explicit `server.address`).

## Task Commits

1. **Task 1: Replay script — re-insert a completed race's raw_* rows on a timer (D-02, D-03)** - `98ec779` (feat)
2. **Task 2: End-to-end human verification of the live chain** - checkpoint:human-verify — no code commit (verification-only task); user approved after visiting http://localhost:8501 and confirming Pre-Race tab, In-Race tab auto-refresh, LIVE badge, side table, and localhost-only/no-auth binding.

Deviations committed alongside Task 1 preparation:
- `8c8526c` - docs: log get_latest_race_session_key blocker as deferred item (pre-fix)
- `ece53d9` - fix: get_latest_race_session_key orders by date_start not session_key (Rule 1)
- `ca2397c` - fix: pin streamlit server.address to localhost (Rule 1/2)

**Plan metadata:** (this commit) - docs: complete 03-04 plan

## Files Created/Modified
- `scripts/replay_race.py` - Dev-only CLI: `replay_lap()` (per-lap dedupe-safe insert), `main()` (argparse, stage/clear/rebuild for in-place replay, restore-on-interrupt)
- `tests/test_replay_race.py` - Seeds a tmp engine, asserts incremental insert + no duplication on re-run of the same lap
- `src/f1_prediction/pipeline.py` - `get_latest_race_session_key` now orders by `date_start DESC` instead of `session_key DESC` (Rule 1 fix, out-of-scope file, applied because it blocked the checkpoint's Pre-Race tab demo)
- `.streamlit/config.toml` - Added `[server] address = "localhost"` (Rule 1/2 fix — makes the already-documented DASH-04 localhost-only invariant actually true)
- `.planning/phases/03-prediction-pipeline-dashboard/deferred-items.md` - Logged the `get_latest_race_session_key` blocker (pre-fix), its resolution, and a new pre-existing gap discovered post-fix (session 9839's `raw_positions` is empty)

## Decisions Made
- `get_latest_race_session_key` ordering changed from `session_key DESC` to `date_start DESC` — both FastF1 backfill and OpenF1 poller populate `date_start` as an ISO 8601 string, so this sorts correctly across both session_key synthesis schemes (2022 FastF1-derived vs. 2023+ OpenF1 sequential).
- Streamlit's `[server] address` is now pinned in `.streamlit/config.toml` rather than relying on remembering a `--server.address` CLI flag on every launch.
- Replay's in-place mode (target == source) uses a stage-then-clear-then-rebuild sequence with a restore-on-interrupt exception handler, rather than a simpler destructive clear, so a Ctrl-C mid-replay can never leave the source race's historical data permanently incomplete.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug, out-of-scope file] `get_latest_race_session_key` resolved a stale 2022 race instead of the true latest session**
- **Found during:** Task 1/2 prep — choosing a `session_key` to hand the human verifier for the checkpoint, and observing the dashboard's own resolution logic would show a different (stale) session than whatever was passed to `replay_race.py`.
- **Issue:** Ordering by `session_key DESC` alone put FastF1-synthesized 2022 session_keys (`year*100_000 + round*100 + session_type_code`, e.g. `202202207`) numerically above real OpenF1 2023-2025 session_keys (small sequential integers like `9839`), so the function always returned a 2022 race regardless of how many newer real races existed. This also meant the Pre-Race tab showed the "No qualifying data yet" empty state, since that stale 2022 round had zero `features_prerace` rows.
- **Fix:** Changed the `ORDER BY` clause to `date_start DESC` in `src/f1_prediction/pipeline.py::get_latest_race_session_key`, reused verbatim by `dashboard_lib/queries.py` and `dashboard.py`. Verified it now resolves session `9839` (2025, round 23, United Arab Emirates) instead of `202202207`.
- **Files modified:** `src/f1_prediction/pipeline.py`
- **Verification:** `tests/test_pipeline_prerace.py` and `tests/test_pipeline_inrace.py` still pass; manual query confirms `get_latest_race_session_key(get_engine()) == 9839`.
- **Committed in:** `ece53d9`

**2. [Rule 1/2 - Bug/Missing Critical, out-of-scope file] Dashboard did not actually bind to localhost-only despite its own docstring claiming it did**
- **Found during:** Re-preparing the Task 2 live demo — the exact launch invocation used for verification (`streamlit run src/f1_prediction/dashboard.py --server.headless true`) was confirmed via `lsof` to bind `*:8501` (all interfaces), not localhost, contradicting `dashboard.py`'s existing docstring claim and the DASH-04 requirement.
- **Fix:** Added `[server] address = "localhost"` to `.streamlit/config.toml`, which is loaded automatically regardless of CLI flags, making the already-documented localhost-only invariant actually hold.
- **Files modified:** `.streamlit/config.toml`
- **Verification:** Task 2's checkpoint step explicitly confirmed the dashboard URL stayed on `localhost:8501` and never prompted for a login.
- **Committed in:** `ca2397c`

---

**Total deviations:** 2 auto-fixed (2 Rule 1/2 bug/security fixes to out-of-scope shared files, both required for the checkpoint to demonstrate the plan's actual acceptance criteria — DASH-04 localhost-only and a working Pre-Race tab).
**Impact on plan:** Both fixes were necessary for the human-verify checkpoint to pass honestly (a stale-data Pre-Race tab and a non-localhost bind would both have been visible defects during verification, not cosmetic ones). No scope creep beyond what was required to make the plan's own stated acceptance criteria true.

## Issues Encountered

**Environment: `f1_prediction` package was not importable outside pytest's shim.** During Task 2 prep, running `python -m src.f1_prediction.pipeline` and `streamlit run src/f1_prediction/dashboard.py` directly (not under pytest) failed with `ModuleNotFoundError: No module named 'f1_prediction'` under the Anaconda base `python3.11` this project actually runs under (the project's own venv has stub dist-info directories without source files — a pre-existing 01-01 decision — but the *processes actually used for this plan's manual verification* run under system/Anaconda Python, not that venv). Resolved by running `pip install -e . --no-deps` into that interpreter (no git change; environment-only, not a code/test fix, so no commit). **This affects anyone else setting up this project fresh on a machine where `f1_prediction` isn't already pip-installed in editable mode** — worth a one-line setup-note callout (e.g. in a README or CONTRIBUTING doc) that `pip install -e .` is required before running `pipeline.py`/`dashboard.py` directly, separate from however pytest discovers the package. Not addressed here — out of scope for this plan's `files_modified`, and no README currently exists in this repo to add it to.

## User Setup Required

None - no external service configuration required for this plan itself. (See "Issues Encountered" above for a local dev-environment note that is not a config/secret requirement, just a missing `pip install -e .` step for fresh checkouts.)

## Next Phase Readiness

- The full Phase 3 poll → inference → dashboard chain is now demonstrably working end to end against realistic lap-by-lap data arrival, with explicit human sign-off — Phase 4 (live race integration) can proceed with confidence the same code path works, pending only the real OpenF1 live session in place of the replay tool.
- `get_latest_race_session_key`'s date-based ordering fix is a permanent correctness fix (not demo-only) and will also apply correctly once real 2026 race sessions start appearing in `raw_sessions`.
- Deferred: session `9839`'s `raw_positions` table is empty (pre-existing backfill gap, unrelated to this plan — logged in `deferred-items.md` for a future backfill pass); the checkpoint's live demo used `--source-session-key 202202207` (which has full position data) to work around this.
- Deferred: a project setup note (README/CONTRIBUTING) documenting that `pip install -e .` is required to run `pipeline.py`/`dashboard.py` directly outside pytest would help future fresh-checkout setups; not created here as it is beyond this plan's scope.

---
*Phase: 03-prediction-pipeline-dashboard*
*Completed: 2026-07-08*
