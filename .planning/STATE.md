---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
current_phase: 4
current_phase_name: Live Race Integration
status: verifying
stopped_at: Phase 4 context gathered
last_updated: "2026-07-09T05:17:52.518Z"
last_activity: 2026-07-09
last_activity_desc: Phase 03 complete, transitioned to Phase 4
progress:
  total_phases: 4
  completed_phases: 3
  total_plans: 11
  completed_plans: 11
  percent: 75
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-16)

**Core value:** Show accurate, live-updating win probabilities for every driver during an F1 race weekend
**Current focus:** Phase 03 — prediction-pipeline-dashboard

## Current Position

Phase: 4 — Live Race Integration
Plan: Not started
Status: Phase complete — ready for verification
Last activity: 2026-07-09 — Phase 03 complete, transitioned to Phase 4

Progress: [██████████] 100% (Phase 01 complete and verified)

## Performance Metrics

**Velocity:**

- Total plans completed: 8
- Average duration: ~15.7 min
- Total execution time: ~47 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-data-features | 3/3 | ~47 min | ~15.7 min |
| 02 | 4 | - | - |
| 03 | 4 | - | - |

**Recent Trend:**

- Last 5 plans: 01-01 (9 min), 01-02 (8 min), 01-03 (30 min)
- Trend: stable for auto tasks; TDD task longer as expected

*Updated after each plan completion*
| Phase 02 P01 | 480 | - tasks | - files |
| Phase 02 P02 | 30 | 3 tasks | 3 files |
| Phase 02 P03 | 20 | 3 tasks | 3 files |
| Phase 02 P04 | 13 | 3 tasks | 2 files |
| Phase 03 P01 | 4 | 3 tasks | 7 files |
| Phase 03 P02 | 8min | 3 tasks | 7 files |
| Phase 03 P03 | 10min | 3 tasks | 4 files |
| Phase 03 P04 | 25min | 2 tasks | 5 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap: FastF1 for 2022 historical data; OpenF1 for 2023–2025 and live race polling (OpenF1 only covers 2023+)
- Roadmap: LightGBM chosen over XGBoost — native categorical support for driver/team/circuit columns, better on small high-cardinality tabular data
- Roadmap: Dash (Plotly) chosen over Streamlit — callback-based refresh avoids full script re-run on 10s interval updates
- Roadmap: SQLite with WAL mode for concurrent poller writes + dashboard reads
- 01-01: session_key synthesis for FastF1 2022 data: year*100_000 + round*100 + session_type_code (1-7)
- 01-01: WAL mode via SQLAlchemy event listener on connect — no per-query PRAGMA needed
- 01-01: System Python 3.11 used (venv has stub dist-info directories without source files)
- 01-02: RateLimitedError on HTTP 429 inside _fetch body — cleaner than retry_if_result predicate for tenacity
- 01-02: FastF1 stints derived from consecutive same-Compound laps; tyre_age_at_start = TyreLife-1 (1-based)
- 01-02: Stints/pits/positions ingested only for race-pace sessions (R, S); Q/FP2/FP3 get laps+weather only
- 01-02: MockTransport uses httpx.AsyncBaseTransport for async OpenF1Client test coverage
- 01-03: grid_position approximated from lap-1 raw_positions (FastF1 GridPosition not in schema)
- 01-03: gap_to_leader approximated via cumsum(lap_duration) for historical data; live uses /v1/intervals
- 01-03: poll_once accepts _is_active and _client injection params for test isolation
- 01-03: team_name in features_prerace looked up from prior features_prerace rows; NULL for debut races
- [Phase ?]: OpenF1 /pit returns HTTP 404 for no-pit-stop sessions — treated as empty list
- [Phase ?]: UniqueConstraint on (year, round_number, session_type) removed via idempotent table-recreation migration
- [Phase ?]: round_number derived from meeting_key rank-within-year for 2023+ sessions
- [Phase ?]: Plan 02-03: encode_tyre_compound reindexes to canonical TYRE_COMPOUND_VALUES — absent compounds produce zero columns for stable model input schema
- [Phase ?]: Plan 02-03: per-(session_key, lap_number) softmax applied in predict_inrace_with_softmax — each lap is a separate INRACE-02 inference event
- [Phase ?]: Plan 02-03: scale_pos_weight=17.49 computed from in-race imbalance (1988 winners per 36756 lap rows)
- 02-04: _assert_sums_within_tolerance extracted as module-level helper — violation path directly testable without mocking softmax
- 02-04: validate_sum_to_one accepts raw_proba + group_keys args (not a model) — math testable without DB or model load
- 03-01: Upgraded streamlit/plotly via pip install --upgrade (not pinning altair<5) — resolves altair to compatible 5.x and unlocks st.fragment(run_every=...) needed by later plans
- 03-01: Kept only fetch_drivers (not drivers) in openf1_client.py's __all__ — drivers is an instance method, not a module-level name; matches existing convention where stints/pit/laps/position/weather/intervals methods are also absent from __all__
- [Phase ?]: 03-02: get_latest_race_session_key orders by session_key DESC alone (not year/round_number) — round_number is only reliably populated after backfill.py's fix_round_numbers() step
- [Phase ?]: 03-02: dashboard_lib/queries.py imports pipeline.get_latest_race_session_key rather than duplicating the D-01 query — single source of truth shared by both processes
- [Phase ?]: 03-02: load_prerace_predictions LEFT JOINs raw_drivers with a driver_number/gray-#888888 fallback for unmatched drivers, instead of hiding them or erroring
- [Phase 03]: 03-03: inrace.py reindexes lap_number only once — it is already one of INRACE_NUMERIC_FEATURE_COLS's 9 columns; double-selecting it produced a 15-wide feature matrix and an XGBoost shape-mismatch error
- [Phase 03]: 03-03: pipeline.py runs a single BackgroundScheduler job (poll_once then run_inference_after_poll) rather than a second scheduler, reusing live/poller.py's max_instances=1/misfire_grace_time=15/EVENT_JOB_ERROR conventions
- [Phase 03]: 03-03: compute_and_store_inrace_latest_lap DELETE-then-INSERTs per (session_key, lap_number) to keep re-runs of the same lap idempotent
- [Phase 03-04]: get_latest_race_session_key orders by date_start DESC not session_key DESC — fixes stale 2022 race resolution (2022 FastF1 session_keys numerically exceed 2023+ OpenF1 session_keys)
- [Phase 03-04]: .streamlit/config.toml pins [server] address=localhost — streamlit's actual runtime default under --server.headless true binds all interfaces despite docstring claiming localhost-only
- [Phase 03-04]: replay_race.py in-place replay stages a full backup under session_key+900_000_000_000 before clearing/rebuilding, with restore-on-interrupt, so Ctrl-C never leaves the source race incomplete

