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
  critical: 1
  warning: 5
  info: 3
  total: 9
status: issues_found
---

# Phase 03: Code Review Report

**Reviewed:** 2026-07-08T00:00:00Z
**Depth:** standard
**Files Reviewed:** 17
**Status:** issues_found

## Summary

Reviewed the Phase 3 prediction-pipeline + Streamlit dashboard implementation: the pre-race/in-race inference modules, the replay dev tool, the dashboard entrypoint + query/chart helpers, the ORM schema additions, and the associated tests.

SQL usage is consistently parameterized (`text()` + bound params) across every file — no injection risk found. The dashboard is genuinely read-only against SQLite, and `.streamlit/config.toml` correctly pins `server.address = "localhost"`, closing the no-auth exposure risk documented in DASH-04.

Two notable "would-be findings" were investigated and **ruled out** after checking project history: (1) `scripts/train.py` uses XGBoost rather than the LightGBM recommended in `CLAUDE.md`'s Technology Stack table, and (2) the dashboard is built on Streamlit rather than the recommended Dash — Streamlit even appears in `CLAUDE.md`'s explicit "What NOT to Use" table. Both are **intentional, documented supersessions**: `.planning/PROJECT.md` §Key Decisions locks "XGBoost + Streamlit over LightGBM + Dash" as a deliberate user-preference override, and `03-RESEARCH.md` explicitly calls out that `CLAUDE.md`'s stack section is stale on this point. Flagging these would have been a false positive; they are noted here only for the record.

The one **real, demonstrable defect** found is in `scripts/replay_race.py`'s crash-recovery path: the tool's own docstring promises the source race's data is "never left permanently incomplete" even if interrupted, but a second interruption during the recovery restore loop causes the `finally` block to delete the only backup copy of the original data before restoration has finished — a genuine data-loss bug in a tool whose entire purpose is to safely mutate a session's raw data in place.

Additional warnings cover a missing DB-level uniqueness constraint on `predictions`, a non-atomic delete+insert in the in-race inference write path, an architecture-coupling smell in the dashboard's query layer, a signal-handling gap in the pipeline's shutdown path, and a test-coverage gap for the crash-recovery logic itself.

## Critical Issues

### CR-01: replay_race.py crash-recovery restore can be interrupted, causing the finally block to delete the only backup and permanently lose race data

**File:** `scripts/replay_race.py:491-519`

**Issue:** For in-place replay (the default, `--target-session-key` unset), `main()` first stages a full backup of the source session's `raw_laps`/`raw_positions`/`raw_stints`/`raw_pit_stops` under a staging `session_key` (`_stage_source_snapshot`), then clears the original rows (`_reset_target_for_inplace_replay`) so they can be rebuilt lap-by-lap. The module docstring and `_stage_source_snapshot`'s own docstring explicitly promise this is safe because "*all* remaining laps are restored immediately if the replay is interrupted (Ctrl-C or any exception), so the source race's data is never left permanently incomplete."

That guarantee does not hold. The recovery path is:

```python
try:
    try:
        for lap_number in lap_numbers:
            result = replay_lap(engine, read_sk, target_sk, lap_number)
            ...
            time.sleep(args.interval)
    except BaseException:
        if staging_sk is not None:
            ...
            for lap_number in lap_numbers:
                try:
                    replay_lap(engine, read_sk, target_sk, lap_number)
                except Exception:
                    logger.exception("replay_race: failed to restore lap %d", lap_number)
        raise
finally:
    if staging_sk is not None:
        _clear_staging(engine, staging_sk)
```

The inner restore loop only catches `Exception`, not `BaseException`. If the user presses Ctrl-C a second time while the restore loop is running (a realistic scenario — a large race has 50-70 laps and the restore loop calls `replay_lap` synchronously for every one with no sleep, so it can take a visible amount of time; users are prone to spamming Ctrl-C when a CLI dev tool doesn't exit instantly), the second `KeyboardInterrupt` propagates straight out of the `for` loop, skipping any laps not yet restored. Regardless, execution falls into the `finally` block, which unconditionally calls `_clear_staging(engine, staging_sk)` and deletes the only backup copy of the original data — even though the target session's raw tables are still incomplete. The source race's real historical data (raw_laps/raw_positions/raw_stints/raw_pit_stops for whatever laps weren't restored) is now permanently gone with no recovery path, for a session that (in the default in-place mode) is the user's real project database, not a scratch copy.

**Fix:** Only clear staging after confirming the restore actually completed, and don't let a second interrupt abort the restore loop early:

