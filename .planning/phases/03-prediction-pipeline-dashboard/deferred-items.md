# Deferred Items — Phase 03 (prediction-pipeline-dashboard)

Out-of-scope discoveries found during Plan 04 execution. Not fixed here per
the executor's scope-boundary rule (only auto-fix issues directly caused by
the current task's own changes) — logged for a future plan/fix pass.

## 1. `get_latest_race_session_key` resolves the wrong "latest race" — BLOCKS Plan 04's checkpoint

**Found during:** Plan 04, while picking a session_key to hand the human
verifier for the checkpoint (Task 2).

**Owning code:** `src/f1_prediction/pipeline.py::get_latest_race_session_key`
(Plan 03-02), reused verbatim by
`src/f1_prediction/dashboard_lib/queries.py::get_latest_race_session_key`
and by `dashboard.py::main()`. None of these files are in Plan 04's
`files_modified` list (`scripts/replay_race.py`, `tests/test_replay_race.py`
only) — out of scope to fix as part of this plan.

**Issue:** `get_latest_race_session_key` resolves "latest race" via
`ORDER BY session_key DESC LIMIT 1` alone. FastF1-synthesized 2022
session_keys (`year*100_000 + round*100 + session_type_code`, e.g.
`202202207` for 2022 Abu Dhabi round 22) are numerically *larger* than real
OpenF1 session_keys for 2023-2025 races (small sequential integers, e.g.
`9987`-`10028` for 2025 races currently in the DB). As a result,
`get_latest_race_session_key()` currently returns `202202207` (2022 Abu
Dhabi/Yas Island) — a stale 2022 race — instead of any actual 2023-2025
race, even though several much more recent OpenF1 races exist in
`raw_sessions`.

Verified live against the project DB during this session:
```
get_latest_race_session_key(get_engine()) == 202202207   # 2022, round 22, Yas Island
```
while the most recent real races present are 2025 OpenF1 sessions
(session_keys 9987-10028).

**Compounding data-completeness gap:** session `202202207` (and 2022 rounds
17-21 generally) also has **zero** `features_prerace` rows — rounds 1-16 of
the 2022 season have 20 `features_prerace` rows each, but rounds 17-22 have
0. This looks like the Phase 2 feature builder ran before those later 2022
rounds were backfilled, and was never re-run for them. `build_all_features`
would need to be re-run (or targeted at `only_years=[2022]`) to backfill
this gap.

**Impact on Plan 04's checkpoint:** `dashboard.py` and `pipeline.py` always
operate on whatever `get_latest_race_session_key()` resolves to — there is
no CLI/UI way to point either process at a different session_key. This
means:
- The dashboard will always show session `202202207`, regardless of which
  `--source-session-key` a human verifier passes to
  `scripts/replay_race.py`.
- Because `202202207` has zero `features_prerace` rows, `pipeline.py`'s
  pre-race computation produces nothing, and the dashboard's Pre-Race tab
  will show the "No qualifying data yet" empty state — Task 2's checkpoint
  step 2 ("Confirm the Pre-Race tab shows a ranked, team-colored horizontal
  bar chart") cannot pass until this is fixed.
- The In-Race tab (this plan's actual focus) *can* still be demonstrated
  correctly if the human verifier passes `--source-session-key 202202207`
  to `replay_race.py` (matching what the dashboard will show) — session
  `202202207` does have full `raw_laps` data (58 laps) — but the Pre-Race
  tab will not show real data alongside it.

**Suggested fix (not applied — out of scope for Plan 04):** Change
`get_latest_race_session_key`'s ordering to something that correctly
reflects chronological recency across mixed FastF1/OpenF1 session_key
schemes — e.g. `ORDER BY date_start DESC` (both sources populate
`date_start` as an ISO 8601 string) or `ORDER BY year DESC, round_number
DESC` (round_number is reliable after `backfill.py::fix_round_numbers()`
has run) — then backfill `features_prerace` for 2022 rounds 17-22 via
`build_all_features(only_years=[2022])`.

**Status:** Deferred. Flagged prominently in Plan 04's checkpoint return so
the human verifier can decide how to proceed (pick session `202202207` for
a partial demo, authorize a fix to `get_latest_race_session_key` first, or
backfill the missing 2022 features).

**Resolution:** Fixed during Plan 04 execution (Rule 1 deviation, commit
`ece53d9`) — `get_latest_race_session_key` now orders by `date_start DESC`
instead of `session_key DESC`, so it correctly resolves to session `9839`
(2025, round 23, United Arab Emirates) rather than the stale 2022 session.
This item is retained above for historical context; see the SUMMARY.md
"Deviations" section for the applied fix.

## 2. `raw_positions` is empty for session 9839 (the now-current "latest race")

**Found during:** Plan 04, Task 2 human verification — after the
`get_latest_race_session_key` fix (item 1) started resolving to session
`9839` (2025 round 23, United Arab Emirates), verified that session's
`raw_positions` table has 0 rows (`raw_laps` has 1156 rows for the same
session). This is a pre-existing backfill gap, not something introduced by
Plan 04 or by the `get_latest_race_session_key` fix, and no file in this
plan's `files_modified` list touches ingestion — out of scope to backfill
here.

**Impact:** The In-Race tab's side table columns that read from
`raw_positions` (current position) will be empty/fallback for session
`9839` specifically, until this session is re-backfilled. This did not
block Task 2's checkpoint — the human verifier used
`--source-session-key 202202207` (which does have full `raw_positions`
data) to demonstrate the live lap-by-lap chain, matching how the earlier
`get_latest_race_session_key` blocker was worked around.

**Suggested fix (not applied — out of scope for Plan 04):** Re-run
`scripts/backfill.py` targeted at session `9839` (or its meeting/round) to
populate `raw_positions`.

**Status:** Deferred to a future backfill pass.