### Pending Todos

None.

### Blockers/Concerns

- OpenF1 free tier may have higher latency for live sessions; sponsor tier (€9.90/month) may be needed on race weekends — validate in Phase 4
- Phase 02 must handle NULL values in features_prerace: practice_pace_fp3 NULL on sprint weekends, championship_position NULL for season opener, team_name NULL for debut races

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| grid_position accuracy | Use FastF1 GridPosition from session.results instead of lap-1 approximation | Phase 4 | 01-03 |
| Sprint weekend FP3 | practice_pace_fp3 is NULL on sprint weekends — Phase 2 model must handle NULL in training | Phase 2 | 01-03 |
| team_name for debut races | NULL for drivers' first race — Phase 2 categorical encoder must handle None | Phase 2 | 01-03 |
| OpenF1 intervals for live gap_to_leader | poll_once uses cumulative lap times for gap; /v1/intervals endpoint available but not yet fetched/stored | Phase 3 | 01-03 |

## Session Continuity

Last session: 2026-07-09T05:17:52.512Z
Stopped at: Phase 4 context gathered
Resume file: .planning/phases/04-live-race-integration/04-CONTEXT.md

## Phase 02 Plan 02 Decisions

- OpenF1 raw_positions have lap_number=0 (API does not return lap number); is_winner SQL uses OR clause to trust finish_position when max_lap=0 (no lap data)
- train_prerace_model exposes cv=5 as default param; production code passes cv=5 explicitly; tests use cv=2 for small fixtures
- scale_pos_weight=18.74 for 1:19 class imbalance (XGBoost parameter, not is_unbalance which is LightGBM-only)
