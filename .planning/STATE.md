---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
current_phase: 02
current_phase_name: model-training
status: executing
stopped_at: Completed 02-01-PLAN.md
last_updated: "2026-07-02T20:13:44.218Z"
last_activity: 2026-06-25
last_activity_desc: Phase 02 execution started
progress:
  total_phases: 4
  completed_phases: 1
  total_plans: 7
  completed_plans: 5
  percent: 25
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-16)

**Core value:** Show accurate, live-updating win probabilities for every driver during an F1 race weekend
**Current focus:** Phase 02 — model-training

## Current Position

Phase: 02 (model-training) — EXECUTING
Plan: 3 of 4
Status: Ready to execute
Last activity: 2026-06-25 — Phase 02 execution started

Progress: [██████████] 100% (Phase 01 complete and verified)

## Performance Metrics

**Velocity:**

- Total plans completed: 3
- Average duration: ~15.7 min
- Total execution time: ~47 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-data-features | 3/3 | ~47 min | ~15.7 min |

**Recent Trend:**

- Last 5 plans: 01-01 (9 min), 01-02 (8 min), 01-03 (30 min)
- Trend: stable for auto tasks; TDD task longer as expected

*Updated after each plan completion*
| Phase 02 P01 | 480 | - tasks | - files |
| Phase 02 P02 | 30 | 3 tasks | 3 files |

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

Last session: 2026-07-02T20:10:32.431Z
Stopped at: Completed 02-02-PLAN.md (pre-race model)
Resume file: None

## Phase 02 Plan 02 Decisions

- OpenF1 raw_positions have lap_number=0 (API does not return lap number); is_winner SQL uses OR clause to trust finish_position when max_lap=0 (no lap data)
- train_prerace_model exposes cv=5 as default param; production code passes cv=5 explicitly; tests use cv=2 for small fixtures
- scale_pos_weight=18.74 for 1:19 class imbalance (XGBoost parameter, not is_unbalance which is LightGBM-only)
