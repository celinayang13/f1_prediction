# Phase 3: Prediction Pipeline & Dashboard - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-07
**Phase:** 3-Prediction Pipeline & Dashboard
**Areas discussed:** Session selection & replay, Dashboard content scope, In-race refresh cadence, Pre-race predictions lifecycle

---

## Session selection & replay

| Option | Description | Selected |
|--------|-------------|----------|
| Always latest race in DB | Auto-detect most recent session_key with data — zero config | ✓ |
| Dropdown selector in the dashboard | Pick any race from a dropdown | |
| CLI arg at pipeline startup | `--session-key` flag at launch | |

**User's choice:** Always latest race in DB
**Notes:** Matches how Phase 4's real live race weekend will actually work — only ever one "current" race.

| Option | Description | Selected |
|--------|-------------|----------|
| Build a replay script | Re-play a historical race's laps into the DB at intervals to exercise the pipeline before Phase 4 | ✓ |
| Static historical race is enough | Point dashboard at a fully-backfilled race, skip replay tooling | |

**User's choice:** Build a replay script
**Notes:** Phase 4 is the only environment that exercises the real-time path end to end per ROADMAP — but the user wants to watch the dashboard update live during Phase 3 development too.

| Option | Description | Selected |
|--------|-------------|----------|
| Configurable interval, default fast | ~5s/lap by default, full race replays in minutes | ✓ |
| Realistic pacing only | ~60-100s/lap, takes as long as a real race | |

**User's choice:** Configurable interval, default fast
**Notes:** `--interval` flag can slow it to realistic pacing if desired.

---

## Dashboard content scope

| Option | Description | Selected |
|--------|-------------|----------|
| Bar chart + data table | Win-probability bars plus position/tyre/gap side table | ✓ |
| Bar chart only | Minimal, just driver + probability | |

**User's choice:** Bar chart + data table
**Notes:** Matches the original STACK.md dashboard layout research (top: race/lap/status, main: bar chart, side: data table).

| Option | Description | Selected |
|--------|-------------|----------|
| Team colors | Bars colored by constructor | ✓ |
| Single/auto color | Streamlit default palette | |

**User's choice:** Team colors
**Notes:** —

| Option | Description | Selected |
|--------|-------------|----------|
| Fetch OpenF1 /v1/drivers | New client method + storage for full_name, name_acronym, team_colour | ✓ |
| Hardcoded mapping in code | Static dict, needs manual updates if grid changes | |

**User's choice:** Fetch OpenF1 /v1/drivers
**Notes:** Discovered during discussion that driver names/team colors aren't stored anywhere yet — only `driver_number` and an inferred `team_name`. Flagged as a real gap, not just a formatting detail.

---

## In-race refresh cadence

| Option | Description | Selected |
|--------|-------------|----------|
| 15 seconds | Faster than poller's 30s interval, catches updates within one poll cycle | ✓ |
| Match poller exactly (30s) | Same interval as the poller | |
| Let me specify a different value | Custom | |

**User's choice:** 15 seconds
**Notes:** —

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-refresh only | No manual button, simpler UI | ✓ |
| Add a manual refresh button too | Force update on demand | |

**User's choice:** Auto-refresh only
**Notes:** Can reconsider later if 15s feels laggy.

---

## Pre-race predictions lifecycle

| Option | Description | Selected |
|--------|-------------|----------|
| Automatically at pipeline startup | Computed once when `python -m src.f1_prediction.pipeline` launches | ✓ |
| Manual trigger via separate script | Explicit step after qualifying finishes | |

**User's choice:** Automatically at pipeline startup
**Notes:** —

| Option | Description | Selected |
|--------|-------------|----------|
| One table, prediction_type column | Single `predictions` table, nullable lap_number for prerace rows | ✓ |
| Two separate tables | `predictions_prerace` / `predictions_inrace` | |

**User's choice:** One table, prediction_type column
**Notes:** Easier to extend for DASH-V2 trend lines later; one query pattern for the dashboard.

| Option | Description | Selected |
|--------|-------------|----------|
| Skip if already computed | Idempotent restart, check predictions table first | ✓ |
| Always recompute on startup | Overwrite every launch | |

**User's choice:** Skip if already computed
**Notes:** Mirrors the resumable pattern from Phase 1's backfill.py (D-11).

---

## Claude's Discretion

- Exact wiring of "inference runs after each completed lap" inside `pipeline.py` (checking `poll_once()`'s `laps_inserted` count before triggering rebuild + inference)
- Streamlit auto-refresh mechanism (`st.rerun()` loop vs `streamlit-autorefresh` component)
- Exact `raw_drivers` schema / caching strategy (table vs in-memory dict refreshed per session)

## Deferred Ideas

- Manual session/race dropdown or browsing older races — v1 uses "always latest race" instead
- Manual "refresh now" button — rejected for v1
- DASH-V2-01/02/03 (trend lines, driver overlays, race event annotations) — already tracked in REQUIREMENTS.md as v2
- HOST-01 (hosted deployment) — explicitly out of scope, local-first only