```python
finally:
    if staging_sk is not None:
        restore_complete = locals().get("_restore_complete", False)
        if restore_complete:
            _clear_staging(engine, staging_sk)
        else:
            logger.error(
                "replay_race: restore did not complete — leaving staging session "
                "%d in place so no data is lost; re-run replay_race against "
                "session %d to finish recovery, or manually inspect staging_sk.",
                staging_sk, target_sk,
            )
```

and, in the `except BaseException` handler, wrap the restore loop so a second `KeyboardInterrupt`/`SystemExit` cannot escape mid-loop before every lap has been attempted (e.g. catch `BaseException` in the inner per-lap `try`, log it, and keep iterating — only re-raise after the full loop has run), setting `_restore_complete = True` only once every lap in `lap_numbers` has been attempted. Never delete the only backup until the code path that repopulates the original data from it has actually finished running.

## Warnings

### WR-01: `predictions` table has no DB-level uniqueness constraint — duplicate rows possible

**File:** `src/f1_prediction/db/models.py:319-356`

**Issue:** `Prediction` has no unique/composite constraint on `(session_key, driver_number, prediction_type, lap_number)`. Idempotency is enforced entirely at the application layer: `inference/prerace.py` relies on the pipeline's `prerace_already_computed` check-then-skip, and `inference/inrace.py` does an explicit `DELETE ... WHERE session_key = :sk AND prediction_type = 'inrace' AND lap_number = :lap` before inserting. If either guard is ever bypassed (a second pipeline process started concurrently, a manual re-run racing with the scheduler, or a future caller that doesn't go through these two entry points), duplicate rows for the same driver/lap can accumulate silently. Because the dashboard's chart/table both display **all** rows returned for the latest lap without deduplicating by driver, a duplicate would visually double-count that driver and break the sum-to-1 invariant several tests assert on.

**Fix:** Add a composite unique constraint / index at the DB layer as defense-in-depth:

```python
__table_args__ = (
    CheckConstraint(
        "prediction_type IN ('prerace', 'inrace')",
        name="ck_predictions_prediction_type",
    ),
    Index(
        "ix_predictions_session_type_lap",
        "session_key", "prediction_type", "lap_number",
    ),
    UniqueConstraint(
        "session_key", "driver_number", "prediction_type", "lap_number",
        name="uq_predictions_session_driver_type_lap",
    ),
)
```

### WR-02: `compute_and_store_inrace_latest_lap` deletes and inserts in separate transactions — dashboard can observe a stale/empty lap mid-run

**File:** `src/f1_prediction/inference/inrace.py:138-152`

**Issue:** The delete of existing `'inrace'` rows for `(session_key, lap_number)` runs and commits on one connection (`conn_delete`), and the subsequent insert (`bulk_insert_rows`) opens a brand-new connection afterward. Between the two, `dashboard_lib.queries.load_inrace_predictions`/`get_latest_inrace_lap` (which select `MAX(lap_number)` over `predictions`) can observe the previous lap's rows (if this is a re-run of an already-computed lap, e.g. after a crash-recovery re-run or a manual re-invocation) or zero rows for a brief window, rather than an atomic transition from lap N-1 to lap N.

**Fix:** Perform the delete and the insert within the same transaction/connection so the swap is atomic:

```python
with engine.begin() as conn:
    conn.execute(
        text(
            "DELETE FROM predictions WHERE session_key = :sk "
            "AND prediction_type = 'inrace' AND lap_number = :lap"
        ),
        {"sk": session_key, "lap": lap_number},
    )
    pd.DataFrame(rows).to_sql("predictions", conn, if_exists="append", index=False)
```

### WR-03: Dashboard query layer imports `pipeline.py`, coupling the read-only dashboard process to APScheduler/live-poller/inference modules

**File:** `src/f1_prediction/dashboard_lib/queries.py:22`, `src/f1_prediction/dashboard.py:6-10`

**Issue:** `dashboard.py`'s own docstring states "the two processes ... never talk directly — they communicate only through SQLite in WAL mode." However, `queries.py` does `from f1_prediction.pipeline import get_latest_race_session_key as _get_latest_race_session_key`, and `pipeline.py` transitively imports `apscheduler.events`, `apscheduler.schedulers.background.BackgroundScheduler`, `f1_prediction.inference.inrace`, `f1_prediction.inference.prerace`, and `f1_prediction.live.poller`. This means importing the dashboard module (`streamlit run src/f1_prediction/dashboard.py`) now pulls in the entire pipeline process's dependency graph, including packages the dashboard never uses (APScheduler, the live poller). Any import-time failure in those modules (a bad APScheduler version, a broken live/poller.py) will now also break dashboard startup, contradicting the documented process-independence goal, and makes the coupling between "read side" and "write side" much tighter than the architecture intends.

