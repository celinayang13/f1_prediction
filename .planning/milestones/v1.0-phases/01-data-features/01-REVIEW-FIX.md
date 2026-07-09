---
phase: 01
fixed_at: 2026-06-17T00:00:00Z
review_path: .planning/phases/01-data-features/01-REVIEW.md
iteration: 1
findings_in_scope: 8
fixed: 8
skipped: 0
status: all_fixed
---

# Phase 01: Code Review Fix Report

**Fixed at:** 2026-06-17T00:00:00Z
**Source review:** `.planning/phases/01-data-features/01-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 8 (3 Critical + 5 Warning)
- Fixed: 8
- Skipped: 0

**Test suite:** 34/34 passed after all fixes (`PYTHONPATH=src python3.11 -m pytest tests/ -q --tb=short`)

## Fixed Issues

### CR-01: NameError crash in stint normalizer when all driver laps have null LapNumber

**Files modified:** `src/f1_prediction/ingest/normalizer.py`
**Commit:** `74d3a14`
**Applied fix:** Added `last_valid_lap_num = None` before the inner `for _, lap_row in group.iterrows()` loop. After `lap_num` is confirmed non-None, added `last_valid_lap_num = lap_num` to track the most recent valid lap. In the `else` block, replaced `lap_num` with `last_valid_lap_num` and added `last_valid_lap_num is not None` guard to the existing `prev_compound is not None and stint_start_lap is not None` check.

---

### CR-02: `meeting_key` stored as `round_number` for all OpenF1 sessions

**Files modified:** `src/f1_prediction/ingest/openf1_loader.py`
**Commit:** `334dd60`
**Applied fix:** In `_normalize_openf1_session`, changed `int(raw.get("meeting_key", 0))` to `int(raw.get("round_number") or raw.get("meeting_round_number") or 0)`. In `load_openf1_year`, changed the rounds filter to check `sess.get("round_number") or sess.get("meeting_round_number")` instead of `sess.get("meeting_key")`, so `--rounds 1 2 3` correctly matches sequential round numbers.

---

### CR-03: `interval_ahead` silently produces wrong values — first merge joins car to itself

**Files modified:** `src/f1_prediction/features/inrace.py`
**Commit:** `07f28e9`
**Applied fix:** Removed the dead `gap_ahead` DataFrame construction (3 lines building `position_plus1` and renaming to `gap_ahead_leader`) and the first erroneous merge (joining position P to position P). Kept only the correct second merge that joins car at position P to the car at position P-1. Added a comment explaining why the dead code was removed.

---

### WR-01: TOCTOU race in `insert_or_skip_session`

**Files modified:** `src/f1_prediction/db/upsert.py`
**Commit:** `cb88740`
**Applied fix:** Imported `IntegrityError` from `sqlalchemy.exc`. Replaced the two-step SELECT-then-INSERT pattern (two separate connection opens with a TOCTOU race between them) with a single `Session` context that does `session.add()` + `session.commit()` and catches `IntegrityError` for the already-exists case, rolling back and returning False.

---

### WR-02: `build_features_for_session` opens a second DB connection while the first is still open

**Files modified:** `src/f1_prediction/features/builder.py`
**Commit:** `f6f1e0d`
**Applied fix:** Moved the `if not rows: return 0` guard and the entire bulk-insert block (previously nested inside the outer `with engine.connect() as conn:` block) to outside and after that block, so the outer connection is fully closed before the insert connection opens. Renamed the inner connection to `conn_insert` for clarity. Removed the dead `if rows:` guard that was unreachable after `if not rows: return 0`.

---

### WR-03: `compute_constructor_pace_delta` SQL divides by zero / COALESCE bias

**Files modified:** `src/f1_prediction/features/prerace.py`
**Commit:** `bbf5a23`
**Applied fix:** Replaced the `(COALESCE(fp2, 0) + COALESCE(fp3, 0)) / count_non_null` expression with a `CASE` expression that returns `(fp2 + fp3) / 2.0` when both are present, `fp2` when only FP2 is available, and `fp3` when only FP3 is available. This eliminates the zero-substitution bias that underestimated constructor pace when only one session had data.

---

### WR-04: `_normalize_openf1_pits` defaults `lap_number` to 0 — stores invalid pit stop record

**Files modified:** `src/f1_prediction/ingest/openf1_loader.py`
**Commit:** `0574077`
**Applied fix:** In `_normalize_openf1_pits`, extracted `lap_num = _safe_int(p.get("lap_number"))` and added `if lap_num is None: continue` to skip records with null lap_number rather than defaulting to 0. In `_normalize_openf1_stints`, extracted `lap_start` and `lap_end` separately and added `if lap_start is None or lap_end is None: continue` to skip stints with null lap boundaries rather than defaulting to 1. Both add a `logger.debug` notice for discarded records.

---

### WR-05: numpy int64 key mismatch in `championship_position`

**Files modified:** `src/f1_prediction/features/prerace.py`
**Commit:** `70a728f`
**Applied fix:** Added `driver_number = int(driver_number)` at the top of `compute_championship_position`, before any dict operations, so numpy int64 callers cannot create a second dict key alongside the Python int keys built from SQL results.

---

_Fixed: 2026-06-17T00:00:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
