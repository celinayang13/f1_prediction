# Phase 4: Live Race Integration - Context

**Gathered:** 2026-07-09
**Status:** Ready for planning

<domain>
## Phase Boundary

Validate the full end-to-end system (poller → feature build → inference → dashboard) against realistic race conditions, using the replay tool built in Phase 3 (`scripts/replay_race.py`) as the validation mechanism for now. This phase produces no new feature code beyond whatever minimal health-visibility Claude judges necessary during planning — it exercises and confirms the four ROADMAP success criteria against the existing system.

A real live F1 race weekend is ~2 weeks out from context-gathering (2026-07-09) but is explicitly **not** part of this phase — see D-01.

**Not in scope:** new features, model changes, dashboard changes (unless a bug is found during validation and needs a fix), the actual real-race validation (deferred to a separate future check).

</domain>

<decisions>
## Implementation Decisions

### Validation Method & Timing

- **D-01:** Phase 4 validates using **`scripts/replay_race.py` only** (built in Phase 3, D-02/D-03) — not a real live OpenF1 race session. A real race weekend exists ~2 weeks from now (2026-07-09), but waiting for it is out of scope for this phase. Phase 4 is considered **complete** once replay-based validation passes all 4 ROADMAP success criteria. The real live-race check is explicitly deferred to a separate future task/phase, not a blocking part of Phase 4.

### OpenF1 Rate-Limit / Latency Risk

- **D-02:** Accept the OpenF1 free tier as-is for this phase — **no upgrade to the sponsor tier (~€9.90/mo)**. Since validation is replay-based (reads from local SQLite via `raw_*` tables, not live OpenF1 polling), the free-tier latency risk noted in STATE.md's Blockers/Concerns doesn't materially apply during replay. It remains a live-list item to revisit when the real race check happens later.

### Crash/Hang Detection & Recovery

