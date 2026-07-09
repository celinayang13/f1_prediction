# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — MVP

**Shipped:** 2026-07-09
**Phases:** 4 | **Plans:** 12 | **Timeline:** 23 days (2026-06-16 → 2026-07-09)

### What Was Built
- Full historical data substrate: FastF1 (2022) + OpenF1 (2023–2025) ingest into 6 raw SQLite tables (sessions, laps, stints, pit stops, positions, weather), plus an APScheduler live poller.
- Two feature tables (`features_prerace`, `features_inrace`) built from SQL joins over the raw tables.
- Two calibrated XGBoost models (pre-race, in-race) — both produce softmax win-probability distributions that sum to 1.0 within 1e-6 tolerance, validated on held-out 2024 data.
- A prediction pipeline (`pipeline.py`) that computes pre-race probabilities at startup and re-runs in-race inference after every polled lap.
- A Streamlit dashboard (localhost:8501, no auth) with a Pre-Race tab and an auto-refreshing (15s) In-Race tab.
- A replay tool (`scripts/replay_race.py`) that let Phase 4 validate the entire chain end-to-end against a real historical race (72 laps, zero crashes, zero SQLite lock contention) without needing to wait for an actual live race weekend.

### What Worked
- **Rebuild-then-slice pattern** for in-race features (rebuild the whole `features_inrace` table via the same function used for training, then slice to the latest lap) avoided writing a second, divergent incremental-feature code path — one function serves both training and live inference.
- **Shared feature/softmax module** (`inference/features.py`) extracted in Phase 3 eliminated constant duplication risk between `train.py` and the two inference modules — verified directly by the integration checker with zero orphaned imports.
- **Replay-based validation** (Phase 4) substituted for waiting on an actual race weekend — it exercised the real code paths (same inference functions, same DB, same dashboard) against real historical data, catching a genuine bug (Streamlit's runtime default binding `0.0.0.0` despite doc claims) that a purely-code review would have missed.
- **Documenting scope narrowing upfront** (e.g., Phase 4's D-01 deciding to defer real-live-session testing, Phase 2's D-03 narrowing pre-race features) meant verification could distinguish "intentional accepted deviation" from "gap" cleanly, rather than re-litigating scope at milestone audit time.

### What Was Inefficient
- `constructor_pace_delta` was added to the `features_prerace` schema in Phase 1 but never populated by the feature builder, and Phase 2 trained without noticing until verification caught it. A quick "does every schema column have a populating writer" check during Phase 1→2 handoff would have caught this earlier.
- `raw_weather` was built and tested in Phase 1 but never consumed by any later phase — data collected with no clear consumer. Worth confirming a feature has a downstream reader before investing in ingesting it, or explicitly flagging it as "speculative, may not be used."
- Several early-phase SUMMARY.md files (Phase 1, Phase 2 — 7 files total) predate the `requirements-completed` frontmatter convention introduced in Phase 3, which meant the milestone audit's automated 3-source cross-reference had to fall back to manually reading each phase's VERIFICATION.md Requirements Coverage table instead of a fast frontmatter scan.

### Patterns Established
- Rebuild-then-slice for any "recompute derived state incrementally" problem — prefer re-deriving from source-of-truth tables over hand-rolled incremental deltas, when volume permits.
- A dedicated replay/dev-harness script (`replay_race.py`) is the standard way to validate real-time systems against historical data before a live event is available to test against.
- Explicit, dated, written scope-narrowing decisions (in CONTEXT.md, surfaced in VERIFICATION.md) let audits confirm "accepted deviation" vs. "gap" without re-deciding scope after the fact.

### Key Lessons
1. When a feature column is added to a schema, verify in the same phase (or explicitly defer with a note) that something actually populates it — schema presence is not the same as data presence.
2. Data ingestion work should name its consumer before being scheduled, or be explicitly flagged as speculative/future-use, so verification doesn't have to guess whether "unused" is a gap or by design.
3. Adopt new tracking conventions (like `requirements-completed` frontmatter) retroactively is lower-priority than getting the phase shipped — the underlying VERIFICATION.md tables are a sufficient audit trail even without it.

### Cost Observations
- Sessions: not tracked at plan granularity in this project's STATE.md; velocity data available for Phase 1 (~15.7 min/plan avg) and partial for later phases.
- Notable: Phase 4 required zero new source code — its entire value was a validation report (`VALIDATION.md`) built by exercising already-shipped Phase 1–3 code against real historical data, which kept it fast (~25 min) relative to feature-building phases.

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Phases | Plans | Key Change |
|-----------|--------|-------|------------|
| v1.0 | 4 | 12 | First milestone — established rebuild-then-slice pattern, replay-based live-system validation, and scope-narrowing-as-documented-decision practice. |

### Cumulative Quality

| Milestone | Tests | Zero-Dep Additions |
|-----------|-------|---------------------|
| v1.0 | 69 passing | replay_race.py (dev-only, no new runtime deps) |

### Top Lessons (Verified Across Milestones)

1. Schema columns need a confirmed writer before being trusted as model inputs — verify at the phase boundary, not at final audit.