**Fix:** Extract `get_latest_race_session_key` (a pure read query with no scheduler/inference dependency) into a small shared module (e.g. `db/queries.py`) that both `pipeline.py` and `dashboard_lib/queries.py` import from, instead of having the dashboard import the pipeline entrypoint module.

### WR-04: No test coverage for replay_race.py's in-place staging/crash-recovery path (the mechanism behind CR-01)

**File:** `tests/test_replay_race.py`

**Issue:** The existing tests only exercise `replay_lap`'s incremental-copy and dedupe behavior for a distinct source/target pair. They never exercise the in-place (`target_session_key == source_session_key`) path — `_stage_source_snapshot`, `_reset_target_for_inplace_replay`, `_clear_staging`, or `main()`'s interrupt-handling restore loop — which is exactly the code responsible for not permanently losing a session's real race data (see CR-01). This is the highest-risk code path in the file (it deletes real data and depends on a backup/restore sequence for correctness) and has the least coverage.

**Fix:** Add a test that: seeds a source session, replays a few laps in-place (`target_session_key=None`), simulates an interruption partway through (e.g. monkeypatch `replay_lap` to raise `KeyboardInterrupt` on a specific lap), and asserts that after `main()`/the recovery path runs, the target session's raw tables exactly match the original pre-replay snapshot.

### WR-05: pipeline.py's shutdown handling doesn't cover SIGTERM

**File:** `src/f1_prediction/pipeline.py:239-244`

**Issue:** `main()`'s run loop is `try: while True: time.sleep(1) except (KeyboardInterrupt, SystemExit): ... scheduler.shutdown(wait=False)`. Python does not raise `KeyboardInterrupt` on `SIGTERM` by default (only `SIGINT`/Ctrl-C does, and `sys.exit()` raises `SystemExit`). If this process is run under a process manager (systemd, Docker, a supervisor script) that sends `SIGTERM` to stop it — the normal way to stop a long-running background process — the scheduler's graceful shutdown never runs, and the in-flight `BackgroundScheduler` job thread (which may be mid-poll or mid-write to SQLite) gets killed abruptly by the OS instead.

**Fix:** Register a `SIGTERM` handler that triggers the same shutdown path, e.g.:

```python
import signal

def _handle_sigterm(signum, frame):
    raise SystemExit(0)

signal.signal(signal.SIGTERM, _handle_sigterm)
```

## Info

### IN-01: `get_latest_inrace_lap` is dead code

**File:** `src/f1_prediction/dashboard_lib/queries.py:149-168`

**Issue:** `get_latest_inrace_lap` is defined, documented ("used for the freshness caption"), and exported via `__all__`, but nothing in the codebase calls it — `dashboard.py::render_inrace_tab` computes lap number and freshness directly from the `load_inrace_predictions` DataFrame (`df["lap_number"].iloc[0]`, `df["computed_at"].max()`) instead. The function is unreachable dead code.

**Fix:** Either wire it into `render_inrace_tab` (replacing the ad-hoc `.iloc[0]`/`.max()` derivation) or remove it along with its `__all__` entry.

### IN-02: Unused `numpy` import in scripts/train.py

**File:** `scripts/train.py:36`

**Issue:** `import numpy as np` is present but `np` is never referenced anywhere in the file.

**Fix:** Remove the unused import.

```python
-import numpy as np  # noqa: E402
```

### IN-03: replay_race.py pit-stop dedupe key can't distinguish two pit stops on the same lap

**File:** `scripts/replay_race.py:371-382`

**Issue:** `replay_lap`'s pit-stop dedupe uses `(driver_number, lap_number)` as the uniqueness key (`existing_pit_keys`). A driver who pits twice on the same lap (e.g. a drive-through penalty combined with a scheduled tyre stop) would have only the first of the two pit rows ever copied into the target session — the second would be silently treated as "already present" and dropped. This is a rare real-world case but is a genuine (if minor) data-fidelity gap for a replay/dev tool whose purpose is to faithfully mirror the source session.

**Fix:** If `raw_pit_stops` can have more than one row per `(session_key, driver_number, lap_number)`, include a distinguishing field (e.g. `date_recorded`) in the dedupe key, or count-based dedupe (compare row counts already copied vs. available in source for that key) instead of a set-membership check.

---

_Reviewed: 2026-07-08T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
