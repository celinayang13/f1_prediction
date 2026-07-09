# Phase 4: Live Race Integration - Pattern Map

**Mapped:** 2026-07-09
**Files analyzed:** 2 (1 new artifact, 1 possible minimal addition)
**Analogs found:** 2 / 2

**Context note:** This is a validation-only phase (per CONTEXT.md D-01 through D-06). No new feature code, no new tables, no dashboard changes are expected unless a bug surfaces. The only guaranteed new artifact is `VALIDATION.md`. A possible minimal health-visibility addition to `pipeline.py`/`poller.py` logging is left to planner discretion (D-03) — patterns for that are included below in case it's needed, but planner should default to "no code change, just run and observe existing logs" per D-03's explicit "don't over-build" guidance.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|-----------------|----------------|
| `.planning/phases/04-live-race-integration/VALIDATION.md` | test/report artifact | request-response (manual observation → written record) | `.planning/phases/01-data-features/01-VERIFICATION.md` and `03-VERIFICATION.md` | exact (structural analog, not code) |
| *(optional)* minimal heartbeat/health log line in `pipeline.py::main()` poll loop | logging/observability addition | event-driven | `src/f1_prediction/pipeline.py::_poll_and_infer_job` + `poll_once()`'s existing `logger.info` line | exact (extends existing pattern, does not introduce a new one) |

No new tables, controllers, services, or components are expected. `dashboard.py`, `queries.py`, `charts.py`, `session_window.py`, and `replay_race.py` are read-only reuse targets for validation, not files to modify (per CONTEXT.md `code_context`).

## Pattern Assignments

### `.planning/phases/04-live-race-integration/VALIDATION.md` (report artifact)

**Analog:** `.planning/phases/01-data-features/01-VERIFICATION.md` and `.planning/phases/03-prediction-pipeline-dashboard/03-VERIFICATION.md`

These two prior VERIFICATION.md files establish the project's convention for mapping success criteria to observed evidence. D-05 explicitly asks for a `VALIDATION.md` that maps each of the 4 ROADMAP §Phase 4 success criteria to an **observed result from the replay run** — this is structurally the same job as a VERIFICATION.md's "Observable Truths" table, scoped down to just the 4 criteria with direct evidence lines (not a full goal-backward audit).

**Table structure to copy** (from `01-VERIFICATION.md` lines 20-30 and `03-VERIFICATION.md` lines 34-44):
```markdown
### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | <criterion text> | VERIFIED | <concrete observation: command run, log line, timestamp/lag observed> |
| 2 | <criterion text> | VERIFIED | <...> |
| 3 | <criterion text> | VERIFIED | <...> |
| 4 | <criterion text> | VERIFIED | <...> |

**Score:** 4/4 truths verified
```

**The exact 4 criteria to map** (from `.planning/ROADMAP.md` §Phase 4, lines 121-126):
```
1. During an active race session, the poller fetches new lap data from OpenF1 within 60 seconds
   of lap completion and triggers feature building and inference automatically
2. The dashboard updates win probabilities lap-by-lap without user action during a live race
3. No SQLite write-contention errors occur during concurrent poller writes and dashboard reads
   (WAL mode confirmed working)
4. The full system (poller + dashboard) runs stably for a full race distance (~60 laps) on the
   developer's machine without crash or hang
```

