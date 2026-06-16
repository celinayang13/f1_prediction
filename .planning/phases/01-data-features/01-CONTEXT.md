# Phase 1: Data & Features - Context

**Gathered:** 2026-06-16
**Status:** Ready for planning

<domain>
## Phase Boundary

Build the data pipeline: fetch and store all historical F1 session data (2022–2025 race, qualifying, FP2+FP3 practice) into SQLite raw tables, run a live poller during race weekends, and produce populated `features_prerace` and `features_inrace` tables ready for model training in Phase 2.

**Not in scope:** model training, inference, dashboard, live prediction loop.

</domain>

<decisions>
## Implementation Decisions

### Feature Table Schema

- **D-01:** Two separate feature tables — `features_prerace` (one row per session_key + driver_number) and `features_inrace` (one row per session_key + driver_number + lap_number). Single unified table rejected — pre-race features have no lap number and repeating them on every lap row adds unnecessary redundancy.

- **D-02:** `features_prerace` columns (Phase 2 pre-race model inputs):
  - `qualifying_delta_to_pole` — gap in seconds to P1 qualifier
  - `practice_pace_fp2` — representative lap time from FP2 vs field median
  - `practice_pace_fp3` — representative lap time from FP3 vs field median
  - `circuit_win_rate` — driver's historical win rate at this circuit (pre-computed, stored here)
  - `circuit_avg_finish` — constructor's average finishing position at this circuit last 2 seasons (pre-computed, stored here)
  - `constructor_pace_delta` — constructor pace relative to field at this circuit (last 2 seasons)
  - `championship_position` — driver standings position at race weekend
  - `grid_position` — starting grid position
  - `driver_seasons_in_f1` — seasons in F1 (experience proxy; rookie disadvantage is real)
  - `finish_position` (int) — target label; `is_winner = finish_position == 1` derived at training time in Phase 2

- **D-03:** `features_inrace` columns (Phase 2 in-race model inputs) — extended set:
  - `gap_to_leader` — current lap gap in seconds
  - `tyre_compound` — compound type (SOFT/MEDIUM/HARD/INTER/WET)
  - `tyre_age_laps` — laps on current set
  - `pit_stop_count` — total pits taken so far
  - `position` — current race position
  - `interval_ahead` — gap to car immediately ahead (seconds)
  - `interval_behind` — gap to car immediately behind (seconds)
  - `pit_stop_delta` — time lost in most recent pit stop
  - `lap_number` — current lap
  - `laps_remaining` — total laps minus current lap

- **D-04:** Circuit-specific stats (`circuit_win_rate`, `circuit_avg_finish`, `constructor_pace_delta`) are **pre-computed in Phase 1** and stored in `features_prerace`. Phase 2 reads directly — no SQL aggregation at training time.

- **D-05:** Target label stored as `finish_position` (int) in `features_prerace` (not a boolean `is_winner`). Rationale: more flexible — Phase 2 or future phases can derive top-3 from the same column without schema changes.

### Practice Sessions Scope

- **D-06:** Backfill **FP2 and FP3 only** — FP1 uses experimental setups and is less predictive of race pace. Skipped at ingest.

- **D-07:** Sprint weekends — **include the sprint session as a practice substitute**. On sprint weekends where FP2/FP3 don't exist, use the sprint race data to proxy race-pace information. ETL must detect session type and route accordingly.

- **D-08:** Store all laps **raw with no filtering at ingest**. Outlaps, inlaps, SC-affected laps, and invalid laps are stored in `raw_laps`. Filtering (e.g., exclude laps where `is_pit_out_lap=True`, apply 107% rule) happens in the feature builder when computing practice pace features. This keeps raw tables as a faithful mirror and lets filter logic evolve without re-backfilling.

### Raw Data Column Depth

- **D-09 (Claude's discretion):** `raw_laps` stores **all fields** returned by the OpenF1 laps endpoint (~15 columns). Rationale: at 72K rows, storage cost is negligible; re-backfilling is slow and rate-limited. Full mirror avoids needing a re-run if a feature requires a field not previously stored.

- **D-10:** FastF1 (2022 data) is **normalized to the OpenF1 schema** at ingest. FastF1-specific columns (e.g., `IsAccurate`, telemetry-derived fields not present in OpenF1) are dropped. Result: `raw_laps` has identical columns for all seasons 2022–2025, one code path in the feature builder.

### Backfill Strategy

- **D-11:** `scripts/backfill.py` is **resumable**. Before fetching each session, it checks whether rows for that `session_key` already exist in `raw_sessions`. If they do, that session is skipped. A re-run after failure or interruption picks up where it left off.

- **D-12 (Claude's discretion):** Rate limiting via **retry with exponential backoff** on HTTP 429 or timeout — no artificial fixed delay. Runs at full speed; backs off only when the server signals throttling. More efficient than a blanket sleep for a one-time 4-season backfill.

- **D-13:** **Single pipeline** — `backfill.py` triggers the feature builder automatically at the end of a successful run. Planner should also expose a `--skip-features` flag (or standalone `build_features.py`) so the feature builder can be re-run independently when feature logic changes without re-fetching raw data.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` — DATA-01 through DATA-04 are the requirements for this phase; see Traceability table for phase mapping

### Project Constraints
- `.planning/PROJECT.md` — Key Decisions table (data source split, training window, SQL storage choice) and Constraints section (OpenF1 only, local-first, Python, 2022–2025)
- `CLAUDE.md` — Full technology stack with versions, rationale for each choice, and "What NOT to Use" table; the feature column list in the ML Framework section is authoritative

### Roadmap
- `.planning/ROADMAP.md` §Phase 1 — Success criteria define the exact table names and what "done" means for this phase; must be read before planning

No external ADRs or design documents yet — all decisions captured above.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- None — `src/`, `scripts/`, and `data/` directories are currently empty. Phase 1 is greenfield.

### Established Patterns
- Python venv is set up; all packages in `CLAUDE.md` Technology Stack are available (fastf1 3.8.3, httpx 0.28.1, SQLAlchemy 2.0.51, APScheduler 3.11.2, etc.)
- `data/cache/` directory exists — FastF1's built-in file cache should point here to avoid re-fetching session data during development

### Integration Points
- Phase 2 (Model Training) reads `features_prerace` and `features_inrace` directly from SQLite; schema decisions here lock Phase 2's training interface
- Phase 3 (Dashboard) reads `predictions` table written by the inference loop; `raw_sessions` and `raw_laps` tables feed live updates during race weekends

</code_context>

<specifics>
## Specific Ideas

- FastF1 cache should be configured to write to `data/cache/` (existing directory) to avoid re-downloading session data across runs
- The live poller (APScheduler `BackgroundScheduler`) should insert into the same `raw_laps`, `raw_positions`, etc. tables that the backfill populates — same schema, same ingest code path
- Sprint session detection: OpenF1 session type field is `"Sprint"` or `"Sprint Shootout"` — ETL should check `session_type` from the sessions endpoint and route to the sprint-as-practice substitution logic

</specifics>

<deferred>
## Deferred Ideas

- Driver age / biographical features beyond `driver_seasons_in_f1` (e.g., home race effect, nationality) — Phase 2 can add if the model underfits
- Weather integration beyond wet/dry/mixed flag (e.g., exact rain probability, wind speed) — DATA-02 is just a wet/dry/mixed flag per session; richer weather is a future enhancement
- Real-time telemetry fields (throttle, braking, speed at specific points) — explicitly out of scope per REQUIREMENTS.md

None — discussion stayed within phase scope otherwise.

</deferred>

---

*Phase: 1-Data & Features*
*Context gathered: 2026-06-16*