- **D-03 (Claude's discretion):** How the ~60-lap stability run demonstrates "no crash or hang" is left to Claude to decide during planning/research — pick the simplest approach that gives confidence (e.g., relying on `pipeline.py`'s existing `logging.basicConfig` INFO-level per-cycle logs from `poll_once()`, optionally with a minimal staleness/heartbeat check if it's low-effort). Do not over-build monitoring infrastructure for a local dev tool.

- **D-04:** If the pipeline process crashes mid-run, **manual restart is sufficient** (`python -m src.f1_prediction.pipeline`). No auto-restart/supervisor process needed for v1 — this runs on the developer's own machine and they're expected to be present during validation.

### What Counts as "Validated"

- **D-05:** Produce a **written `VALIDATION.md`** (or equivalently named record) mapping each of the 4 ROADMAP §Phase 4 success criteria to an observed result from the replay run (e.g., "poller inserted new laps within 60s of simulated lap completion: confirmed, ~Xs observed lag at lap N"). This is the artifact that demonstrates the phase's success criteria were checked, not just eyeballed.

- **D-06:** The SQLite WAL/concurrent-write success criterion (criterion 3: no write-contention errors between poller and dashboard) is considered **implicitly covered** by running `python -m src.f1_prediction.pipeline` (writer) and `streamlit run src/f1_prediction/dashboard.py` (reader) concurrently during the replay — no separate stress-test script needed. The VALIDATION.md record should still note "no SQLITE_BUSY/locked errors observed in logs" as the evidence line for this criterion.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements & Roadmap
- `.planning/ROADMAP.md` §Phase 4 — defines the 4 exact success criteria this phase validates against (60s lap-detection window, lap-by-lap dashboard updates, WAL no-contention, ~60-lap stability); no new requirements (validation-only phase)
- `.planning/REQUIREMENTS.md` — notes Phase 4 is a "system validation phase," all v1 requirements already satisfied by Phases 1–3

### Project Constraints
- `.planning/PROJECT.md` §Key Decisions, §Constraints — local-first deployment, OpenF1-only data source, Python/SQLite stack
- `.planning/STATE.md` §Blockers/Concerns — flags the OpenF1 free-tier latency risk this phase's D-02 addresses (deferring the sponsor-tier decision to the real-race check)

### Prior Phase Context (the system being validated)
- `.planning/phases/03-prediction-pipeline-dashboard/03-CONTEXT.md` — D-01 through D-11 define the two-process architecture (pipeline writer / dashboard reader), the replay tool (D-02/D-03) this phase validates with, and the `predictions` table contract
- `.planning/phases/02-model-training/02-CONTEXT.md` — model artifact paths and feature contracts the inference pipeline depends on
- `.planning/phases/01-data-features/01-CONTEXT.md` — raw table schema and live-poller design (`raw_laps`, `raw_positions`, `raw_stints`, `raw_pit_stops`) that `replay_race.py` re-populates during simulation

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `scripts/replay_race.py` — re-inserts a completed historical race's laps/positions/stints/pit-stops at a configurable interval to simulate a live race; the validation mechanism for D-01. Already has in-place backup/restore-on-interrupt (per 03-04 decisions).
- `src/f1_prediction/pipeline.py::main()` — the process under test; already has SIGTERM handling (`_handle_sigterm`), a 30s poll interval (within the 60s success-criterion budget), and `logging.basicConfig(level=logging.INFO, ...)` per-cycle logging via `poll_once()`'s `logger.info("poll_once: session %d — %s", ...)` — this is the natural basis for D-03's health-visibility decision.
- `src/f1_prediction/live/poller.py::poll_once()` — returns a structured dict (`laps_inserted`, `stints_replaced`, `positions_inserted`, `pit_stops_inserted`, optional `error`) each cycle; useful for building any lightweight validation logging/assertions.
- `src/f1_prediction/live/session_window.py::is_session_active()` — ±30min window check; replay's simulated session must fall within this window for the poller to treat it as active (already handled by replay_race.py per Phase 3).
- `src/f1_prediction/dashboard.py`, `dashboard_lib/queries.py`, `dashboard_lib/charts.py` — the read-side under test; no changes expected unless validation surfaces a bug.

### Established Patterns
- All SQL uses `text()` with bound `:param` — continue this if any validation queries are added.
- `BackgroundScheduler` with `max_instances=1`, `misfire_grace_time=15`, `EVENT_JOB_ERROR` listener — already the crash-isolation pattern in both `poller.py` and `pipeline.py`; D-03/D-04 should build on this rather than adding new process-supervision.
- SQLite WAL mode already configured via SQLAlchemy event listener in `db/engine.py` (per 01-01 decision) — the mechanism D-06 is validating, not introducing.

### Integration Points
- No new tables/schema expected for this phase — validation reads existing `raw_*`, `features_*`, and `predictions` tables.
- `VALIDATION.md` (D-05) is a new phase-level artifact, analogous to a `SUMMARY.md`/`VERIFICATION.md` but scoped to the 4 ROADMAP success criteria specifically.

</code_context>

<specifics>
## Specific Ideas

- The user knows the exact real-race timing ("one in 2 weeks") — this is a hard external constraint, not a preference, and should inform any scheduling language in the plan (e.g., don't block Phase 4 completion on it).
- Keep this phase minimal — it's explicitly a smoke test / validation phase, not a place to add monitoring infrastructure, supervisors, or new dashboard features.

</specifics>

<deferred>
## Deferred Ideas

- Real live-race validation against actual OpenF1 data during the race weekend ~2026-07-23 — deferred to a separate future task/phase, not part of Phase 4's completion criteria (D-01)
- OpenF1 sponsor tier upgrade decision — deferred until the real live-race check, since replay doesn't exercise OpenF1 API latency at all (D-02)
- Auto-restart/process-supervisor for the pipeline process — explicitly rejected for v1 (D-04); local dev tool, manual restart acceptable
- Explicit WAL/concurrency stress-test script — explicitly rejected (D-06); concurrent pipeline+dashboard during replay is considered sufficient exercise of this criterion

### Reviewed Todos (not folded)
None — no pending todos matched Phase 4 (`todo.match-phase` returned 0 matches).

</deferred>

---

*Phase: 4-Live Race Integration*
*Context gathered: 2026-07-09*
