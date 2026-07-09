# Phase 1: Data & Features - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-16
**Phase:** 1-Data & Features
**Areas discussed:** Feature table schema, Practice sessions scope, Raw data column depth, Backfill strategy

---

## Feature Table Schema

| Option | Description | Selected |
|--------|-------------|----------|
| Unified table, one row per (session, driver, lap) | Pre-race features repeated on every lap row. Simple to query for training — one SELECT gives everything. | |
| Split tables: prerace + inrace | `features_prerace` (session, driver) + `features_inrace` (session, driver, lap). Clean separation matches two-model design. | ✓ |
| You decide | Claude picks based on training shape. | |

**User's choice:** Split tables: prerace + inrace

---

| Option | Description | Selected |
|--------|-------------|----------|
| CLAUDE.md list exactly | qualifying delta to pole, practice pace, circuit win history, constructor pace, championship standings, grid position | |
| Add driver age / experience | Adds `driver_seasons_in_f1` — rookie disadvantage is real. | ✓ |
| You decide | Claude picks the minimal set that covers CLAUDE.md list. | |

**User's choice:** Add driver age / experience (seasons in F1)

---

| Option | Description | Selected |
|--------|-------------|----------|
| CLAUDE.md list: gap to leader, tyre compound age, pit stop count | Minimal but sufficient for in-race model. | |
| Extended: add position, interval to car ahead/behind, pit stop delta | More context; all derivable from raw tables. Adds ~4 columns. | ✓ |
| You decide | Claude picks the minimal set from CLAUDE.md. | |

**User's choice:** Extended in-race features

---

| Option | Description | Selected |
|--------|-------------|----------|
| Compute circuit stats in Phase 1 | Pre-aggregated into features_prerace. Phase 2 reads and trains — no joins at training time. | ✓ |
| Leave as raw joins for Phase 2 | Simpler Phase 1, but Phase 2 must do SQL aggregation at training time. | |

**User's choice:** Compute in Phase 1 and store in features_prerace

---

| Option | Description | Selected |
|--------|-------------|----------|
| Store `is_winner` (bool) | Binary label — one winner per race. | |
| Store `finish_position` (int) | Phase 2 derives `is_winner = finish_position == 1`. More flexible for top-3 extensions. | ✓ |
| Leave labels out of features tables | Phase 2 joins results at training time. | |

**User's choice:** Store `finish_position` (int) in features_prerace

---

## Practice Sessions Scope

| Option | Description | Selected |
|--------|-------------|----------|
| FP2 + FP3 only | FP1 uses experimental setups. FP2 (race sim) and FP3 (qualifying prep) are most predictive. | ✓ |
| All: FP1 + FP2 + FP3 | More data; model can learn which sessions matter. | |
| You decide | Claude picks based on F1 domain knowledge. | |

**User's choice:** FP2 + FP3 only

---

| Option | Description | Selected |
|--------|-------------|----------|
| Skip sprint sessions entirely | Exclude sprint races; use only the one practice session on sprint weekends. | |
| Include sprint session as practice substitute | Use sprint race data as proxy for FP2/FP3 race pace on sprint weekends. | ✓ |
| You decide | Claude picks the simpler approach. | |

**User's choice:** Include sprint session as a practice substitute

---

| Option | Description | Selected |
|--------|-------------|----------|
| Filter at ingest: store only clean laps | 107% rule, no pit/SC laps stored. Cleaner raw table, harder to change filter later. | |
| Store all laps raw, filter at feature build time | Raw tables are a full mirror. Feature builder applies filters. Easier to iterate. | ✓ |

**User's choice:** Store all laps raw, filter at feature build time

---

## Raw Data Column Depth

| Option | Description | Selected |
|--------|-------------|----------|
| Full mirror: all OpenF1 fields | ~15 columns, max fidelity. | Claude's discretion ✓ |
| Minimal: lap_duration, sector times, gap_to_leader, position, is_pit_out_lap | Smaller table, re-backfill needed for new fields. | |
| You decide | Claude picks based on storage/re-backfill tradeoff. | ✓ |

**User's choice:** You decide (Claude chose full mirror)
**Notes:** Storage is cheap at 72K rows; re-backfilling is slow and rate-limited. Full mirror is the right call.

---

| Option | Description | Selected |
|--------|-------------|----------|
| Normalize to OpenF1 schema | Same columns for 2022–2025. One feature builder code path. | ✓ |
| Store FastF1 extras as additional columns | FastF1-specific fields preserved but requires handling two schemas. | |
| You decide | Claude picks what minimizes feature builder complexity. | |

**User's choice:** Normalize to OpenF1 schema

---

## Backfill Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Resumable: skip sessions already in DB | Check `raw_sessions` before fetching; re-runs pick up where left off. | ✓ |
| Simple sequential: always re-fetch everything | Simpler; acceptable if backfill is done once (~30-60 min). | |

**User's choice:** Resumable

---

| Option | Description | Selected |
|--------|-------------|----------|
| Fixed delay (0.5–1s sleep) | Simple and safe. Adds ~5 min to full backfill. | |
| Retry with exponential backoff on 429/timeout | No artificial delay; backs off when server signals throttling. | Claude's discretion ✓ |
| You decide | Claude picks what's most reliable for a one-time 4-season backfill. | ✓ |

**User's choice:** You decide (Claude chose exponential backoff)

---

| Option | Description | Selected |
|--------|-------------|----------|
| Separate scripts: backfill.py then build_features.py | Each script has one job; feature builder can be re-run independently. | |
| Single pipeline: backfill.py runs feature builder at the end | One command does everything. | ✓ |

**User's choice:** Single pipeline

---

## Claude's Discretion

- **Raw data column depth:** Full mirror of all OpenF1 fields — storage is cheap, re-backfilling is expensive
- **Rate limiting:** Exponential backoff on 429/timeout rather than fixed delay — more efficient for a one-time bulk run

## Deferred Ideas

- Driver biographical features beyond `driver_seasons_in_f1` (home race effect, nationality) — future Phase 2 enhancement if model underfits
- Richer weather data beyond wet/dry/mixed (exact rain probability, wind speed) — DATA-02 scope is just a session-level flag
- Real-time telemetry (throttle, braking, speed trap) — explicitly out of scope per REQUIREMENTS.md
