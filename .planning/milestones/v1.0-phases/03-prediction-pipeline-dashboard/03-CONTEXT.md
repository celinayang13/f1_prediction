# Phase 3: Prediction Pipeline & Dashboard - Context

**Gathered:** 2026-07-07
**Status:** Ready for planning

<domain>
## Phase Boundary

Wire trained model inference into the live poll loop, and ship a Streamlit dashboard that shows ranked win probabilities. Two decoupled processes communicate only through SQLite: `python -m src.f1_prediction.pipeline` (poller + inference, writes `predictions`) and `streamlit run src/f1_prediction/dashboard.py` (read-only UI). Phase 3 also needs to source driver names/team colors (not currently stored anywhere) and build a replay tool so live-refresh behavior can be exercised before an actual race weekend (Phase 4).

**Not in scope:** model retraining, live-race validation against a real OpenF1 session (that's Phase 4), multi-race browsing UI, DASH-V2 items (trend lines, race event annotations).

</domain>

<decisions>
## Implementation Decisions

### Session Selection & Live Testing

- **D-01:** The pipeline and dashboard always track the **latest race session** in the DB (most recent `Race`-type `session_key`, auto-detected). No manual session dropdown, no CLI `--session-key` flag for v1 — matches how a real race weekend will actually work in Phase 4 (there's only ever one "current" race).

- **D-02:** Build a **replay script** (e.g. `scripts/replay_race.py`) that re-inserts a completed historical race's laps/positions/stints/pit-stops into the `raw_*` tables at a configurable interval, simulating a live race so the poll → inference → dashboard chain can be watched end-to-end during development, before Phase 4's real live-race test.

- **D-03:** Replay script defaults to a **fast interval** (e.g. one lap every ~5s, so a full race replays in a couple minutes). Expose an `--interval` flag to slow it to realistic race pacing (~60–100s/lap) if the user wants to watch it unfold naturally.

### Dashboard Content & Driver Identity

- **D-04:** Dashboard shows the required horizontal win-probability bar chart (DASH-01) **plus** a side data table per driver with: current position, tyre compound, tyre age, and gap to leader.

- **D-05:** Bar chart bars are **color-coded by constructor/team** using official team colors (not a single/auto palette).

- **D-06 (important gap found during discussion):** Driver full names, acronyms, and official team colors are **not currently stored anywhere** in the DB — raw tables only have `driver_number`, and `team_name` is inferred indirectly (`features/builder.py::_get_team_name_from_session`). Phase 3 MUST add an OpenF1 `/v1/drivers` client method (`src/f1_prediction/ingest/openf1_client.py` already has the `_fetch` pattern for other endpoints — follow it) plus storage (a `raw_drivers` table or equivalent per-session lookup) to source `full_name`, `name_acronym`, `team_name`, and `team_colour` for the dashboard. This is a real new data source, not a formatting detail — flag it to the researcher/planner explicitly.

### Refresh Behavior

- **D-07:** In-race tab **auto-refreshes every 15 seconds** — independent of the poller's 30s interval (poller writes new data every 30s; a 15s dashboard refresh catches each update within one poll cycle without hammering SQLite reads).

- **D-08:** **No manual "refresh now" button** — auto-refresh only, for simplicity. Can be reconsidered later if 15s feels laggy in practice.

### Predictions Table & Pre-race Lifecycle

- **D-09:** Pre-race win probabilities are computed **automatically when `python -m src.f1_prediction.pipeline` starts** (using whatever qualifying/practice data exists for the latest race session), before the process moves into the live poll loop for in-race updates. No separate manual trigger script.

- **D-10:** **Single `predictions` table** with a `prediction_type` column (`'prerace'` / `'inrace'`) and a nullable `lap_number` (NULL for `prerace` rows) — one query pattern for the dashboard, and easier to extend for DASH-V2 trend lines later. Needs at minimum: `session_key`, `driver_number`, `win_probability`, `prediction_type`, `lap_number`, `computed_at`.

- **D-11:** Pre-race prediction computation is **idempotent on restart** — at pipeline startup, check whether a `'prerace'` row already exists for the current `session_key` before recomputing; skip if present. Mirrors the resumable pattern already established in Phase 1's `backfill.py` (D-11 there).

### Claude's Discretion

- Exact wiring of "inference runs after each completed lap" inside `pipeline.py` (e.g., checking `poll_once()`'s returned `laps_inserted` count before triggering feature rebuild + inference) — implementation detail, not a user-facing decision.
- Streamlit auto-refresh mechanism (`st.rerun()` loop vs `streamlit-autorefresh` component) to hit the 15s cadence from D-07.
- Exact `raw_drivers` schema / caching strategy for D-06 (table vs in-memory dict refreshed per session).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements & Roadmap
- `.planning/REQUIREMENTS.md` — DASH-01 through DASH-04 are this phase's requirements; DASH-V2-* and HOST-01 are explicitly v2/out of scope
- `.planning/ROADMAP.md` §Phase 3 — success criteria define exact entrypoints (`python -m src.f1_prediction.pipeline`, `streamlit run src/f1_prediction/dashboard.py`), port (localhost:8501), and the `predictions` table trigger contract

### Project Constraints — READ CAREFULLY, supersedes CLAUDE.md on stack choice
- `.planning/PROJECT.md` §Key Decisions — **"XGBoost + Streamlit over LightGBM + Dash"** is a locked user-preference decision (row 4 of the Key Decisions table). Phases 1–2 already built on XGBoost.
- `CLAUDE.md` — Technology Stack section is **stale on dashboard framework**: it recommends Dash and lists Streamlit under "What NOT to Use." This predates the PROJECT.md pivot. **Use Streamlit, not Dash** — ROADMAP.md, REQUIREMENTS.md, and PROJECT.md all agree on Streamlit; CLAUDE.md was never updated after the pivot. The rest of CLAUDE.md (constraints, OpenF1/FastF1 sourcing, SQLite/WAL, local-first) is still accurate.
- `.planning/research/STACK.md` — also predates the Streamlit pivot (recommends Dash for the same reasons CLAUDE.md does); read PROJECT.md as the tiebreaker.

### Prior Phase Context (integration contracts locked here)
- `.planning/phases/01-data-features/01-CONTEXT.md` — feature table schema (D-01 through D-13); notes Phase 3 reads `predictions`/`raw_sessions`/`raw_laps` for live updates
- `.planning/phases/02-model-training/02-CONTEXT.md` — model artifact paths (`data/models/prerace_model.joblib`, `data/models/inrace_model.joblib`) loaded via `joblib.load()`; feature column sets (D-03, D-04) that inference must reproduce exactly at prediction time

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/f1_prediction/db/engine.py::get_engine()` — SQLAlchemy engine + WAL mode; pipeline and dashboard both read/write through this
- `src/f1_prediction/db/upsert.py::bulk_insert_rows()` — used by the poller for batch inserts; reuse for predictions writes and the replay script
- `src/f1_prediction/live/poller.py::poll_once()` — returns a dict including `laps_inserted`; this is the natural hook point for "run inference after each completed lap" (D-09/D-11 wiring)
- `src/f1_prediction/ingest/openf1_client.py::OpenF1Client` — has `_fetch()` helper and per-endpoint methods (`laps`, `stints`, `pit`, `position`, `weather`, `intervals`); add a `drivers()` method here following the same pattern for D-06
- `scripts/build_features.py` — standalone feature builder invocation; pipeline.py likely calls the same underlying builder functions after new laps land
- `data/models/prerace_model.joblib`, `data/models/inrace_model.joblib` — trained Phase 2 artifacts to load via `joblib.load()`

### Established Patterns
- All SQL uses `text()` with bound `:param` names — no f-string SQL (security pattern from Phase 1/2, must continue)
- Per-field whitelist extraction before insert (`_LAP_FIELDS`, `_STINT_FIELDS` etc. in poller.py) — apply the same pattern for any new `/v1/drivers` fields
- Resumable/idempotent scripts check DB state before acting (backfill.py D-11, now extended to pre-race predictions via D-11 above)
- `BackgroundScheduler` (threaded, `max_instances=1`, `misfire_grace_time=15`) already running in `poller.py` — `pipeline.py` likely wraps or extends this rather than building a new scheduler

### Integration Points
- No `predictions` table exists yet in `src/f1_prediction/db/models.py` — Phase 3 creates it (schema per D-10)
- No `dashboard.py`, `pipeline.py`, or driver/team lookup exists yet — greenfield within an otherwise-populated `src/f1_prediction/` package
- Dashboard process never writes to the DB — read-only, decoupled from the pipeline process via SQLite WAL (already configured in Phase 1)
- Stale `src/f1_prediction/ml/__pycache__/` directory exists with no matching `.py` source files — leftover from an earlier Phase 2 iteration; not relevant to Phase 3, safe to ignore

</code_context>

<specifics>
## Specific Ideas

- Two-process architecture is locked by ROADMAP.md's exact entrypoints — don't merge pipeline and dashboard into one process
- Dashboard layout: bar chart (main) + side table (position/tyre/gap) + team-color coding, per D-04/D-05
- Replay script is a dev/testing tool, not a v1 requirement — but the user explicitly wants it to validate the live chain before Phase 4

</specifics>

<deferred>
## Deferred Ideas

- Manual session/race dropdown or browsing older races — rejected for v1 in favor of "always latest race" (D-01); could become a v2 feature if the user wants to review past races
- Manual "refresh now" button — rejected for v1 (D-08); reconsider if 15s auto-refresh feels laggy
- DASH-V2-01 (probability trend lines), DASH-V2-02 (driver info overlays — largely satisfied early by D-04's side table, but full overlay design deferred), DASH-V2-03 (race event annotations) — already tracked in REQUIREMENTS.md as v2, not this phase
- HOST-01 (hosted deployment) — explicitly out of scope; local-first only per PROJECT.md constraints

</deferred>

---

*Phase: 3-Prediction Pipeline & Dashboard*
*Context gathered: 2026-07-07*
