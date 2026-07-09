# Phase 3: Prediction Pipeline & Dashboard - Research

**Researched:** 2026-07-07
**Domain:** Streamlit live-refreshing dashboards, OpenF1 driver metadata, SQLite WAL two-process coordination, XGBoost inference serving
**Confidence:** MEDIUM (HIGH on codebase-verified facts; MEDIUM on Streamlit/OpenF1 web-sourced facts — see Sources)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** The pipeline and dashboard always track the **latest race session** in the DB (most recent `Race`-type `session_key`, auto-detected). No manual session dropdown, no CLI `--session-key` flag for v1 — matches how a real race weekend will actually work in Phase 4 (there's only ever one "current" race).
- **D-02:** Build a **replay script** (e.g. `scripts/replay_race.py`) that re-inserts a completed historical race's laps/positions/stints/pit-stops into the `raw_*` tables at a configurable interval, simulating a live race so the poll → inference → dashboard chain can be watched end-to-end during development, before Phase 4's real live-race test.
- **D-03:** Replay script defaults to a **fast interval** (e.g. one lap every ~5s, so a full race replays in a couple minutes). Expose an `--interval` flag to slow it to realistic race pacing (~60–100s/lap) if the user wants to watch it unfold naturally.
- **D-04:** Dashboard shows the required horizontal win-probability bar chart (DASH-01) **plus** a side data table per driver with: current position, tyre compound, tyre age, and gap to leader.
- **D-05:** Bar chart bars are **color-coded by constructor/team** using official team colors (not a single/auto palette).
- **D-06 (important gap found during discussion):** Driver full names, acronyms, and official team colors are **not currently stored anywhere** in the DB — raw tables only have `driver_number`, and `team_name` is inferred indirectly (`features/builder.py::_get_team_name_from_session`). Phase 3 MUST add an OpenF1 `/v1/drivers` client method (`src/f1_prediction/ingest/openf1_client.py` already has the `_fetch` pattern for other endpoints — follow it) plus storage (a `raw_drivers` table or equivalent per-session lookup) to source `full_name`, `name_acronym`, `team_name`, and `team_colour` for the dashboard. This is a real new data source, not a formatting detail.
- **D-07:** In-race tab **auto-refreshes every 15 seconds** — independent of the poller's 30s interval.
- **D-08:** **No manual "refresh now" button** — auto-refresh only, for simplicity. Can be reconsidered later if 15s feels laggy in practice.
- **D-09:** Pre-race win probabilities are computed **automatically when `python -m src.f1_prediction.pipeline` starts** (using whatever qualifying/practice data exists for the latest race session), before the process moves into the live poll loop for in-race updates. No separate manual trigger script.
- **D-10:** **Single `predictions` table** with a `prediction_type` column (`'prerace'` / `'inrace'`) and a nullable `lap_number` (NULL for `prerace` rows) — one query pattern for the dashboard, and easier to extend for DASH-V2 trend lines later. Needs at minimum: `session_key`, `driver_number`, `win_probability`, `prediction_type`, `lap_number`, `computed_at`.
- **D-11:** Pre-race prediction computation is **idempotent on restart** — at pipeline startup, check whether a `'prerace'` row already exists for the current `session_key` before recomputing; skip if present. Mirrors the resumable pattern already established in Phase 1's `backfill.py` (D-11 there).
- **Project Constraints (supersedes CLAUDE.md):** `.planning/PROJECT.md` §Key Decisions locks **"XGBoost + Streamlit over LightGBM + Dash."** Phases 1–2 already built on XGBoost. CLAUDE.md's Technology Stack section is stale on the dashboard framework (recommends Dash, lists Streamlit under "What NOT to Use") — this predates the pivot. **Use Streamlit, not Dash.** The rest of CLAUDE.md (OpenF1/FastF1 sourcing, SQLite/WAL, local-first, no paid data) is still accurate. `.planning/research/STACK.md` is also stale on this point for the same reason.
- Two-process architecture is locked by ROADMAP.md's exact entrypoints — `python -m src.f1_prediction.pipeline` and `streamlit run src/f1_prediction/dashboard.py` — don't merge pipeline and dashboard into one process.

### Claude's Discretion

- Exact wiring of "inference runs after each completed lap" inside `pipeline.py` (e.g., checking `poll_once()`'s returned `laps_inserted` count before triggering feature rebuild + inference) — implementation detail, not a user-facing decision.
- Streamlit auto-refresh mechanism (`st.rerun()` loop vs `streamlit-autorefresh` component vs `st.fragment(run_every=...)`) to hit the 15s cadence from D-07. **This research recommends `st.fragment(run_every=15)`** — see Architecture Patterns, Pattern 3.
- Exact `raw_drivers` schema / caching strategy for D-06 (table vs in-memory dict refreshed per session). **This research recommends a `raw_drivers` table**, refreshed once per pipeline startup per session_key — see Don't Hand-Roll and Open Question 2.

### Deferred Ideas (OUT OF SCOPE)

- Manual session/race dropdown or browsing older races — rejected for v1 in favor of "always latest race" (D-01); could become a v2 feature.
- Manual "refresh now" button — rejected for v1 (D-08); reconsider if 15s auto-refresh feels laggy.
- DASH-V2-01 (probability trend lines), DASH-V2-02 (driver info overlays — largely satisfied early by D-04's side table, but full overlay design deferred), DASH-V2-03 (race event annotations) — already tracked in REQUIREMENTS.md as v2, not this phase.
- HOST-01 (hosted deployment) — explicitly out of scope; local-first only per PROJECT.md constraints.
- Model retraining, live-race validation against a real OpenF1 session (Phase 4), multi-race browsing UI.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-------------------|
| DASH-01 | Dashboard displays a live win probability bar chart with all drivers ranked by their current win probability | Architecture Patterns (System Diagram, Pattern 3/4/5); Code Examples (`predictions` schema); Don't Hand-Roll (team-color sourcing) |
| DASH-02 | Dashboard has a pre-race tab showing qualifying-based win probabilities before the race starts | System Diagram (Pre-race tab, static render); Pattern 2 (reuse `predict_with_softmax`); D-09/D-11 idempotency handled in `pipeline.py` startup sequence |
| DASH-03 | Dashboard has an in-race tab that auto-refreshes each lap showing live-updating probabilities | Pattern 1 (rebuild-then-slice inference trigger), Pattern 3 (`st.fragment(run_every=15)`), Pattern 4 (fresh-connection reads), Pitfall 5 (column-order correctness) |
| DASH-04 | Dashboard runs locally at localhost and requires no authentication | Security Domain (V2/V3/V4 explicitly not applicable; localhost-bind threat pattern); Environment Availability (Streamlit fix required first — Pitfall 1) |
</phase_requirements>

## Summary

Phase 3 wires the two Phase 2 XGBoost artifacts into the live poll loop and ships a two-tab Streamlit dashboard. The two processes (`pipeline.py`, `dashboard.py`) never talk directly — they communicate only through SQLite in WAL mode, which is already configured in `db/engine.py`. The single biggest risk in this phase is **not** the ML or dashboard logic — it is the runtime environment: the project's actual Python interpreter (`/opt/anaconda3/bin/python3.11`, the one on `PATH` as `python3.11`) has Streamlit 1.16.0 installed, and **it is currently broken** (`ModuleNotFoundError: No module named 'altair.vegalite.v4'` — Streamlit 1.16 imports an Altair v4 API that Altair 5.0.1, also installed, removed). `python -m streamlit --version` fails outright today. Streamlit must be upgraded before any dashboard code can be exercised, and the upgrade is also what unlocks the modern `st.fragment(run_every=...)` auto-refresh API this phase needs (stable only since Streamlit 1.37.0, mid-2024).

Two other structural facts should drive the plan. First, `pyproject.toml`'s `dependencies` list is stale — it lists only `fastf1`, `pandas`, `numpy`, `apscheduler`, `httpx`, `sqlalchemy`, `tenacity`, but Phase 2 already depends on `xgboost`, `scikit-learn`, `joblib`, `scipy`, and Phase 3 needs `streamlit` and `plotly`. None of these six ML/dashboard packages are declared anywhere; they only exist because they happen to be pre-installed in the Anaconda base environment. Second, `driver_number` is the only driver identity currently stored anywhere in the DB — there is no `full_name`, `name_acronym`, or `team_colour` source. The OpenF1 `/v1/drivers` endpoint fills this gap and must be added to `openf1_client.py` following the exact `_fetch()` pattern already used for `laps`/`stints`/`pit`/`position`/`weather`/`intervals`.

**Primary recommendation:** Fix the Streamlit/Altair install and pin real dependencies in `pyproject.toml` as a Wave 0 task; add an `OpenF1Client.drivers()` method + a `RawDriver` lookup table; build `predictions` as a single table with a `prediction_type` discriminator; wire inference into `pipeline.py` by reusing `build_features_for_session(kind="inrace")`'s full-session rebuild-then-slice-latest-lap pattern (do not hand-roll incremental per-lap feature computation); and use `st.fragment(run_every=15)` scoped to the in-race tab only, opening a fresh SQLAlchemy connection per fragment rerun so WAL snapshot semantics don't serve stale data.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Poll OpenF1 for new laps | API/Backend process (`pipeline.py`) | — | Already implemented in `live/poller.py`; pipeline wraps/extends it |
| Trigger inference after new lap | API/Backend process (`pipeline.py`) | Database | Hooks off `poll_once()`'s `laps_inserted` return value; writes result to DB, never to the dashboard process directly |
| Feature rebuild for inference | API/Backend process (`pipeline.py`) | Database | Reuses `features/builder.py::build_features_for_session` — full-session DELETE+INSERT, not new incremental logic |
| Model inference (XGBoost + softmax) | API/Backend process (`pipeline.py`) | — | Loads `data/models/*.joblib`; same `predict_*_with_softmax` pattern as `scripts/train.py` |
| Persist predictions | Database (SQLite/WAL) | — | Single `predictions` table is the only channel between the two processes |
| Driver identity/team color lookup | Database / Backend | — | New `raw_drivers` table populated by `pipeline.py` (or a one-off sync step) from OpenF1 `/v1/drivers`; dashboard reads it read-only |
| Render bar chart + side table | Browser/Client (Streamlit process) | — | `dashboard.py` — read-only, no writes to DB ever |
| Auto-refresh cadence (15s) | Browser/Client (Streamlit process) | — | `st.fragment(run_every=15)` scoped to the in-race tab; pre-race tab renders once, no fragment |
| Replay/dev-simulation of a live race | Standalone dev script (`scripts/replay_race.py`) | Database | Writes directly into `raw_*` tables on a timer; drives the real `pipeline.py`/`dashboard.py` chain without touching OpenF1 |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| streamlit | 1.59.0 (latest on PyPI as of 2026-07; currently `1.16.0` is installed and broken — see Pitfall 1) [VERIFIED: pip index versions] | Dashboard UI, tabs, auto-refresh fragments | Locked project decision (PROJECT.md Key Decisions; supersedes stale CLAUDE.md) |
| plotly | 6.8.0 (latest on PyPI; `5.22.0` is what's actually installed and working in the project's interpreter today) [VERIFIED: pip index versions] | Horizontal bar chart with per-team colors | Already a project dependency per CLAUDE.md; `go.Bar(orientation="h", marker_color=[...])` is the idiomatic per-bar-color API |
| xgboost | 3.2.0 (installed) [VERIFIED: pip show, matches scripts/train.py imports] | Load `CalibratedClassifierCV`-wrapped `XGBClassifier` artifacts for inference | Already the Phase 2 model; Phase 3 only loads, never trains |
| scikit-learn | 1.5.1 (installed) | `CalibratedClassifierCV.predict_proba` at inference time | Same artifact format Phase 2 produced |
| joblib | 1.4.2 (installed) | `joblib.load()` the two `.joblib` model files | Matches `save_model()` in `scripts/train.py` |
| scipy | (installed, used via `scipy.special.softmax`) | Per-group softmax normalization of raw XGBoost scores into a win-probability distribution | Reuse `predict_with_softmax` / `predict_inrace_with_softmax` logic verbatim from `scripts/train.py` — do not reimplement |
| sqlalchemy | 2.0.51 declared / 2.0.30 installed | Read/write `predictions` and new `raw_drivers` tables | Already the project ORM/engine layer (`db/engine.py`, `db/models.py`) |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | 0.28.1 declared / 0.26.0 installed | Fetch `/v1/drivers` | Reuse the existing `OpenF1Client` async pattern — do not add a new HTTP client |
| apscheduler | 3.11.2 | `pipeline.py`'s scheduler | Already used by `live/poller.py`'s `BackgroundScheduler`; `pipeline.py` should reuse/extend it, not build a second scheduler |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `st.fragment(run_every=15)` | `streamlit-autorefresh` component (`streamlit_autorefresh.st_autorefresh`) | Third-party component reruns the **entire** script every tick (heavier, and would also rerun the pre-race tab against D-08's "no manual refresh, in-race only" intent); `st.fragment` is native, scoped, and requires no new dependency once Streamlit is upgraded |
| `st.fragment(run_every=15)` | `st.rerun()` inside a `while True` + `time.sleep()` loop | Blocks the Streamlit script thread and fights Streamlit's own rerun model; not the documented pattern for periodic partial updates. PROJECT.md's own rationale ("st.rerun() sufficient for lap-by-lap refresh cadence") predates the fragment API's stabilization — `st.fragment(run_every=...)` achieves the same outcome more safely |
| Single `predictions` table with `prediction_type` discriminator (D-10, locked) | Two separate tables (`predictions_prerace`, `predictions_inrace`) | Locked decision — do not deviate. Discriminator column keeps one dashboard query pattern and is easier to extend for DASH-V2 trend lines |
| New `raw_drivers` table (D-06 discretion, recommend table) | In-memory dict refreshed per session inside `pipeline.py` | A table is preferred: it survives process restarts, is queryable by `dashboard.py` independently (dashboard must never call OpenF1 directly — it is read-only against SQLite only), and follows the existing `raw_*` naming/whitelist-extraction convention |

**Installation (fix + additions needed in the target interpreter, `/opt/anaconda3/bin/python3.11`):**
```bash
python3.11 -m pip install --upgrade "streamlit>=1.37" plotly
```
Upgrading `streamlit` resolves its own `altair` dependency to a compatible 5.x version automatically — do not add an `altair<5` pin (that would keep it compatible with the broken 1.16.0, not fix the real problem, and would block `st.fragment`).

**Version verification:** Verified via `pip index versions streamlit`, `pip index versions plotly`, `pip index versions altair` against the live PyPI index on 2026-07-07, and cross-checked against `pip show`/`import` on both `.venv` (essentially empty — only `pip`/`setuptools`) and the actual working interpreter at `/opt/anaconda3/bin/python3.11` (confirmed via `which python3.11`, matching STATE.md's 01-01 note "System Python 3.11 used").

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| streamlit | PyPI | Est. 2019 (multi-year, well-known) | Unknown via automated check (millions/week in reality) | github.com/streamlit/streamlit | [SUS] (flagged "too-new" + "unknown-downloads" by the automated legitimacy checker) | **Approved — false positive.** The checker's "too-new" signal reflects only the *latest release date* (2026-07-06), not package age; streamlit is a 6+ year old, extremely widely used package, already named as a locked stack choice in `PROJECT.md`/`ROADMAP.md`/CLAUDE.md. No `checkpoint:human-verify` needed. |
| plotly | PyPI | Est. 2013 (multi-year, well-known) | Unknown via automated check ("unknown-downloads" only) | github.com/plotly/plotly.py | [SUS] ("unknown-downloads" only) | **Approved — false positive.** Same reasoning; plotly is already an installed, imported dependency in the working interpreter and named in CLAUDE.md's stack table. No checkpoint needed. |

**Packages removed due to [SLOP] verdict:** none.
**Packages flagged as suspicious [SUS]:** streamlit, plotly — both dispositioned "Approved — false positive" above; no `checkpoint:human-verify` required because both are pre-existing, named, locked-decision stack packages, not newly discovered ones. If the planner wants defense-in-depth, a lightweight `pip show streamlit && pip show plotly` sanity check before the upgrade command is sufficient (already effectively done during this research — see Environment Availability).

No new/unfamiliar packages are being introduced in this phase — `streamlit` and `plotly` are upgrades of already-present dependencies, not net-new installs.

## Architecture Patterns

### System Architecture Diagram

```
                    ┌───────────────────────────────────────────┐
                    │        OpenF1 REST API (api.openf1.org)     │
                    └───────────────────┬─────────────────────────┘
                                        │ poll every 30s
                                        ▼
┌──────────────────────────────────────────────────────────────────┐
│  Process 1: `python -m src.f1_prediction.pipeline`                │
│                                                                    │
│  [Startup]                                                        │
│    1. Resolve latest Race session_key (D-01: MAX by year/round     │
│       among raw_sessions WHERE session_type='Race')                │
│    2. If no 'prerace' row exists yet for this session_key (D-11):  │
│         compute_qualifying/practice features → load                │
│         prerace_model.joblib → predict_with_softmax →              │
│         INSERT INTO predictions (prediction_type='prerace')        │
│    3. Start BackgroundScheduler (reuse live/poller.py's pattern)   │
│                                                                    │
│  [Every 30s poll tick]                                             │
│    poll_once(session_key) ──► laps_inserted > 0? ──► YES           │
│         │                                            │              │
│         NO (no-op)                                   ▼              │
│                                    build_features_for_session(      │
│                                      session_key, kind="inrace")    │
│                                    (full-session rebuild — reuse,   │
│                                     do not hand-roll incremental)   │
│                                            │                        │
│                                            ▼                        │
│                             SELECT rows WHERE lap_number = MAX(..)  │
│                                            │                        │
│                                            ▼                        │
│                     load inrace_model.joblib → predict_proba →     │
│                     predict_inrace_with_softmax (per-lap group)    │
│                                            │                        │
│                                            ▼                        │
│              INSERT INTO predictions (prediction_type='inrace',    │
│                                        lap_number=N)                │
└──────────────────────────────┬─────────────────────────────────────┘
                                │  writes only — WAL mode
                                ▼
                    ┌───────────────────────────┐
                    │   data/f1.db (SQLite,      │
                    │   WAL journal mode)        │
                    │   predictions table +      │
                    │   raw_drivers lookup table │
                    └─────────────┬─────────────┘
                                │  reads only — fresh conn per refresh
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  Process 2: `python -m streamlit run src/f1_prediction/dashboard.py`│
│  (localhost:8501, no auth)                                         │
│                                                                    │
│  st.tabs(["Pre-race", "In-race"])                                  │
│    Pre-race tab (static, renders once per page load):              │
│      SELECT * FROM predictions WHERE prediction_type='prerace'     │
│        AND session_key=<latest> → join raw_drivers → bar chart     │
│                                                                    │
│    In-race tab — @st.fragment(run_every=15):                       │
│      SELECT * FROM predictions WHERE prediction_type='inrace'      │
│        AND session_key=<latest> AND lap_number = MAX(lap_number)   │
│        → join raw_drivers (team_colour, full_name) →               │
│        plotly go.Bar(orientation='h', marker_color=[team colors])  │
│        + side st.dataframe (position, tyre, gap)                   │
└──────────────────────────────────────────────────────────────────┘

                    ┌───────────────────────────────────────────┐
                    │ scripts/replay_race.py (dev-only tool)     │
                    │ Reads a COMPLETED historical race's rows   │
                    │ from raw_laps/raw_positions/raw_stints/    │
                    │ raw_pit_stops, re-INSERTs them into the    │
                    │ SAME raw_* tables on a timer (--interval), │
                    │ simulating poll_once()'s incremental       │
                    │ arrival of new laps — drives Process 1     │
                    │ end-to-end without hitting OpenF1 at all.  │
                    └───────────────────────────────────────────┘
```

### Recommended Project Structure
```
src/f1_prediction/
├── pipeline.py              # NEW — entrypoint: `python -m src.f1_prediction.pipeline`
├── dashboard.py             # NEW — entrypoint: `streamlit run src/f1_prediction/dashboard.py`
├── inference/                # NEW package
│   ├── __init__.py
│   ├── prerace.py            # load prerace_model.joblib, compute + insert prerace predictions
│   └── inrace.py             # load inrace_model.joblib, compute + insert inrace predictions per lap
├── db/
│   ├── models.py             # ADD: Prediction, RawDriver ORM classes
│   └── upsert.py             # reuse bulk_insert_rows for predictions/raw_drivers
├── ingest/
│   └── openf1_client.py      # ADD: drivers() method + fetch_drivers() convenience fn
├── dashboard_lib/             # NEW — small helpers so dashboard.py itself stays thin
│   ├── __init__.py
│   ├── queries.py             # read-only SQL against predictions/raw_drivers (fresh conn per call)
│   └── charts.py              # build_win_prob_bar_chart(df) -> plotly Figure
scripts/
└── replay_race.py             # NEW — dev-only race replay simulator
```

### Pattern 1: Rebuild-then-slice for in-race inference (avoid incremental hand-rolling)
**What:** `features/inrace.py::compute_inrace_features_for_session` already recomputes **all** lap rows for a session on every call (DELETE-then-bulk-INSERT). Phase 3's post-poll hook should call this exact function (via `build_features_for_session(session_key, kind="inrace")`) after every poll that inserted new laps, then filter the returned/reloaded feature rows to `lap_number == MAX(lap_number)` before running inference.
**When to use:** Every time `poll_once()` reports `laps_inserted > 0`.
**Why:** At ~20 drivers × ~60 laps, a full-session feature rebuild is cheap (sub-second on SQLite) and this function is already tested from Phase 1. Writing a new "incremental single-lap feature updater" would duplicate `interval_ahead`/`interval_behind`/`gap_to_leader` logic that depends on cross-driver, cross-lap joins — a classic candidate for silent bugs (see `01-REVIEW-FIX.md` CR-03, a real historical bug in exactly this join logic).

### Pattern 2: Reuse `predict_*_with_softmax` verbatim from `scripts/train.py`
**What:** `scripts/train.py` already defines `predict_with_softmax(model, df, feature_cols)` (groups by `session_key`) and `predict_inrace_with_softmax(model, df)` (groups by `session_key, lap_number`), both wrapping `scipy.special.softmax`.
**When to use:** Import/extract these two functions (or move them to a shared `f1_prediction/inference/` module importable by both `scripts/train.py` and the new `pipeline.py`) rather than reimplementing softmax normalization.
**Example:**
```python
# Source: scripts/train.py (existing, verified in this codebase)
def predict_inrace_with_softmax(model, df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    X = build_inrace_feature_matrix(df).values
    raw_proba = model.predict_proba(X)[:, 1]
    df["raw_prob"] = raw_proba
    df["win_prob"] = df.groupby(["session_key", "lap_number"])["raw_prob"].transform(
        lambda x: softmax(x.values)
    )
    return df
```
**Recommendation:** Move `PRERACE_FEATURE_COLS`, `INRACE_NUMERIC_FEATURE_COLS`, `TYRE_COMPOUND_VALUES`, `encode_tyre_compound`, `build_inrace_feature_matrix`, and the two `predict_*_with_softmax` functions out of `scripts/train.py` into a shared module (e.g. `f1_prediction/inference/features.py`) that both `scripts/train.py` and `pipeline.py` import. Duplicating these constants between `scripts/train.py` and a new `pipeline.py` risks exactly the kind of feature-column drift the 02-CONTEXT.md flags as critical ("feature column sets that inference must reproduce exactly at prediction time").

### Pattern 3: Streamlit fragment-scoped auto-refresh, tabs stay separate
**What:** `@st.fragment(run_every=15)` (stable since Streamlit 1.37.0) reruns only the decorated function's body on a timer; code outside any fragment (including other tabs) is not rerun. [CITED: docs.streamlit.io/develop/api-reference/execution-flow/st.fragment]
**When to use:** Wrap only the in-race tab's render function; keep the pre-race tab's render function un-decorated so it renders once and stays static, matching D-07/D-08 exactly.
**Example:**
```python
# Source: Streamlit official docs pattern (st.fragment + st.tabs), adapted
import streamlit as st

tab_prerace, tab_inrace = st.tabs(["Pre-race", "In-race"])

with tab_prerace:
    render_prerace_tab()          # runs once per page load, no auto-refresh

with tab_inrace:
    @st.fragment(run_every=15)
    def render_inrace_tab():
        df = load_inrace_predictions()   # fresh query each fragment rerun
        st.plotly_chart(build_win_prob_bar_chart(df), use_container_width=True)
        st.dataframe(build_side_table(df))
    render_inrace_tab()
```

### Pattern 4: Fresh connection per read to avoid stale WAL snapshots
**What:** SQLite WAL readers see a snapshot fixed at the moment their read transaction begins; a connection/cursor held open across fragment reruns can keep returning the same snapshot. [CITED: sqlite.org/wal.html]
**When to use:** Cache the `Engine` object itself with `st.cache_resource` (cheap, connection-pool-only), but call `with engine.connect() as conn: pd.read_sql(...)` freshly inside every fragment rerun — never hold a long-lived `Connection` object in `st.session_state` across reruns.
**Example:**
```python
# Source: adapted from Streamlit docs (st.cache_resource) + SQLite WAL docs pattern
import streamlit as st
from f1_prediction.db.engine import get_engine

@st.cache_resource
def _engine():
    return get_engine()   # cached: connection pool, not query results

def load_inrace_predictions(session_key: int) -> pd.DataFrame:
    with _engine().connect() as conn:   # fresh transaction each call
        return pd.read_sql(text("SELECT ... FROM predictions WHERE ..."), conn, params={"sk": session_key})
```

### Pattern 5: `/v1/drivers` client method follows the existing whitelist pattern
**What:** OpenF1 `/v1/drivers` returns `broadcast_name, driver_number, first_name, full_name, headshot_url, last_name, meeting_key, name_acronym, session_key, team_colour, team_name`. [CITED: openf1.org/docs — cross-referenced via WebSearch, field names not independently hit against live API in this research session; treat field list as MEDIUM confidence, verify with one live call during implementation]
**Example:**
```python
# Source: pattern matches existing OpenF1Client methods in openf1_client.py
async def drivers(self, session_key: int) -> list[dict]:
    """Fetch driver metadata from /v1/drivers for a session."""
    return await self._fetch("drivers", {"session_key": session_key})
```
```python
# Whitelist, mirroring _LAP_FIELDS / _STINT_FIELDS in live/poller.py
_DRIVER_FIELDS = [
    "session_key", "driver_number", "full_name", "name_acronym",
    "team_name", "team_colour",
]
```
Note `team_colour` is returned as a bare hex string (e.g. `"3671C6"`, no leading `#`) — Plotly's `marker_color` accepts hex strings but requires the `#` prefix, so prepend it: `f"#{team_colour}"`.

### Anti-Patterns to Avoid
- **`while True: time.sleep(15); st.rerun()`** — blocks the Streamlit script thread; not the documented pattern once `st.fragment(run_every=)` is available (Streamlit ≥1.37).
- **Reimplementing feature computation for a single new lap** — always call the existing full-session `build_features_for_session(..., kind="inrace")` and filter to the max lap; don't write a second, parallel feature-computation code path (drift risk, see Pattern 1).
- **Dashboard process writing to SQLite** — locked as read-only per D-06/CONTEXT.md "Integration Points"; any write from `dashboard.py` breaks the two-process decoupling contract.
- **Holding a single open `Connection`/cursor in `st.session_state` across fragment reruns** — risks serving a stale WAL snapshot (Pattern 4).
- **Pinning `altair<5` to keep Streamlit 1.16.0 working** — fixes the crash but leaves Streamlit too old for `st.fragment`; upgrade Streamlit instead (Pitfall 1).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Per-lap incremental feature computation | A new function that computes only the newest lap's `gap_to_leader`/`interval_ahead`/`interval_behind` | `features/builder.py::build_features_for_session(session_key, kind="inrace")` (full rebuild) + filter to `MAX(lap_number)` | This exact cross-driver join logic already had a real bug (01-REVIEW-FIX.md CR-03: "first merge joins car to itself"); a second parallel implementation doubles the maintenance/bug surface for no measurable perf gain at ~20 drivers |
| Softmax normalization of raw model scores | Custom normalization math | `scipy.special.softmax` via the existing `predict_with_softmax`/`predict_inrace_with_softmax` functions in `scripts/train.py` | Already implements the exact per-group (`session_key` / `session_key,lap_number`) softmax the model was calibrated against; PRERACE-02/INRACE-02 require probabilities summing to 1 — reusing verified code guarantees this |
| Periodic browser-side refresh timer | Custom JS `setInterval` + Streamlit custom component, or a manual polling loop | `st.fragment(run_every=15)` | Native to Streamlit ≥1.37, zero extra dependency, and correctly scoped to only the in-race tab (see Pattern 3) |
| Driver → team-color mapping | A hardcoded Python dict of driver numbers to hex colors (breaks on driver transfers, rookie call-ups, livery changes) | OpenF1 `/v1/drivers` fetched and stored per session in a `raw_drivers` table | Team colors and driver-team pairings change between seasons and even mid-season (driver swaps); a live API-sourced, per-session-keyed table is correct and matches the project's existing `raw_*` sourcing convention |
| Two-process coordination (locking, IPC) | A message queue, socket, or file-lock between `pipeline.py` and `dashboard.py` | SQLite in WAL mode (already configured) | CLAUDE.md explicitly rules out Celery/Redis for this project's scale; WAL mode's snapshot isolation is sufficient for a single-writer/single-reader pair polling every 15–30s |

**Key insight:** Nearly everything Phase 3 needs already exists in the codebase from Phases 1–2 (feature builder, softmax prediction helpers, OpenF1 client pattern, WAL-mode engine). The actual new work is thin glue code (`pipeline.py`, `dashboard.py`) plus one genuinely new data source (`/v1/drivers`). The temptation to "just write a simpler version inline" for feature computation or softmax should be resisted — reuse, don't reimplement.

## Runtime State Inventory

> This phase is additive (new table, new client method, new entrypoint scripts), not a rename/refactor/migration. Skipping this section per its own trigger condition — no existing string/identifier is being renamed. Confirmed: no `git mv`, no schema-column renames, no ID reassignment anywhere in this phase's scope.

## Common Pitfalls

### Pitfall 1: Streamlit is currently non-functional in the project's actual interpreter
**What goes wrong:** `python -m streamlit --version` (and any `streamlit run ...`) raises `ModuleNotFoundError: No module named 'altair.vegalite.v4'` immediately on import, before any app code runs.
**Why it happens:** The working interpreter is `/opt/anaconda3/bin/python3.11` (confirmed via `which python3.11`, matching STATE.md's 01-01 decision to use "System Python 3.11" because `.venv` only has stub dist-info directories). That interpreter has `streamlit==1.16.0` (released ~Nov 2022) alongside `altair==5.0.1`. Streamlit 1.16's internal chart code imports `altair.vegalite.v4`, a module path Altair removed in its 5.0 release. [CITED: discuss.streamlit.io/t/modulenotfounderror-no-module-named-altair-vegalite-v4]
**How to avoid:** Upgrade Streamlit (`pip install --upgrade "streamlit>=1.37"` in the same interpreter) as a Wave 0 / setup task, before any dashboard code is written. This must be verified with `python3.11 -m streamlit --version` succeeding before proceeding.
**Warning signs:** Any `ImportError`/`ModuleNotFoundError` mentioning `altair` when running `dashboard.py`.

### Pitfall 2: `pyproject.toml` doesn't declare the packages the project actually runs on
**What goes wrong:** A fresh clone + `pip install -e .` (or `pip install .`) would NOT install `xgboost`, `scikit-learn`, `joblib`, `scipy`, `streamlit`, or `plotly` — the project currently only works because these happen to be pre-installed in the Anaconda base environment being used as if it were the project's venv.
**Why it happens:** `pyproject.toml`'s `dependencies` array was written in Phase 1 and never updated in Phase 2 when XGBoost/scikit-learn/joblib/scipy were introduced.
**How to avoid:** Phase 3 should add `streamlit` and `plotly` to `dependencies`, and ideally also backfill the Phase-2-introduced packages (`xgboost`, `scikit-learn`, `joblib`, `scipy`) that are silently missing today — otherwise a genuinely fresh environment (e.g. CI, another developer's machine) cannot run any of this project's code.
**Warning signs:** `pip install -e .` in a clean venv, then any `import xgboost` / `import streamlit` failing.

### Pitfall 3: `team_colour` from OpenF1 has no `#` prefix
**What goes wrong:** Passing the raw OpenF1 `team_colour` string (e.g. `"3671C6"`) directly to Plotly's `marker_color` may be silently misinterpreted or rejected depending on Plotly's color-string parsing.
**Why it happens:** OpenF1 returns team colors as bare 6-digit hex without a leading `#`.
**How to avoid:** Always format as `f"#{team_colour}"` before passing to Plotly, and store the raw (un-prefixed) value in the DB so the API contract stays a faithful mirror of OpenF1 (consistent with the project's "mirror OpenF1 field names" convention in `db/models.py`).
**Warning signs:** Bars rendering in a fallback/default color instead of team colors.

### Pitfall 4: Feature-column drift between `scripts/train.py` and the new inference code
**What goes wrong:** If `pipeline.py` reimplements `PRERACE_FEATURE_COLS` / `INRACE_NUMERIC_FEATURE_COLS` / `encode_tyre_compound` instead of importing them, a future edit to one copy (e.g. adding a feature column in a later phase) silently desyncs training-time and inference-time feature order — XGBoost will not error, it will just produce wrong predictions on mismatched columns.
**Why it happens:** `scripts/train.py` is a standalone CLI script, not a library module, so nothing currently stops a second implementation of the same constants from drifting.
**How to avoid:** Extract the feature-column constants and `encode_tyre_compound`/`build_inrace_feature_matrix`/`predict_*_with_softmax` functions into a shared importable module (`f1_prediction/inference/features.py` or similar) that both `scripts/train.py` and the new `pipeline.py` import from a single source of truth.
**Warning signs:** Predictions that don't sum to ~1.0 per race/lap, or predictions that look nonsensical despite the model files being unchanged.

### Pitfall 5: `predict_proba` column-order sensitivity
**What goes wrong:** `CalibratedClassifierCV.predict_proba(X)` expects `X`'s columns in exactly the order used at `.fit()` time. If `pipeline.py`'s DataFrame-to-array conversion reorders columns (e.g. by reading from a differently-ordered SQL SELECT), predictions silently become garbage with no exception raised.
**Why it happens:** `scripts/train.py` builds `X_train` via `df[PRERACE_FEATURE_COLS].values` / `build_inrace_feature_matrix(df).values` — order is implicit in the list/concat order, not enforced by column names at predict time.
**How to avoid:** At inference time, always select/reindex the DataFrame to the exact same column list constants used at training time (reason to share the module — Pitfall 4) before calling `.values`.
**Warning signs:** Same as Pitfall 4 — no crash, just wrong/degenerate probability distributions.

## Code Examples

### Determining the "latest race session" (D-01)
```python
# Pattern derived from raw_sessions schema in db/models.py — no existing
# helper does this yet; recommend adding to a new `pipeline.py` or a
# `live/session_lookup.py` module.
from sqlalchemy import text

def get_latest_race_session_key(engine) -> int | None:
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT session_key FROM raw_sessions "
            "WHERE session_type = 'Race' "
            "ORDER BY year DESC, round_number DESC, session_key DESC "
            "LIMIT 1"
        )).fetchone()
    return int(row[0]) if row else None
```
Note: `round_number` is only reliably populated after `backfill.py`'s `fix_round_numbers()` step per `RawSession`'s docstring — verify this holds for the live-poller-inserted row too (OpenF1 sessions are inserted with `round_number=0` before that fix runs), or fall back to `ORDER BY session_key DESC` alone if `round_number` can't be trusted for the newest, not-yet-backfilled session.

### `predictions` table (D-10 schema)
```python
# Recommended addition to db/models.py, following the existing style
class Prediction(Base):
    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_key = Column(Integer, nullable=False, index=True)
    driver_number = Column(Integer, nullable=False)
    win_probability = Column(Float, nullable=False)
    prediction_type = Column(String, nullable=False)   # 'prerace' | 'inrace'
    lap_number = Column(Integer, nullable=True)          # NULL for prerace rows
    computed_at = Column(String, nullable=False)         # ISO 8601 string, matches project convention

    __table_args__ = (
        CheckConstraint(
            "prediction_type IN ('prerace', 'inrace')",
            name="ck_predictions_prediction_type",
        ),
        Index("ix_predictions_session_type_lap", "session_key", "prediction_type", "lap_number"),
    )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `st.experimental_fragment` decorator with `run_every` | `st.fragment` (stable) | Streamlit 1.37.0, July 2024 [CITED: docs.streamlit.io/develop/quick-reference/release-notes] | The experimental decorator is deprecated; must use `st.fragment` in any Streamlit ≥1.37 install (which this phase requires anyway) |
| `st.experimental_rerun()` | `st.rerun()` | Streamlit 1.27 (2023) | Irrelevant once `st.fragment(run_every=)` is used instead of a manual rerun loop, but avoid the experimental name if any manual rerun is ever needed |
| CLAUDE.md's Dash + `dcc.Interval` recommendation | Streamlit + `st.fragment(run_every=)` | Locked pivot recorded in PROJECT.md Key Decisions (predates this research session) | CLAUDE.md's Technology Stack section is stale on this point per 03-CONTEXT.md's explicit instruction — Streamlit is correct, not Dash |

**Deprecated/outdated:**
- `altair.vegalite.v4` import path: removed in Altair 5.0; only relevant because it's what's currently breaking the installed Streamlit 1.16.0.
- `st.experimental_fragment`: deprecated as of Streamlit 1.37, scheduled removal after Jan 1 2025 (per Streamlit's own changelog) — do not use in new code.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | OpenF1 `/v1/drivers` returns exactly the fields `broadcast_name, driver_number, first_name, full_name, headshot_url, last_name, meeting_key, name_acronym, session_key, team_colour, team_name` | Architecture Patterns (Pattern 5) | If the live API differs (e.g. field renamed/removed), the `_DRIVER_FIELDS` whitelist in the new client method would silently produce `None` for a missing field via `.get()` — low risk (matches existing `_extract()` tolerant pattern) but should be confirmed with one live `curl`/test call during implementation, not just this research |
| A2 | `team_colour` is returned without a `#` prefix | Common Pitfalls (Pitfall 3) | If OpenF1 actually includes `#`, the `f"#{team_colour}"` formatting would produce `"##3671C6"` — trivially caught by a visual dashboard check in first manual test |
| A3 | Streamlit ≥1.37 is the correct minimum for `st.fragment` (non-experimental) with `run_every` | Standard Stack, Pitfall 1 | If wrong, `st.fragment` usage would raise `AttributeError` immediately — fails loudly, low risk |
| A4 | `round_number` is reliably populated for the live-poller-inserted "latest" race session at the moment `pipeline.py` starts (needed for the `ORDER BY year DESC, round_number DESC` query in D-01's session-lookup) | Code Examples | If `round_number=0` for a freshly-inserted OpenF1 session (before any `fix_round_numbers()`-style correction runs), the ORDER BY could pick the wrong session on the same day multiple sessions exist; fallback of `ORDER BY session_key DESC` (OpenF1 keys are server-assigned and monotonically increasing) is a safer sole tiebreaker — planner should decide which to use |

## Open Questions

1. **Does OpenF1 provide a "current/latest session" endpoint that's simpler than DB-side `MAX`?**
   - What we know: `/v1/sessions?session_key=latest` is a documented OpenF1 convenience alias in some contexts, but D-01 explicitly says the source of truth is "most recent Race-type session_key in the DB," not a live API call — so this is likely moot for `pipeline.py`, but could simplify a startup double-check.
   - What's unclear: whether relying purely on DB state (vs. confirming against OpenF1 directly) could pick a stale session_key if the DB hasn't been backfilled/polled for the current weekend yet.
   - Recommendation: keep D-01's DB-only approach for v1 as decided; this is a Phase 4 concern (real live-race validation) more than a Phase 3 one.

2. **Should `raw_drivers` be re-fetched every pipeline startup, or only once per session_key if absent?**
   - What we know: D-06 leaves caching strategy to Claude's discretion; team colors/names don't change mid-weekend.
   - What's unclear: whether a driver could be added mid-weekend (e.g. reserve driver substitution) requiring a re-fetch.
   - Recommendation: fetch once at `pipeline.py` startup per session_key (mirrors the D-11 idempotent pre-race-prediction pattern: check-then-skip), matching the project's existing resumability convention.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| streamlit | DASH-01–04 (dashboard process) | ✗ (installed but broken) | 1.16.0 installed, currently non-functional; 1.59.0 latest on PyPI | Upgrade required — no viable fallback; this blocks all dashboard work until fixed (Pitfall 1) |
| plotly | DASH-01, D-05 (team-colored bar chart) | ✓ | 5.22.0 (installed, working); 6.8.0 latest on PyPI | — |
| xgboost | Model inference | ✓ | 3.2.0 | — |
| scikit-learn | `CalibratedClassifierCV.predict_proba` | ✓ | 1.5.1 | — |
| joblib | Load `.joblib` model artifacts | ✓ | 1.4.2 | — |
| scipy | Softmax normalization | ✓ (imported successfully in `scripts/train.py`, confirmed via prior test runs) | not independently version-checked this session | — |
| SQLite (stdlib) | `predictions`/`raw_drivers` tables, WAL mode | ✓ | 3.x via Python 3.11 stdlib | — |
| `data/models/prerace_model.joblib`, `data/models/inrace_model.joblib` | Inference | ✓ | Phase 2 artifacts present in repo (`git status` shows both modified, i.e. present) | — |
| Package manager reconciliation (`pyproject.toml` vs. actual interpreter) | Reproducible builds | ✗ | `pyproject.toml` lists 7 deps; interpreter has ~13 relevant packages installed ad hoc | No safe fallback — must be fixed for the project to be installable outside this one Anaconda environment (Pitfall 2) |

**Missing dependencies with no fallback:**
- A working Streamlit install (upgrade required — see Pitfall 1). This blocks DASH-01 through DASH-04 entirely until resolved.
- Accurate `pyproject.toml` dependency declarations (Pitfall 2) — not a hard blocker for local dev (packages are present), but blocks any clean-environment reproduction of this project.

**Missing dependencies with fallback:** none identified — the Streamlit issue has no viable fallback other than fixing it directly.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-------------------|
| V2 Authentication | No | DASH-04 explicitly requires no authentication; this is a locked, local-only-deployment decision (PROJECT.md, HOST-01 deferred). No control needed — do not add auth. |
| V3 Session Management | No | No user sessions/accounts exist; Streamlit's own internal `session_state` is process-local, not a security boundary here. |
| V4 Access Control | No | Single-user local tool; no roles or permissions to enforce. |
| V5 Input Validation | Partial — yes for the OpenF1 response data | The dashboard takes no direct user input beyond tab selection. The new `/v1/drivers` ingestion path must use the same per-field whitelist extraction (`_extract()`) already used for `laps`/`stints`/`pit`/`position` — never insert raw API response dicts directly into SQL. |
| V6 Cryptography | No | No secrets, tokens, or encrypted data are introduced by this phase. |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|----------------------|
| SQL injection via unvalidated OpenF1 response fields flowing into raw SQL | Tampering | Continue the project's established `text()` + bound `:param` convention (already enforced project-wide per T-0X-05 comments throughout the codebase) — apply identically to any new `raw_drivers` insert/query code |
| Denial of service via unbounded OpenF1 polling frequency (drivers endpoint hit too often) | Denial of Service (self-inflicted, against OpenF1's rate limits) | Fetch `/v1/drivers` at most once per pipeline startup per session_key (Open Question 2), not on every 30s poll tick — reuse the project's existing `RateLimitedError`/tenacity backoff on `_fetch()` regardless |
| Local network exposure of the dashboard beyond `localhost` | Information Disclosure | `streamlit run` defaults to binding `localhost` unless `--server.address 0.0.0.0` is explicitly passed; do not pass that flag — DASH-04's "no authentication" is only safe under a strict localhost-only bind, which must not be widened in this phase (HOST-01 hosting is explicitly out of scope) |
| Stale/incorrect win probabilities silently displayed (not a classic STRIDE category, but a correctness-as-safety concern for a "must be useful during the race" product) | — | Surface `computed_at`/`lap_number` in the dashboard UI so a user can visually confirm the displayed probabilities are current, rather than trusting the auto-refresh silently |

## Sources

### Primary (HIGH confidence)
- Codebase inspection (this session): `src/f1_prediction/db/models.py`, `db/engine.py`, `db/upsert.py`, `live/poller.py`, `live/session_window.py`, `features/builder.py`, `features/inrace.py`, `features/prerace.py`, `ingest/openf1_client.py`, `scripts/train.py`, `pyproject.toml`, `.venv/pyvenv.cfg` — all read directly in this session.
- Live environment probes (this session): `which python3.11`, `pip show streamlit`, `pip index versions streamlit/plotly/altair`, `python3.11 -m streamlit --version` (reproduced the crash), `python3.11 -m src.f1_prediction.db.engine` (confirmed the `python -m src.f1_prediction.X` invocation style works today).
- `.planning/PROJECT.md` §Key Decisions, `.planning/ROADMAP.md` §Phase 3, `.planning/REQUIREMENTS.md`, `.planning/phases/03-prediction-pipeline-dashboard/03-CONTEXT.md`, `.planning/phases/02-model-training/02-CONTEXT.md`, `.planning/phases/01-data-features/01-REVIEW-FIX.md` (CR-03 bug reference) — all read directly in this session.

### Secondary (MEDIUM confidence)
- [st.fragment — Streamlit Docs](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment) — via WebSearch, not independently fetched with WebFetch this session
- [Streamlit Release notes](https://docs.streamlit.io/develop/quick-reference/release-notes) — st.fragment stabilization in 1.37.0
- [ModuleNotFoundError: No module named 'altair.vegalite.v4' — Streamlit community](https://discuss.streamlit.io/t/modulenotfounderror-no-module-named-altair-vegalite-v4/42921)
- [Support Altair 5 · Issue #6215 · streamlit/streamlit](https://github.com/streamlit/streamlit/issues/6215)
- [Horizontal bar charts in Python — Plotly docs](https://plotly.com/python/horizontal-bar-charts/)
- [plotly.graph_objects.Bar — API reference](https://plotly.com/python-api-reference/generated/plotly.graph_objects.Bar.html)
- [Write-Ahead Logging — sqlite.org](https://sqlite.org/wal.html)
- [st.cache_resource — Streamlit Docs](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_resource)
- [Caching overview — Streamlit Docs](https://docs.streamlit.io/develop/concepts/architecture/caching)

### Tertiary (LOW confidence)
- OpenF1 `/v1/drivers` exact field list — sourced from WebSearch summarization of openf1.org/docs content, not independently confirmed via a live API call or WebFetch of the docs page directly in this session. **Flagged in Assumptions Log (A1) — verify with one live request during implementation.**

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH for xgboost/scikit-learn/joblib/scipy/sqlalchemy (directly verified installed + matches existing code); MEDIUM for streamlit/plotly exact latest-version numbers (verified via `pip index`, a point-in-time snapshot) and the recommended `st.fragment` approach (web-sourced, not independently fetched from docs.streamlit.io via WebFetch)
- Architecture: HIGH — derived directly from reading the actual Phase 1/2 source files, not assumed
- Pitfalls: HIGH for Pitfall 1 (reproduced the crash directly) and Pitfall 2 (directly diffed `pyproject.toml` against installed packages); MEDIUM for Pitfalls 3–5 (reasoned from OpenF1 field-naming conventions and existing code patterns, not exhaustively tested)

**Research date:** 2026-07-07
**Valid until:** 30 days for the Streamlit/Plotly version numbers and pip-index snapshot (fast-moving PyPI state); the architectural/codebase findings remain valid until the underlying Phase 1/2 code changes.
