---
phase: 01
status: has-findings
critical: 3
warning: 5
info: 4
---

# Phase 01: Code Review Report

**Reviewed:** 2026-06-17T00:00:00Z
**Depth:** standard
**Files Reviewed:** 16
**Status:** has-findings

## Summary

Reviewed all 16 source files from the data-features phase. The code has solid structural
foundations: parameterized SQL is used consistently, the DB engine is well-configured with
WAL mode, and the ingest pipeline correctly separates FastF1 (2022) and OpenF1 (2023+)
paths. However, three blockers were found: a potential `NameError` crash in the stint
normalizer, a silent data corruption bug where `meeting_key` is stored as `round_number`
for all OpenF1 sessions (breaking every cross-session join), and a logical error that
silently computes incorrect `interval_ahead` values for all in-race rows. Several warnings
cover a TOCTOU race in session deduplication, a dead-code block that wastes a DB
connection, championship point type coercion fragility, and missing lap-number bounds
validation in the pit stop normalizer.

---

## Critical

### CR-01: `NameError` crash in stint normalizer when all driver laps have null `LapNumber`

**File:** `src/f1_prediction/ingest/normalizer.py:514`

**Issue:** In `normalize_fastf1_stints`, the `for/else` clause at line 505 runs after the
inner `for _, lap_row in group.iterrows()` loop completes normally (i.e., not via `break`).
If every lap in `group` has `lap_num is None` (line 478), the inner loop executes only
`continue` statements and `lap_num` is never assigned in the local scope. When the `else`
block then references `lap_num` on line 514, Python raises `UnboundLocalError: local
variable 'lap_num' referenced before assignment`. This crash propagates up through
`load_session`, aborting the entire backfill for the affected year.

```python
# Buggy for/else (line 474-517):
for _, lap_row in group.iterrows():
    lap_num = _to_int_or_none(lap_num_raw)
    if lap_num is None:
        continue          # lap_num may never be set
    ...
else:
    ...
    "lap_end": lap_num,   # NameError if all laps skipped
```

**Fix:** Guard the `else` block with a last-valid-lap tracker:

```python
last_valid_lap_num = None  # add before the inner for loop

for _, lap_row in group.iterrows():
    lap_num = _to_int_or_none(lap_num_raw)
    if lap_num is None:
        continue
    last_valid_lap_num = lap_num  # track the most recent valid lap
    ...
else:
    if prev_compound is not None and stint_start_lap is not None and last_valid_lap_num is not None:
        stints.append({
            ...
            "lap_end": last_valid_lap_num,  # use tracked value, not unbound lap_num
            ...
        })
```

---

### CR-02: `meeting_key` stored as `round_number` for all OpenF1 sessions — breaks every cross-session join

**File:** `src/f1_prediction/ingest/openf1_loader.py:65`

**Issue:** `_normalize_openf1_session` maps `round_number` to `raw.get("meeting_key", 0)`.
In OpenF1, `meeting_key` is a large server-assigned integer (e.g., 1219 for Bahrain 2024),
not a sequential round number (1, 2, 3, …). Every downstream query that joins or filters
on `round_number` is broken for all 2023+ data:

- `compute_qualifying_delta` looks up `WHERE year = :year AND round_number = :rn` to find
  the matching Qualifying session — it will never find a match because `round_number` will
  be 1219 instead of 1.
- `compute_championship_position` queries `WHERE rs.year = :year AND rs.round_number < :rn`
  using the wrong values.
