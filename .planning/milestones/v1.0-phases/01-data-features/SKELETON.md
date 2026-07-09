# Walking Skeleton — F1 Race Winner Prediction

**Phase:** 1
**Generated:** 2026-06-16

## Capability Proven End-to-End

A developer can run `python scripts/backfill.py --year 2022 --round 1` and see one real 2022 race's laps and results land in `data/f1.db` via FastF1, with `pytest tests/test_skeleton.py` confirming a non-zero row count in `raw_laps` and `raw_sessions`.

This proves the full lower stack of the F1 prediction system: source library (FastF1) → ingest module → schema (SQLAlchemy ORM, WAL mode) → SQLite file on disk → query that data back. Every subsequent Phase 1 task and every subsequent phase builds on these exact decisions without altering them.

## Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language / runtime | Python 3.11 (already in `.venv`) | Already set up; FastF1 is Python-only |
| Source layout | `src/f1_prediction/` (single package) | Existing `src/f1_poller/` is empty stub; consolidate under one package. `f1_poller/` directory will be deleted in Plan 01 — see READ_FIRST. |
| Data source split | FastF1 (year ≤ 2022) → OpenF1 (year ≥ 2023) | Locked by ROADMAP / D-10. OpenF1 has no 2022 data; FastF1 wraps jolpica for 2022 |
| Storage | SQLite at `data/f1.db`, WAL mode via SQLAlchemy event listener | CLAUDE.md decision; WAL allows concurrent poller writes + dashboard reads in Phase 3 |
| ORM | SQLAlchemy 2.0 (`DeclarativeBase`, `select()`) — no legacy `Query` API | Migration path if schema evolves; modern 2.0 style is canonical |
| HTTP client | httpx 0.28.1 (async) for OpenF1; FastF1 uses its own (requests + requests-cache) | Async client lets poller use a single client; do not call `requests` directly |
| Retry strategy | tenacity 9.1.4 with `wait_exponential` + `stop_after_attempt(6)` | D-12; handles OpenF1 429s and timeouts uniformly |
| Live poller | APScheduler 3.11.2 `BackgroundScheduler`, `max_instances=1`, `misfire_grace_time=15` | Runs alongside future FastAPI lifespan in Phase 3 without async coupling |
| FastF1 cache | `data/cache/` directory, enabled once at process start | Avoids re-downloads; directory already exists |
| Schema | One canonical OpenF1-shaped raw schema; FastF1 rows normalized to it at ingest | D-10; one feature-builder code path for all seasons |
| Feature tables | Two: `features_prerace` (session+driver), `features_inrace` (session+driver+lap) | D-01; pre-race columns are not repeated per-lap |
| Target label | `finish_position INT` in `features_prerace` (not `is_winner` boolean) | D-05; future flexibility for top-3 prediction |
| Test runner | pytest (to be installed in Plan 01) | Standard Python test runner; needed for verification across all phases |
| Directory layout | `src/f1_prediction/{db,ingest,features}/` + `scripts/{backfill,live_poller,build_features}.py` | Mirrors RESEARCH.md recommended structure; matches Architectural Responsibility Map |

## Stack Touched in Phase 1

- [x] Project scaffold — `pyproject.toml`, package init, pytest config, install missing deps (httpx, sqlalchemy, tenacity, pytest)
- [x] Routing — N/A in Phase 1 (no HTTP server until Phase 3); replaced by CLI entrypoints under `scripts/`
- [x] Database — real write (raw_laps, raw_sessions, raw_stints, raw_pit_stops, raw_positions, raw_weather, features_prerace, features_inrace) AND real read (feature builder + skeleton test reads back)
- [x] UI — N/A in Phase 1 (dashboard is Phase 3); replaced by CLI script outputs + `python -m sqlite3 data/f1.db` query in verification
- [x] Deployment — fully local; documented via `python scripts/backfill.py --year 2022 --round 1` and `python scripts/live_poller.py` commands in the phase README

## Out of Scope (Deferred to Later Slices)

- **Model training** — Phase 2 reads `features_prerace` / `features_inrace` directly; Phase 1 produces no model artifacts.
- **Inference loop** — Phase 3 wires inference into the poller; Phase 1's poller only writes raw rows.
- **Dashboard** — Phase 3.
- **Live race integration test** — Phase 4.
- **Driver biographical features beyond `driver_seasons_in_f1`** — CONTEXT.md `<deferred>`; explicit defer.
- **Granular weather beyond wet/dry/mixed flag** — DATA-02 only requires the flag; richer weather is a v2 enhancement.
- **Real-time telemetry (throttle, brake, speed at point)** — out of scope per REQUIREMENTS.md.
- **Filtering laps at ingest** — D-08; all laps stored raw, filtering happens in feature builder.
- **FP1 ingestion** — D-06; FP2/FP3 only.
- **Cross-circuit `total_laps` lookup table for live races** — researched as Pitfall 8 but not required for Phase 1 since `MAX(lap_number)` from completed races and FastF1 `session.event` metadata cover the historical case. Live-race total laps will be addressed in Phase 4.

## Subsequent Slice Plan

Each later phase adds vertical capability on top of this skeleton without altering its architectural decisions (schema names, source split, WAL mode, ORM choice, package layout):

- **Phase 2 — Model Training:** Read `features_prerace` / `features_inrace`; train LightGBM pre-race + in-race models; serialize to `data/models/*.joblib`. Adds `src/f1_prediction/models/` module. Does not modify raw or feature tables.
- **Phase 3 — Prediction Pipeline & Dashboard:** Adds FastAPI app + Dash dashboard; wires APScheduler job into FastAPI lifespan; reads `predictions` table written by inference. Adds `src/f1_prediction/{api,dashboard,inference}/` modules. Does not modify Phase 1 schema except adding a `predictions` table.
- **Phase 4 — Live Race Integration:** End-to-end validation against a real race weekend. No new code modules — exercises the runtime path Phases 1–3 produced. May add a circuit→total_laps lookup file for live-race `laps_remaining`.
