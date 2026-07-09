---
phase: 03-prediction-pipeline-dashboard
verified: 2026-07-08T00:00:00Z
status: passed
score: 9/9 must-haves verified
behavior_unverified: 0
overrides_applied: 0
mvp_mode_note: >
  ROADMAP.md marks this phase mode: mvp, but the phase goal text
  ("A locally running dashboard shows ranked win probabilities for all
  drivers, sourced from a prediction pipeline that fires after each
  completed race lap") is NOT in User Story format
  ("As a ..., I want to ..., so that ..."). Confirmed via
  `gsd_run query user-story.validate` returning valid=false. Per
  verify-mvp-mode.md this is surfaced as a discrepancy rather than silently
  applying MVP User Flow Coverage framing. Standard goal-backward
  verification was used instead, anchored on ROADMAP.md's five explicit
  Success Criteria (present and complete) and PLAN frontmatter must_haves
  (present in all four plans) — both of which fully cover the phase intent,
  so this did not block verification. Recommend running
  `/gsd mvp-phase 03` retroactively (or updating ROADMAP mode to null) if
  future re-verification of this phase is needed under strict MVP framing.
---

# Phase 3: Prediction Pipeline & Dashboard Verification Report

**Phase Goal:** A locally running dashboard shows ranked win probabilities for all drivers, sourced from a prediction pipeline that fires after each completed race lap
**Verified:** 2026-07-08
**Status:** passed
**Re-verification:** No — initial verification (this is the canonical goal-backward VERIFICATION.md; the phase previously went through code review (03-REVIEW.md/03-REVIEW-FIX.md), a security audit (03-SECURITY.md), and conversational UAT (03-UAT.md), but no automated goal-backward VERIFICATION.md had been produced until now).

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | After each poll cycle detects a new lap, inference runs automatically and inserts updated win probability rows into `predictions` | ✓ VERIFIED | `src/f1_prediction/pipeline.py::run_inference_after_poll` gates on `poll_result["laps_inserted"] > 0` and calls `compute_and_store_inrace_latest_lap`; `main()`'s `BackgroundScheduler` job calls `poll_once()` then `run_inference_after_poll()` every 30s. Behavioral test `tests/test_pipeline_inrace.py::test_run_inference_after_poll_gates_on_laps_inserted` run individually — **PASS**. |
| 2 | The dashboard at `localhost:8501` shows a horizontal bar chart with all drivers ranked by win probability | ✓ VERIFIED | `dashboard_lib/charts.py::build_win_prob_bar_chart` builds `go.Bar(orientation="h", ...)` sorted so highest probability renders on top, team-colored (`#`-prefixed hex). Rendered from both `render_prerace_tab` and `render_inrace_tab` in `dashboard.py`. Confirmed live by human UAT at http://localhost:8501 (03-UAT.md items 3-5, all `result: pass`). |
| 3 | The pre-race tab displays qualifying-based win probabilities computed from qualifying session data before the race starts | ✓ VERIFIED | `inference/prerace.py::compute_and_store_prerace` reads `features_prerace` (qualifying/practice-derived), reuses `predict_with_softmax`, writes `prediction_type='prerace'`. `dashboard.py::render_prerace_tab` renders it. Behavioral test `tests/test_pipeline_prerace.py::test_compute_and_store_prerace_sums_to_one` run individually — **PASS** (sums to ~1.0 per session). |
| 4 | The in-race tab auto-refreshes each lap and reflects updated probabilities without manual page reload | ✓ VERIFIED | `dashboard.py::render_inrace_tab` decorated `@st.fragment(run_every=15)` (confirmed present via `ast.parse` + string check); no "refresh now"/manual control string found. Human UAT (03-UAT.md item 4) confirms lap number climbed on its own every ~15s during a live replay session, "user confirmed: things are changing and it is working". |
| 5 | The dashboard runs with no authentication and requires only the two documented entrypoint commands | ✓ VERIFIED | `.streamlit/config.toml` pins `[server] address = "localhost"` (closes a real gap discovered during Plan 04 where Streamlit's runtime default bound `*:8501` regardless of source code — see 03-SECURITY.md T-03-02). No `0.0.0.0` string anywhere in `dashboard.py`/`pipeline.py`/`replay_race.py`/config (grep-verified this session). Human UAT confirms no login prompt at runtime. |

**Score:** 5/5 ROADMAP success criteria verified (0 present-but-behavior-unverified)

### PLAN Frontmatter Must-Haves (all 4 plans)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| P1.1 | `python3.11 -m streamlit --version` prints >= 1.37 | ✓ VERIFIED | Ran this session: `Streamlit, version 1.59.0`. |
| P1.2 | pyproject.toml declares streamlit/plotly/xgboost/scikit-learn/joblib/scipy | ✓ VERIFIED | `tomllib` parse + membership assertion, ran this session — all 6 present. |
| P1.3 | train.py + pipeline import shared feature constants/functions from one module | ✓ VERIFIED | `scripts/train.py` and `src/f1_prediction/inference/{prerace,inrace}.py` all import from `f1_prediction.inference.features` (`PRERACE_FEATURE_COLS`, `INRACE_NUMERIC_FEATURE_COLS`, `predict_with_softmax`, `predict_inrace_with_softmax`, `load_model`); no duplicate constant definitions found in train.py. |
| P1.4 | predictions/raw_drivers tables created by init_db() | ✓ VERIFIED | Ran `init_db()` against a fresh tmp DB this session — `inspect(engine).get_table_names()` includes both `predictions` and `raw_drivers`. |
| P1.5 | OpenF1Client.drivers()/fetch_drivers() exist | ✓ VERIFIED | `src/f1_prediction/ingest/openf1_client.py` — confirmed via prior test suite pass; `fetch_drivers` imported and used in `pipeline.py::sync_drivers_for_session`. |
| P2.1 | Pipeline startup writes 'prerace' rows summing to ~1.0 per session_key | ✓ VERIFIED | Named test run individually — **PASS**. |
| P2.2 | Second pipeline start does not recompute prerace (D-11) | ✓ VERIFIED | Named test `test_prerace_idempotent_on_second_startup` run individually — **PASS**. Note: code has since been hardened further (03-REVIEW-FIX WR-01) — `compute_and_store_prerace` itself is now safe to call twice via atomic DELETE-then-INSERT, not solely reliant on the external guard. |
| P2.3 | Pipeline resolves latest Race session_key with no manual flag (D-01) | ✓ VERIFIED | `src/f1_prediction/db/queries.py::get_latest_race_session_key` — no `--session-key` CLI flag exists anywhere in `pipeline.py`'s argparse (none defined). Ordering logic was corrected post-Plan-02 (see Deviations note below) and independently re-verified this session against the live project DB by UAT (03-UAT.md item 2, session ordering 9839 > 9845 > 9858... confirmed correct). |
| P2.4 | Dashboard Pre-Race tab renders ranked, team-colored bar chart | ✓ VERIFIED | Confirmed in code + human UAT (03-UAT.md item 3). |
| P2.5 | Dashboard binds to localhost only, no auth (DASH-04) | ✓ VERIFIED | See Truth 5 above. |
| P3.1 | Post-poll inference rebuilds features, slices latest lap, inserts 'inrace' rows with lap_number set | ✓ VERIFIED | `inference/inrace.py::compute_and_store_inrace_latest_lap` calls `build_features_for_session(kind='inrace')` (Pattern 1, no hand-rolled incremental join), slices `MAX(lap_number)`. Named test run individually — **PASS**. |
| P3.2 | In-race win_probability sums to ~1.0 per (session_key, lap_number) | ✓ VERIFIED | Same named test asserts this — **PASS**. |
| P3.3 | In-Race tab auto-refreshes every 15s, no manual button | ✓ VERIFIED | See Truth 4 above. |
| P3.4 | In-Race tab shows team-colored bar chart + side table (position/tyre/age/gap) | ✓ VERIFIED | `dashboard_lib/queries.py::load_inrace_predictions` joins `features_inrace` for `position`, `tyre_compound`, `tyre_age_laps`, `gap_to_leader`; `dashboard.py::render_inrace_tab` renders all four columns in an `st.dataframe`. Confirmed by human UAT. |
| P3.5 | LIVE badge + freshness line visible on In-Race tab | ✓ VERIFIED | `dashboard.py:87-92` — hardcoded LIVE badge markup (`background-color:#E10600`) + `st.caption(f"LIVE · Lap {lap_number} · updated {...}")`. Confirmed by human UAT (red LIVE badge, climbing lap number). |
| P4.1 | replay_race.py re-inserts a completed race's raw_* rows incrementally on a timer | ✓ VERIFIED | `scripts/replay_race.py` confirmed present, `--help` lists `--source-session-key`/`--target-session-key`/`--interval`/`--db`. Test suite includes 5 replay-specific tests, all passing. |
| P4.2 | Replay defaults to fast interval (~5s/lap), accepts --interval | ✓ VERIFIED | `--help` output shows `--interval SECONDS ... Default 5.0 is a fast [...]`, confirmed this session. |
| P4.3 | Replay + pipeline + dashboard together show In-Race tab updating lap-by-lap | ✓ VERIFIED | Human-verify checkpoint (03-04-PLAN.md Task 2) — explicit "approved" resume signal recorded in 03-04-SUMMARY.md and re-confirmed independently in 03-UAT.md item 5 ("user confirmed... approved for this exact checkpoint"). |

**Score:** 18/18 plan-level must-haves verified (9 ROADMAP+plan truths deduplicated to headline 9 for frontmatter score; full itemized list above).

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/f1_prediction/inference/features.py` | Shared feature/softmax module | ✓ VERIFIED | Exports all 7 symbols + `load_model`; imported by train.py, prerace.py, inrace.py |
| `.streamlit/config.toml` | Dark theme + localhost bind | ✓ VERIFIED | `[theme] base="dark"`, `[server] address="localhost"` |
| `db/models.py::Prediction` | `predictions` table, D-10 discriminator | ✓ VERIFIED | `CheckConstraint("prediction_type IN ('prerace','inrace')")` present |
| `db/models.py::RawDriver` | `raw_drivers` table, D-06 | ✓ VERIFIED | `team_colour` stored without `#` prefix per Pitfall 3 |
| `openf1_client.py::OpenF1Client.drivers`/`fetch_drivers` | `/v1/drivers` client | ✓ VERIFIED | Present, delegates to `_fetch`, inherits retry/backoff |
| `src/f1_prediction/inference/prerace.py` | Prerace inference | ✓ VERIFIED | Substantive, wired into `pipeline.py::main()` |
| `src/f1_prediction/inference/inrace.py` | In-race inference | ✓ VERIFIED | Substantive, wired into `pipeline.py::run_inference_after_poll` |
| `src/f1_prediction/pipeline.py` | Entrypoint | ✓ VERIFIED | `python -m src.f1_prediction.pipeline` guard present; wires prerace + poll loop + inrace hook |
| `src/f1_prediction/dashboard.py` | Streamlit entrypoint | ✓ VERIFIED | Two tabs, fragment auto-refresh, no writes |
| `src/f1_prediction/dashboard_lib/queries.py` | Read-only query layer | ✓ VERIFIED | All SELECT/`text()`+bound params; fresh connection per call |
| `src/f1_prediction/dashboard_lib/charts.py` | Chart builder | ✓ VERIFIED | `build_win_prob_bar_chart` produces `#`-prefixed marker colors |
| `scripts/replay_race.py` | Dev replay tool | ✓ VERIFIED | CLI flags present, dedupe-safe, SIGTERM-safe (hardened post-review) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `poll_once()` laps_inserted>0 | `compute_and_store_inrace_latest_lap` | `run_inference_after_poll` hook in `pipeline.py` | ✓ WIRED | Confirmed by source read + passing gating test |
| `pipeline.py` writes `predictions` | `dashboard.py` reads `predictions` | Shared SQLite table only, no direct process coupling | ✓ WIRED | `dashboard_lib/queries.py` never imports `pipeline.py` — imports `db/queries.py` instead, per an explicit refactor (WR-03) documented in its own docstring to avoid pulling in APScheduler/inference deps into the dashboard process |
| `raw_drivers.team_colour` | Bar chart `marker_color` | `#`-prefix added at render time | ✓ WIRED | `build_win_prob_bar_chart` confirmed to prepend `#` (verified via direct test in 03-02-SUMMARY and re-confirmed by source read this session) |
| `replay_race.py` writes `raw_laps` | `pipeline.poll_once` sees `laps_inserted>0` | Shared raw_* tables | ✓ WIRED (Plan 04 note) | Replay tool actually calls `inference.inrace.compute_and_store_inrace_latest_lap()` directly per lap (documented pattern) rather than relying solely on `pipeline.py`'s own poll loop, since the real poll loop gates on a live OpenF1 session window that a replayed historical race can never satisfy — an intentional, documented adaptation, not a broken link. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| `dashboard.py::render_prerace_tab` | `df` from `load_prerace_predictions` | `predictions` JOIN `raw_drivers`, real SQL SELECT, no static fallback except missing-driver gray | ✓ FLOWING |
| `dashboard.py::render_inrace_tab` | `df` from `load_inrace_predictions` | `predictions` JOIN `raw_drivers` JOIN `features_inrace`, real SQL SELECT | ✓ FLOWING |
| `inference/prerace.py::compute_and_store_prerace` | `df` from `features_prerace` | Real SQL SELECT against seeded/backfilled feature table | ✓ FLOWING |
| `inference/inrace.py::compute_and_store_inrace_latest_lap` | `df` from `features_inrace` (post-rebuild) | `build_features_for_session()` rebuild + real SQL SELECT | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Prerace sum-to-1 + prediction_type/lap_number contract | `pytest tests/test_pipeline_prerace.py::test_compute_and_store_prerace_sums_to_one -q` | 1 passed | ✓ PASS |
| Prerace idempotency (D-11) | `pytest tests/test_pipeline_prerace.py::test_prerace_idempotent_on_second_startup -q` | 1 passed | ✓ PASS |
| Inrace sum-to-1 per (session,lap) | `pytest tests/test_pipeline_inrace.py::test_compute_and_store_inrace_latest_lap_sums_to_one -q` | 1 passed | ✓ PASS |
| Inference hook gates on laps_inserted>0 | `pytest tests/test_pipeline_inrace.py::test_run_inference_after_poll_gates_on_laps_inserted -q` | 1 passed | ✓ PASS |
| Streamlit repaired & working | `python3.11 -m streamlit --version` | "Streamlit, version 1.59.0" | ✓ PASS |
| pyproject dependency reconciliation | `tomllib` parse + membership assert | all 6 packages present | ✓ PASS |
| Dashboard source gates (fragment/tabs/no-0.0.0.0/no-manual-refresh) | `ast.parse` + string assertions on `dashboard.py` | all assertions pass | ✓ PASS |
| replay_race.py CLI contract | `python3.11 scripts/replay_race.py --help` | `--interval` default 5.0, `--source-session-key` present | ✓ PASS |
| DB schema creation | `init_db()` + `inspect().get_table_names()` | `predictions`, `raw_drivers` present among 10 tables | ✓ PASS |
| Full test suite (single run, not filtered per-truth) | `python3.11 -m pytest tests/ -q` | **69 passed** | ✓ PASS |

### Probe Execution

Not applicable — no `scripts/*/tests/probe-*.sh` files exist in this project and neither the PLAN nor SUMMARY files reference probe-based verification. Skipped.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|--------------|-------------|--------------|--------|----------|
| DASH-01 | 03-03, 03-04 | Live win-probability bar chart, drivers ranked | ✓ SATISFIED | In-race tab renders ranked chart via `build_win_prob_bar_chart`; human UAT confirmed |
| DASH-02 | 03-02 | Pre-race tab, qualifying-based | ✓ SATISFIED | `compute_and_store_prerace` + `render_prerace_tab`; human UAT confirmed |
| DASH-03 | 03-03, 03-04 | In-race tab auto-refreshes each lap | ✓ SATISFIED | `st.fragment(run_every=15)` + post-poll inference hook; human UAT confirmed lap number climbing |
| DASH-04 | 03-01, 03-02, 03-04 | Runs locally, no auth | ✓ SATISFIED | `.streamlit/config.toml` pins localhost bind; human UAT confirmed no login prompt |

All 4 requirement IDs declared in the four plans' frontmatter (`requirements: [DASH-04]`, `[DASH-02, DASH-04]`, `[DASH-01, DASH-03]`, `[DASH-01, DASH-03]`) are accounted for and cross-referenced against REQUIREMENTS.md, which independently marks all four `[x]` complete with status "Complete" in its Phase 3 traceability table. No orphaned requirements found — REQUIREMENTS.md maps exactly DASH-01 through DASH-04 to Phase 3, and all four appear in plan frontmatter.

### Anti-Patterns Found

None. Scanned all 11 phase-modified source files (`pipeline.py`, `dashboard.py`, `inference/{prerace,inrace,features}.py`, `dashboard_lib/{queries,charts}.py`, `db/queries.py`, `db/models.py`, `ingest/openf1_client.py`, `scripts/replay_race.py`) for `TBD|FIXME|XXX|TODO|HACK|PLACEHOLDER` and "not yet implemented"/"coming soon" phrasing — zero matches. Two prior grep-gate false-positives (docstring text matching literal `"0.0.0.0"` and `"refresh now"` substrings) were identified and fixed during original execution (documented in 03-02-SUMMARY.md and 03-03-SUMMARY.md) — these were documentation wording issues caught by the plan's own verification commands, not functional stubs, and are already resolved in the current source.

### Human Verification Required

None outstanding. All items that require human/visual confirmation were already exercised and passed via 03-UAT.md's conversational UAT (5/5 passed, including a live end-to-end dashboard session the user personally confirmed at http://localhost:8501, with the In-Race tab's auto-refreshing lap counter and re-ordering bar chart observed directly) and the blocking human-verify checkpoint in 03-04-PLAN.md Task 2 (explicit "approved" resume signal). No new human-verification gaps were identified during this goal-backward pass.

### Gaps Summary

No gaps found. All 5 ROADMAP success criteria and all 4 requirement IDs (DASH-01 through DASH-04) are substantively implemented, wired end-to-end, covered by passing named behavioral tests (run individually, not just enumerated), and confirmed via a full local test suite run (69/69 passing) plus a previously-completed conversational UAT and security audit. The one process irregularity — ROADMAP.md's `mode: mvp` tag without a matching User-Story-formatted goal — is noted above as a `mvp_mode_note` but does not constitute a phase-goal gap; it is a planning-artifact inconsistency worth fixing prospectively (e.g., via `/gsd mvp-phase 03` or clearing the mode flag), not evidence the phase's actual deliverable is incomplete.

Two data-quality items are logged in `deferred-items.md` (session 9839's `raw_positions` table is empty; a `pip install -e .` setup-note gap for fresh checkouts) — both are pre-existing/out-of-phase-scope backfill and documentation gaps, not Phase 3 code defects, and were already correctly deferred rather than treated as blocking gaps.

---

_Verified: 2026-07-08_
_Verifier: Claude (gsd-verifier)_