- The `UniqueConstraint(year, round_number, session_type)` will silently deduplicate
  sessions that happen to share the same `meeting_key` across years (unlikely but possible
  given OpenF1's key scheme).

The `rounds` filter in `load_openf1_year` also filters on `meeting_key`, meaning `--rounds
1 2 3` silently matches nothing for 2023+ data (line 430-432 compares `meeting_key` to the
rounds list, while CLI docs tell users to pass round numbers).

**Fix:** OpenF1 does not expose a sequential round number directly. Use OpenF1's
`round_key` field if available, or derive it from meeting sort order. At minimum, add a
lookup step or store `meeting_key` only in the `meeting_key` column and derive
`round_number` via the ordered session list:

```python
# Option 1: use round_key if OpenF1 exposes it
"round_number": int(raw.get("round_key") or raw.get("meeting_key", 0)),

# Option 2: caller derives round_number from the sorted session list index
# and passes it explicitly to _normalize_openf1_session
```

Document the approximation clearly and add a test that verifies qualifying lookup works
end-to-end for a 2023 session.

---

### CR-03: `interval_ahead` silently produces wrong values — first merge joins car to itself

**File:** `src/f1_prediction/features/inrace.py:162-181`

**Issue:** The `interval_ahead` computation uses two sequential merges, but the first merge
(lines 162-169) is logically incorrect and its output is never used. The merge joins
`laps_df` using `left_on=["lap_number", "position"]` against `right_on=["lap_number",
"pos_ahead"]` where `pos_ahead` is just a rename of the original `position` column. This
join matches each car to itself (position P to position P), producing a useless
`gap_ahead_val` column that adds phantom columns to the DataFrame.

The second merge (lines 173-180) then correctly joins car at position P to the car at P-1
using `pos_ahead_adj = position - 1`. However, because the first merge already added
`pos_ahead` and `gap_ahead_val` columns to `laps_df`, the second merge on the same
DataFrame may hit column-name conflicts or produce duplicate rows if `gap_by_pos` has
duplicate (lap_number, position) rows (possible if positions data has duplicates not caught
by deduplication).

The real corruption risk: after the first merge introduces `pos_ahead` as a column,
`laps_df["position"]` used in `laps_df["pos_ahead_adj"] = laps_df["position"] - 1` may
refer to a widened/coerced `position` column that lost integer precision due to Float64
upcast from the first merge, causing the second merge's join key to miss.

**Fix:** Remove the first merge entirely — it is dead code that produces only `pos_ahead`
and `gap_ahead_val`, neither of which is used:

```python
# REMOVE these lines (162-169) — dead code:
# gap_ahead = gap_by_pos[...].copy()
# gap_ahead["position_plus1"] = ...
# gap_ahead = gap_ahead.rename(...)
# laps_df = laps_df.merge(
#     gap_by_pos[...].rename(columns={"position": "pos_ahead", ...}),
#     left_on=["lap_number", "position"],
#     right_on=["lap_number", "pos_ahead"],
#     how="left",
# )

# Keep only the second merge for interval_ahead (using pos - 1):
laps_df["pos_ahead_adj"] = laps_df["position"].astype("Float64") - 1
laps_df = laps_df.merge(
    gap_by_pos[["lap_number", "position", "gap_to_leader"]].rename(
        columns={"position": "pos_m1", "gap_to_leader": "gap_m1"}
    ),
    left_on=["lap_number", "pos_ahead_adj"],
    right_on=["lap_number", "pos_m1"],
    how="left",
)
laps_df["interval_ahead"] = laps_df["gap_to_leader"] - laps_df["gap_m1"]
```

---

## Warning

### WR-01: TOCTOU race in `insert_or_skip_session` — check and insert are non-atomic

**File:** `src/f1_prediction/db/upsert.py:45-54`

**Issue:** `insert_or_skip_session` checks for existence in one connection (lines 45-49),
closes that connection, then opens a new Session to insert (lines 52-54). Between the
two operations another process (or thread) could insert the same `session_key`, causing
the insert to fail with an `IntegrityError` (primary key violation on `session_key`) or
a `UniqueConstraint` violation on `(year, round_number, session_type)`. The function
neither catches `IntegrityError` nor retries, so the caller in `openf1_loader.py`
receives an unhandled exception.

**Fix:** Use `INSERT OR IGNORE` (SQLite-specific) or wrap the insert in a try/except
`IntegrityError`:

```python
from sqlalchemy.exc import IntegrityError

with Session(engine) as session:
    try:
        session.add(RawSession(**session_row))
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        logger.debug("Session %d already exists (concurrent insert) — skipping", sk)
        return False
```

---

### WR-02: `build_features_for_session` opens a second DB connection while the first is still open

**File:** `src/f1_prediction/features/builder.py:315-324`

**Issue:** The bulk-insert step (lines 315-324) opens a second engine connection (`conn2`)
while the outer `with engine.connect() as conn:` block (line 187) is still active and
holds the connection open. SQLite's WAL mode tolerates concurrent readers but only one
writer at a time. When the outer `conn` still has a pending transaction (it committed the
DELETE on line 219 but the connection is not yet closed), opening a second write connection
can cause a `sqlite3.OperationalError: database is locked` under SQLite's default timeout.

Additionally, `if not rows: return 0` (line 309-310) and `if rows:` (line 313) are
tautological — the second check is dead code (the function already returned 0 if `rows`
is empty). This suggests a refactor was abandoned mid-way.

**Fix:** Close the outer connection before opening the second one for insertion, or perform
the insert using the same connection:

```python
# After computing rows, exit the outer with-block before inserting:
with engine.connect() as conn:
    ...  # compute rows
    # do NOT nest another engine.connect() here

# Then insert in a fresh connection:
if rows:
    df_insert = pd.DataFrame(rows)
    with engine.connect() as conn:
        df_insert.to_sql(table_name, conn, if_exists="append",
                         index=False, method="multi", chunksize=500)
        conn.commit()
```

Also remove the redundant `if rows:` guard on line 313 — it is unreachable because
`if not rows: return 0` already handled the empty case.

---

### WR-03: `compute_constructor_pace_delta` SQL divides by zero when both FP2 and FP3 are null — silently filtered by WHERE clause but fragile

**File:** `src/f1_prediction/features/prerace.py:286-296`

**Issue:** The `combined_pace` expression divides by the count of non-null practice pace
columns:

```sql
(COALESCE(fp.practice_pace_fp2, 0) + COALESCE(fp.practice_pace_fp3, 0))
/
((CASE WHEN fp.practice_pace_fp2 IS NOT NULL THEN 1 ELSE 0 END) +
 (CASE WHEN fp.practice_pace_fp3 IS NOT NULL THEN 1 ELSE 0 END))
```

The outer `WHERE (fp.practice_pace_fp2 IS NOT NULL OR fp.practice_pace_fp3 IS NOT NULL)`
clause prevents rows where both are NULL from reaching the division. However, if a row has
both non-null values in the WHERE clause (so it passes the filter) but the CASE expressions
evaluate to 0 for both (which cannot happen given the WHERE, but is unclear to a reader),
SQLite would produce a division-by-zero result as NULL silently. The real risk is that the
query silently treats a driver with only FP2 data as equivalent to one with both sessions:
`COALESCE(fp2, 0)` substitutes 0 for missing FP3, not the FP2 value again, which
underestimates the constructor pace when FP3 is absent.

**Fix:** Use `NULLIF` to make the intent explicit and avoid the 0-substitution bias:

```sql
(
  COALESCE(fp.practice_pace_fp2, fp.practice_pace_fp3) +
  COALESCE(fp.practice_pace_fp3, fp.practice_pace_fp2)
) / 2.0
```

Or use a proper conditional average:

```sql
CASE
  WHEN fp.practice_pace_fp2 IS NOT NULL AND fp.practice_pace_fp3 IS NOT NULL
       THEN (fp.practice_pace_fp2 + fp.practice_pace_fp3) / 2.0
  WHEN fp.practice_pace_fp2 IS NOT NULL THEN fp.practice_pace_fp2
  ELSE fp.practice_pace_fp3
END AS combined_pace
```

---

### WR-04: `_normalize_openf1_pits` defaults `lap_number` to 0 — stores invalid pit stop record

**File:** `src/f1_prediction/ingest/openf1_loader.py:141`

**Issue:** `"lap_number": _safe_int(p.get("lap_number")) or 0` defaults to lap 0 when
`lap_number` is missing or null. Lap 0 does not exist in a race session. The feature
builder uses `WHERE pit_lap <= lap_number` (effectively `0 <= 1`), so a phantom pit stop
on lap 0 is always counted for every lap of the race, inflating every driver's
`pit_stop_count` by 1 and producing a phantom `pit_stop_delta` on lap 0.

The same pattern appears for `lap_start` and `lap_end` in `_normalize_openf1_stints`
(line 122-123): `"lap_start": _safe_int(s.get("lap_start")) or 1` and
`"lap_end": _safe_int(s.get("lap_end")) or 1` both default to lap 1, potentially
creating a zero-length or invalid stint.

**Fix:** Discard records with null `lap_number` rather than defaulting to 0:

```python
lap_num = _safe_int(p.get("lap_number"))
if lap_num is None:
    continue  # skip pit stops without a lap reference
rows.append({
    ...
    "lap_number": lap_num,
    ...
})
```

---

### WR-05: `championship_position` uses `driver_number` (int) as dict key but queries may return numpy int64 — silent mismatch possible

**File:** `src/f1_prediction/features/prerace.py:362-373`

**Issue:** `compute_championship_position` builds a `points` dict keyed by `int(dn)` from
SQL results (line 364). It then checks `if driver_number not in points` (line 366) using
the `driver_number` argument passed in. If the caller passes a numpy int64 (e.g., from a
pandas DataFrame column) and the dict was built with Python int keys, the `in` check
evaluates correctly in CPython (numpy int64 compares equal to Python int), but the code
also does `points[driver_number] = 0.0` on line 367 using the original numpy int64 as a
key, creating a second entry in the dict. The subsequent `sorted_drivers` loop then
iterates both the Python int key and the numpy int64 key, potentially returning two
different ranks for the same driver number.

**Fix:** Normalise `driver_number` to Python int at the function boundary:

```python
def compute_championship_position(
    driver_number: int, year: int, round_number: int, conn
) -> Optional[int]:
    driver_number = int(driver_number)  # normalise at entry
    ...
```

---

## Info

### IN-01: Dead code — `gap_ahead` DataFrame built but never used in `compute_inrace_features_for_session`

**File:** `src/f1_prediction/features/inrace.py:157-159`

**Issue:** Lines 157-159 build a `gap_ahead` DataFrame with renamed columns and a
`position_plus1` column. Neither `gap_ahead`, `position_plus1`, nor `gap_ahead_leader`
appears anywhere else in the function. This is entirely unused dead code left over from
a development iteration.

**Fix:** Remove lines 157-159.

---

### IN-02: `_get_drivers_for_session` in `builder.py` is defined but never called

**File:** `src/f1_prediction/features/builder.py:84-129`

**Issue:** `_get_drivers_for_session` is a complete function that queries
`raw_laps` and looks up `team_name` from `features_prerace`. The actual
`build_features_for_session` function duplicates this logic inline (lines 241-245 for
drivers, lines 278 for team_name via `_get_team_name_from_session`). The unused function
adds 46 lines of dead code and creates confusion about which code path is authoritative.

**Fix:** Remove `_get_drivers_for_session` entirely since its logic is inlined at the
call site.

---

### IN-03: `normalize_fastf1_laps` drops `is_pit_in_lap` / `tyre_compound_observed` / segment columns — OpenF1 columns silently absent for 2022 data

**File:** `src/f1_prediction/ingest/normalizer.py:285-301`

**Issue:** The `normalize_fastf1_laps` function assembles the `row` dict with only 12 of
the 16 columns in `RawLap`. It silently omits `is_pit_in_lap`, `tyre_compound_observed`,
`segments_sector_1/2/3`. These columns will be NULL for all 2022 (FastF1) lap rows.
While this is documented behavior, there is no log-level notice when the function returns
rows lacking these columns, making it easy for future consumers to assume these fields are
uniformly populated.

**Fix:** Add a single `logger.debug` noting which columns are omitted for FastF1 source,
or add inline comments on the dict literal for each missing key. No functional change
required.

---

### IN-04: Magic number 30 in `session_window.py` — undocumented polling window constant

**File:** `src/f1_prediction/live/session_window.py:110-111`

**Issue:** The ±30-minute window is repeated as a literal `timedelta(minutes=30)` in both
`is_session_active` (lines 110-111) and `find_current_session` (lines 166-167) without a
named constant. If this padding ever needs adjusting, it must be changed in two places.

**Fix:**

```python
_WINDOW_PAD = timedelta(minutes=30)  # add at module level

# Then use:
window_start = date_start - _WINDOW_PAD
window_end = date_end + _WINDOW_PAD
```

---

_Reviewed: 2026-06-17T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
