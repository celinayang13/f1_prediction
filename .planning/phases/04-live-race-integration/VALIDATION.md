# Phase 4: Live Race Integration — Validation

## Raw Observations

**Session validated:** `session_key=9550` (2024 Austria GP, Race, 72 laps) — chosen via
`SELECT rl.session_key, COUNT(DISTINCT rl.lap_number) AS laps, rs.session_type, rs.year, rs.round_number, rs.country_name FROM raw_laps rl LEFT JOIN raw_sessions rs ON rs.session_key = rl.session_key GROUP BY rl.session_key ORDER BY laps DESC LIMIT 10` (query used `text()` with no interpolated user input — a fixed LIMIT). Top candidates were the two Monaco races (78 laps each); Austria 2024 was selected instead as a non-Monaco full-distance race (72 laps, close to the ~60-lap target and more representative of a typical circuit) with confirmed non-empty `raw_positions` (591 rows), `raw_stints` (65 rows), and `raw_pit_stops` (45 rows) — Monaco/2022-era sessions were avoided per known data-quality caveats already logged in STATE.md's Deferred Items.

**Processes run concurrently against the shared project DB (`data/f1.db`):**

1. `python3.11 -m src.f1_prediction.pipeline` — PID 92426. Resolved `session_key=9839` as the latest race (D-01, unrelated to the session being replayed — expected, since `get_latest_race_session_key` always picks the true latest race regardless of which session `replay_race.py` targets). Logged `pipeline: prerace predictions already computed for session 9839 — skipping (D-11)`, then started its 30s `BackgroundScheduler` poll loop. Log: `/private/tmp/claude-501/-Users-celinayang-Downloads-f1-prediction/e6805d89-4bd9-469e-8886-8b8fa0fdc844/scratchpad/pipeline.log`.
2. `python3.11 -m streamlit run src/f1_prediction/dashboard.py --server.headless true` — PID 92557. Confirmed `Uvicorn server started on localhost:8501` (binds to `.streamlit/config.toml`'s pinned `address = "localhost"`). Log: `/private/tmp/claude-501/-Users-celinayang-Downloads-f1-prediction/e6805d89-4bd9-469e-8886-8b8fa0fdc844/scratchpad/dashboard.log`.
3. `python3.11 scripts/replay_race.py --source-session-key 9550 --interval 1.5` — PID 92715. In-place replay (default target == source per T-03-09): staged a full backup of session 9550's `raw_laps`/`raw_positions`/`raw_stints`/`raw_pit_stops` under staging session `900000009550`, cleared the original, then rebuilt all 72 laps incrementally. Used `--interval 1.5` (not the default 5.0s) to keep the run bounded (~2 minutes total) while still exercising the full 72-lap distance — a documented, plan-sanctioned deviation from the default interval per the plan's "a smaller `--interval` is fine to keep the run short" guidance. Log: `/private/tmp/claude-501/-Users-celinayang-Downloads-f1-prediction/e6805d89-4bd9-469e-8886-8b8fa0fdc844/scratchpad/replay.log`.

**Run window:** replay started 2026-07-09T10:23:43 (local), completed 2026-07-09T10:25:54 (local) — **72 laps in 131 seconds** wall-clock (`replay_race: replay complete for session 9550 (72 laps)` at 10:25:54). Both `pipeline.py` and the `streamlit` dashboard process remained running throughout and after (still alive as of evidence-gathering).

### Criterion 1 evidence — per-lap inference lag + auto-trigger

Query: `SELECT lap_number, COUNT(*) FROM predictions WHERE prediction_type='inrace' AND session_key=:sk GROUP BY lap_number ORDER BY lap_number` (bound param `sk=9550`) returned **71 distinct lap_number rows** (laps 1–71), each with 18–20 driver rows (field naturally thins toward the end as cars are lapped/retire from the query's per-lap join). Lap 72 has no `inrace` row under lap_number=72 — investigated and confirmed **not a bug**: `raw_laps` for session 9550 has only 1 driver's row at `lap_number=72` (the race winner crosses the line first; the other ~19 cars' last *completed* lap is still 71 at that point in a real race), so `compute_and_store_inrace_latest_lap`'s `MAX(lap_number)` over the rebuilt `features_inrace` table correctly resolves to 71 one extra time on the final iteration (log shows `lap 72 — ... inserted 12 inrace rows for session 9550 lap 71` — i.e., a second, idempotent DELETE-then-INSERT of lap 71's row for the 12 remaining cars whose stints data changed). This is the expected shape of historical race-completion data, not a system defect.

Per-lap lag (replay's lap-insert log line timestamp vs. the corresponding `compute_and_store_inrace_latest_lap` completion log line timestamp for the same lap number, both parsed from `replay.log`):

| Lap | Lag (s) |
|-----|---------|
| 1 | 1.0 |
| 2 | 0.0 |
| 3 | 0.0 |
| 4 | 1.0 |
| 5 | 0.0 |
| ... | ... |
| 68 | 0.0 |
| 69 | 1.0 |
| 70 | 1.0 |
| 71 | 1.0 |

**71 of 72 laps produced an inference lag sample** (lap 72 has no distinct new lap_number to key on, per the explanation above). Max observed lag: **1.0s**. Min: **0.0s**. All samples are far under the 60s budget.

**Important nuance (per plan instructions and `replay_race.py`'s own docstring, lines 24-38):** this sub-second lag reflects `replay_race.py` calling `compute_and_store_inrace_latest_lap()` **directly** after each lap insert — not the real poll loop's `is_session_active()`-gated path. Confirmed directly in `pipeline.log`: the pipeline's own `_poll_and_infer_job` ran every 30s throughout the replay window (`Running job "main.<locals>._poll_and_infer_job ..."` / `... executed successfully`, e.g. at 10:25:23, 10:25:53, 10:26:23), but it was polling session `9839` (the real latest race), not `9550` — and even if it had targeted 9550, `is_session_active()` queries OpenF1 for the session's real `date_start`/`date_end` against wall-clock "now," which is always False for a completed historical race. This is the intentional Phase 3 adaptation documented in the script's docstring, not a validation failure: the 60s-lag criterion is genuinely exercised by replay's direct-call path, which is materially faster (sub-second) than the poll loop would be, demonstrating the inference chain itself (rebuild → slice → softmax → insert) comfortably meets the budget once triggered.

### Criterion 2 evidence — dashboard lap-by-lap auto-refresh (human-observed)

Deferred to the Task 3 human-verify checkpoint — the dashboard (PID 92557) was kept running at `http://localhost:8501` throughout and after the replay specifically so this could be observed live. See Task 3 below.

### Criterion 3 evidence — WAL no-contention

Grepped all three capture logs for `SQLITE_BUSY`, `database is locked`, and `OperationalError` (case-insensitive):

- `pipeline.log`: **0 matches**
- `dashboard.log`: **0 matches**
- `replay.log`: **0 matches**

Zero lock-contention errors across the full concurrent run (writer process `pipeline.py`, writer process `replay_race.py`, reader process `dashboard.py`, all three touching `data/f1.db` simultaneously for the full 131-second replay window). Mechanism: `src/f1_prediction/db/engine.py::_set_sqlite_pragma` — a SQLAlchemy `connect` event listener that runs `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` on every new connection from `get_engine()`, which all three processes use exclusively (no second engine factory anywhere in the codebase).

### Criterion 4 evidence — ~60-lap stability

- **Laps replayed:** 72 (exceeds the ~60-lap target).
- **Total duration:** 131 seconds wall-clock (10:23:43 → 10:25:54).
- **Pipeline process:** confirmed alive via `ps -p 92426` both during and after the replay window; `pipeline.log` shows zero `Traceback`/`ERROR` lines across 38 log lines spanning the full run, and its `EVENT_JOB_ERROR` listener (`_on_error`) never fired (no `pipeline poll job failed` lines present).
- **Dashboard process:** confirmed alive via `ps -p 92557`; `dashboard.log` (9 lines) shows a clean startup with zero errors.
- **Replay process:** ran to normal completion (`replay_race: replay complete for session 9550 (72 laps)`), never hit its `except BaseException` interrupt-restore path (that path only logs on interruption, and no such log line is present).
- No manual restart of any process was required at any point (D-04 not invoked).

No log files were committed into the repository — all three logs remain under the scratchpad directory referenced above.