**Evidence-gathering pattern per criterion** (per CONTEXT.md D-03/D-05/D-06 — reuse existing logging, don't build new tooling):

- **Criterion 1 (60s lag):** Run `scripts/replay_race.py` alongside `python -m src.f1_prediction.pipeline`; read `poll_once()`'s existing `logger.info("poll_once: session %d — %s", session_key, result)` line (`src/f1_prediction/live/poller.py:202`) and correlate timestamps against replay's lap-insertion timestamps to compute observed lag. Note: per `replay_race.py`'s own docstring (lines 24-38), the real poll loop's `is_session_active()` gate will always be False against a completed historical race, so criterion 1's "60s lag" is more directly validated by replay's own per-lap insert timestamps vs. `compute_and_store_inrace_latest_lap()` being called immediately after (which `replay_race.py` does directly, not via the poll loop) — document this nuance in VALIDATION.md rather than treating it as a failure.
- **Criterion 2 (dashboard lap-by-lap):** Run `streamlit run src/f1_prediction/dashboard.py` concurrently; reuse the same human-observation approach as `03-UAT.md` (referenced in `03-VERIFICATION.md` line 41) — watch the `st.fragment(run_every=15)` in-race tab auto-refresh while replay runs.
- **Criterion 3 (WAL no-contention):** Per D-06, grep pipeline/dashboard process logs for `SQLITE_BUSY`/`database is locked` during the concurrent run; absence is the evidence line. `get_engine()`'s WAL pragma (`src/f1_prediction/db/engine.py:22-27`) is the mechanism being validated, not introduced.
- **Criterion 4 (60-lap stability):** Let replay run a full ~60-lap race; confirm the pipeline process is still alive at the end (no unhandled exception in logs, no crash requiring D-04's manual restart).

**Frontmatter convention** (optional, copy from `01-VERIFICATION.md` lines 1-7 if a lightweight header is desired):
```yaml
---
phase: 04-live-race-integration
validated: <date>
status: passed
score: 4/4
---
```

---

### *(Optional, planner discretion per D-03)* Health-visibility addition to `pipeline.py`

**Analog:** `src/f1_prediction/live/poller.py::poll_once()` lines 194-203, and `pipeline.py::main()` lines 202-224

If planning determines a minimal heartbeat/staleness check is warranted (D-03 leaves this to Claude's discretion, but explicitly says "don't over-build monitoring infrastructure"), the existing per-cycle `logger.info` call is the pattern to extend — not a new module:

**Existing per-cycle log line to build on** (`src/f1_prediction/live/poller.py:202`):
```python
result = {
    "session_key": session_key,
    "skipped": False,
    "laps_inserted": laps_inserted,
    "stints_replaced": stints_replaced,
    "positions_inserted": positions_inserted,
    "pit_stops_inserted": pit_stops_inserted,
}
logger.info("poll_once: session %d — %s", session_key, result)
return result
```

**Scheduler job wrapper pattern to extend if a staleness check is added** (`src/f1_prediction/pipeline.py:204-211`):
```python
def _poll_and_infer_job():
    result = poll_once(session_key, engine=engine)
    run_inference_after_poll(engine, session_key, result)

def _on_error(event):
    logger.error("pipeline poll job failed: %s", event.exception)

scheduler.add_listener(_on_error, EVENT_JOB_ERROR)
```
A minimal addition (if pursued) would be a module-level `_last_poll_time` timestamp set at the top of `_poll_and_infer_job`, logged via the existing `logging.basicConfig(level=logging.INFO, ...)` config (`pipeline.py:167-171`) — no new alerting/supervisor infra, consistent with D-04's "manual restart is sufficient."

**Recommendation:** Default to NOT adding this. D-03 explicitly favors relying on existing logs; the `EVENT_JOB_ERROR` listener + per-cycle `logger.info` already give sufficient signal for a developer watching the terminal during a supervised replay run.

## Shared Patterns

### Logging (already established, no new pattern needed)
**Source:** `src/f1_prediction/pipeline.py:167-171` (`logging.basicConfig`) + `src/f1_prediction/live/poller.py:202` (per-cycle `logger.info`)
**Apply to:** Validation observation — read these logs directly rather than instrumenting new output.

### Crash isolation (already established)
**Source:** `BackgroundScheduler` + `max_instances=1` + `misfire_grace_time=15` + `EVENT_JOB_ERROR` listener, present in both `poller.py:393-401` and `pipeline.py:211-219`
**Apply to:** Criterion 4 (stability) — this is the mechanism already preventing a single bad poll cycle from crashing the process; VALIDATION.md should cite this as the design basis, not test a new mechanism.

### WAL mode (already established)
**Source:** `src/f1_prediction/db/engine.py:22-27` (`_set_sqlite_pragma` event listener)
**Apply to:** Criterion 3 — cite this as the mechanism under validation.

### SQL parameterization (if any validation query is added to VALIDATION.md's evidence-gathering, e.g. an ad hoc lag-check query)
**Source:** all existing queries in `poller.py`, e.g. lines 135-141:
```python
max_lap_row = conn.execute(
    text(
        "SELECT COALESCE(MAX(lap_number), 0) FROM raw_laps "
        "WHERE session_key = :sk"
    ),
    {"sk": session_key},
).fetchone()
```
**Apply to:** Any one-off SQL used to compute lag/timestamps for VALIDATION.md evidence — always `text()` with bound `:param`, never f-string SQL.

## No Analog Found

None — this phase produces no genuinely new code pattern. `VALIDATION.md` has direct structural analogs in prior VERIFICATION.md files, and any optional logging addition extends an existing, already-established pattern rather than introducing a new one.

## Metadata

**Analog search scope:** `.planning/phases/01-data-features/`, `.planning/phases/03-prediction-pipeline-dashboard/`, `src/f1_prediction/pipeline.py`, `src/f1_prediction/live/poller.py`, `src/f1_prediction/db/engine.py`, `scripts/replay_race.py`
**Files scanned:** 6
**Pattern extraction date:** 2026-07-09
