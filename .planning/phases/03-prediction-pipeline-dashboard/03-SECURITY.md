---
phase: 03-prediction-pipeline-dashboard
audited: 2026-07-08
asvs_level: 1
block_on: high
threats_total: 11
threats_closed: 11
threats_open: 0
---

# Phase 03: Security Audit — Prediction Pipeline & Dashboard

**Audited:** 2026-07-08
**ASVS Level:** 1 (grep-level presence verification — mitigation pattern must be present in the cited file)
**Block threshold:** high (open high/critical severity threats would block ship)

## Result: SECURED — 11/11 threats closed, 0 open

All 11 threats declared across the four phase plans (03-01 through 03-04) were verified against actual implementation code, not documentation or SUMMARY.md claims. One severity=high threat (T-03-02) was found to have been genuinely VIOLATED at the source-code level the plan's own grep gate was designed to catch — a Streamlit runtime default binding `*:8501` despite `dashboard.py` claiming a localhost-only default — and was independently confirmed fixed via `.streamlit/config.toml`, not just re-trusted from the SUMMARY narrative.

## Threat Verification

| Threat ID | Category | Severity | Disposition | Status | Evidence |
|-----------|----------|----------|-------------|--------|----------|
| T-03-01 | Tampering | medium | mitigate | CLOSED | `src/f1_prediction/pipeline.py:52-55` — `_DRIVER_FIELDS` whitelist (`session_key, driver_number, full_name, name_acronym, team_name, team_colour`); `sync_drivers_for_session` (line 116) does `{f: d.get(f) for f in _DRIVER_FIELDS}` before `bulk_insert_rows` — raw OpenF1 dict never inserted directly. |
| T-03-02 | Information Disclosure | high | mitigate | CLOSED | `.streamlit/config.toml:13` — `[server] address = "localhost"` pinned. Note: the plan's own mitigation ("grep gate: no `0.0.0.0` in dashboard.py") was a necessary-but-insufficient check — 03-04-SUMMARY documents this was genuinely violated at Streamlit *runtime* (bound `*:8501` under `--server.headless true` regardless of source-code content) and was fixed in commit `ca2397c` by pinning `server.address` in `.streamlit/config.toml`, which loads unconditionally regardless of CLI flags. Verified the actual config file content directly (not the grep gate, and not merely the SUMMARY's claim). No `0.0.0.0` string found in `dashboard.py`, `pipeline.py`, `replay_race.py`, or config. Human checkpoint (03-04 Task 2) additionally confirmed localhost-only + no auth prompt at runtime. |
| T-03-03 | Tampering | medium | mitigate | CLOSED | `src/f1_prediction/dashboard_lib/queries.py` and `dashboard.py` — grepped for `INSERT`/`UPDATE`/`DELETE`/`to_sql`/`bulk_insert`: only match is the docstring's contract statement ("never INSERT/UPDATE/DELETE"); every actual SQL statement in both files is `SELECT`/`pd.read_sql` via `text()` + bound params. Dashboard is read-only in practice, not just by claim. |
| T-03-04 | Denial of Service | low | mitigate | CLOSED | `src/f1_prediction/pipeline.py:100-104` — `sync_drivers_for_session` does `SELECT 1 FROM raw_drivers WHERE session_key = :sk LIMIT 1` and returns early (0 rows) if already populated (check-then-skip, once per session_key). `fetch_drivers` → `OpenF1Client.drivers` → `_fetch` (`openf1_client.py:92-97`) inherits `tenacity` `@retry` with `wait_exponential(multiplier=1, min=2, max=60)` and `stop_after_attempt(6)`. |
| T-03-05 | Information Disclosure | medium | mitigate | CLOSED | `dashboard.py:92` — `st.caption(f"LIVE · Lap {lap_number} · updated {_format_time(latest_computed_at)}")` surfaces both `lap_number` and `computed_at` from the queried row (not hardcoded). `dashboard_lib/queries.py:120` (`load_inrace_predictions`) and `:37-40` (`_engine`/`@st.cache_resource`) — only the Engine (connection pool) is cached; every query opens `with _engine().connect() as conn:` fresh, confirmed in both `load_prerace_predictions` and `load_inrace_predictions`. |
| T-03-06 | Denial of Service | low | accept | CLOSED | Accepted risk, logged in this SECURITY.md (see Accepted Risks Log below) — disposition requires only a documented acceptance entry, not a code mitigation. Entry now present. |
| T-03-07 | Tampering | low | mitigate | CLOSED | `src/f1_prediction/inference/inrace.py:137-161` — `DELETE FROM predictions WHERE session_key = :sk AND prediction_type = 'inrace' AND lap_number = :lap` runs inside the same `engine.begin()` transaction as the subsequent `rows_df.to_sql(..., if_exists="append")` insert (WR-02 fix applied post-review: delete+insert now atomic, closing the stale-read window WR-02 flagged). `predict_inrace_with_softmax` reused verbatim (no reimplemented softmax) guarantees sum-to-1. All SQL uses `text()` + bound params. |
| T-03-08 | Denial of Service | medium | mitigate | CLOSED | `src/f1_prediction/pipeline.py:208-219` — `scheduler.add_listener(_on_error, EVENT_JOB_ERROR)` registered before `scheduler.add_job(..., max_instances=1, misfire_grace_time=15)`; `_poll_and_infer_job` wraps `poll_once` + `run_inference_after_poll` as a single scheduled job — an inference exception is caught by APScheduler's own job-error handling and logged via `_on_error`, the scheduler thread survives (mirrors `live/poller.py` convention). |
| T-03-09 | Tampering | medium | mitigate | CLOSED | `scripts/replay_race.py` — `--target-session-key` defaults to `--source-session-key` (line 481); `--db` flag allows scratch-DB targeting (line 479); dedupe guards in `replay_lap` (existing driver-number sets checked before insert, lines 307-345); all SQL via `text()` + bound params. **Strengthened beyond the original plan**: post-review fixes (commit `973e1c3`, CR-01) replaced the naive `finally: _clear_staging(...)` with a `safe_to_clear_staging` flag only set `True` after the restore loop has attempted every lap, and the restore loop now catches `BaseException` (not `Exception`) so a second Ctrl-C cannot abort it early. A **further follow-up fix (commit `e4e076a`)** — landed after a real production incident (`pkill -TERM` against a live in-place replay stranded session 9839's `raw_laps` at 620/1156 rows) — added a `_handle_sigterm` handler (`replay_race.py:404-417`) registered via `signal.signal(signal.SIGTERM, _handle_sigterm)` in `main()` (line 422), translating SIGTERM into `SystemExit` so the existing CR-01 restore path now also runs on process-manager kills, not just Ctrl-C. This closes a real gap the original T-03-09 mitigation plan did not anticipate (the original plan only cited the dedupe guard, not interrupt-signal coverage) — verified present in the current file, not just claimed in commit messages. |
| T-03-10 | Tampering | low | mitigate | CLOSED | `src/f1_prediction/db/models.py:345-349` — `CheckConstraint("prediction_type IN ('prerace', 'inrace')", name="ck_predictions_prediction_type")` on the `Prediction.__table_args__`. |
| T-03-11 | Information Disclosure | high | mitigate | CLOSED | 03-04-PLAN.md Task 2 (`checkpoint:human-verify`, gate=`blocking`) step 6 explicitly required confirming localhost-only URL + no login prompt; 03-04-SUMMARY.md confirms `resume-signal` was "approved" after the user visited `http://localhost:8501` and confirmed this. Backed by the same `.streamlit/config.toml` code-level fix verified under T-03-02 — this is not solely a procedural/documentation claim, there is a corresponding runtime configuration change. |

## Depth Note (ASVS Level 1)

Per `asvs_level: 1`, verification was grep/presence-level: confirming each cited mitigation pattern exists in the correct file. For T-03-02 and T-03-09 specifically, verification went slightly beyond pure grep-presence because the SUMMARY/REVIEW artifacts themselves documented that a prior grep-only check had produced a false negative (T-03-02: source-level check passed while runtime behavior violated the invariant) — in both cases the actual current runtime-relevant artifact (`.streamlit/config.toml`, `replay_race.py`'s registered signal handler) was read directly rather than trusting the narrative.

## Unregistered Flags

None found requiring new threat entries. Only 03-03-SUMMARY.md contains an explicit `## Threat Flags` section, which states "None — all new surface already dispositioned in this plan's own threat_model (T-03-05..T-03-08)." Plans 01, 02, and 04 have no `## Threat Flags` section in their SUMMARY files. Cross-checked their Accomplishments/Deviations/Issues Encountered narratives for undeclared new attack surface:

- 03-01: pyproject.toml dependency additions, shared inference module extraction, new ORM tables — all covered by T-03-01/T-03-10/T-03-SC.
- 03-02: dashboard_lib query/chart layer, pipeline.py entrypoint — all covered by T-03-01/T-03-02/T-03-03/T-03-04.
- 03-04: replay_race.py staging/backup mechanism — covered by T-03-09; the WR-03 (dashboard/pipeline import coupling) and WR-05 (SIGTERM) code-review findings are code-quality/robustness fixes to already-registered threats or non-security architecture concerns, not new trust boundaries.

No new external inputs, new network listeners, or new privilege boundaries were introduced outside the registered threat model.

## Accepted Risks Log

| Threat ID | Category | Severity | Rationale | Accepted By |
|-----------|----------|----------|-----------|-------------|
| T-03-06 | Denial of Service (Fragment 15s reads hammering SQLite) | low | 15-second polling cadence against a single-writer SQLite WAL database with ~20-row read results per query is well within SQLite's read capacity; the dashboard is strictly read-only so there is no lock contention with the pipeline's writer connection. Consistent with the project's documented scale assumptions (CLAUDE.md: ~72,000 total rows across 3 seasons). | 03-03-PLAN.md threat_model (plan-time disposition), confirmed unchanged through implementation and code review. |

## Code-Review-Driven Hardening Beyond the Original Threat Model

The 03-REVIEW.md / 03-REVIEW-FIX.md cycle produced two fixes that materially strengthen already-registered threats beyond what their original mitigation plans specified:

1. **T-03-07** (inference hook writing bad rows): WR-02 finding upgraded the mitigation from "delete-then-insert on separate connections" (a stale-read window existed) to atomic delete+insert in a single `engine.begin()` transaction. The original plan's disposition text only promised idempotency, not atomicity — the fix closes a real (if narrow) staleness window the plan did not anticipate.
2. **T-03-09** (replay writing to the live project DB): CR-01 and the follow-up SIGTERM fix (`e4e076a`) address an interrupt-handling gap that was not part of the original T-03-09 mitigation plan (dedupe guard + target-key defaulting only). A real incident (SIGTERM during an in-place replay stranding the staging backup) proved the gap was live, not theoretical, and the fix is now verified present in `scripts/replay_race.py`.

Additionally, `src/f1_prediction/db/models.py`'s `UniqueConstraint` (WR-01 fix) adds defense-in-depth for T-03-10/prediction-integrity beyond the `CheckConstraint` the plan originally required, and `src/f1_prediction/pipeline.py`'s `_handle_sigterm` (WR-05) closes the same class of interrupt-handling gap for the pipeline's own scheduler shutdown, mirrored by the replay script's later fix.

None of these hardening fixes were required to close a threat that would otherwise remain OPEN — they are recorded here because the adversarial audit stance requires surfacing evidence, not just accepting SUMMARY.md's self-report that "6/6 fixed."

## Verification Method Summary

- Read every implementation file cited across all four threat registers directly (not SUMMARY.md prose) for: `openf1_client.py`, `db/models.py`, `pipeline.py`, `dashboard.py`, `dashboard_lib/queries.py`, `inference/inrace.py`, `inference/prerace.py`, `scripts/replay_race.py`, `.streamlit/config.toml`.
- Ran targeted greps for `0.0.0.0`/`server.address`, `INSERT`/`UPDATE`/`DELETE`/`to_sql`, and confirmed check-then-skip patterns rather than accepting plan/summary claims at face value.
- Cross-checked git log for the specific commits SUMMARY/REVIEW-FIX files claimed (`e4e076a`, `973e1c3`) to confirm the described fixes are actually the commits' content, not just their messages.
