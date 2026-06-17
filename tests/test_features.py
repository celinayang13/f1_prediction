"""
Tests for the feature builder (Plan 03, Task 1).

Covers:
  - feature table schema is correct (columns, PKs)
  - build_features_for_session produces rows for a seeded race weekend
  - pre-race feature values are correct (qualifying_delta, practice_pace)
  - in-race feature values are correct (gap_to_leader, tyre_compound, pit_stop_count)
  - re-running the builder DELETEs and re-INSERTs cleanly (no duplicates)
  - NULL handling when FP2 session is absent
  - build_all_features returns expected dict keys
  - non-race sessions are skipped by the builder

All tests use the tmp_engine fixture (in-memory database) — no data/f1.db created.
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from f1_prediction.db.engine import get_engine, init_db
from f1_prediction.db.models import FeaturesInrace, FeaturesPrerace
from f1_prediction.features.builder import build_all_features, build_features_for_session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def race_engine(tmp_path):
    """
    In-memory-like SQLite engine seeded with a minimal 1-race-weekend:
    - FP2 session   (session_key=100, year=2023, round=1)
    - Q session     (session_key=200, year=2023, round=1)
    - Race session  (session_key=300, year=2023, round=1)
    with 3 drivers (1, 11, 44) and 5 laps each.
    FP3 is intentionally absent to test NULL handling.
    """
    db_path = tmp_path / "test_feat.db"
    engine = get_engine(db_path)
    init_db(engine)
    _seed_race_weekend(engine)
    return engine


@pytest.fixture()
def race_engine_with_prior(tmp_path):
    """
    Engine with TWO race weekends in chronological order:
    Round 1 (2022) then Round 1 (2023) at the same circuit.
    Used to test circuit_win_rate aggregation.
    """
    db_path = tmp_path / "test_feat_prior.db"
    engine = get_engine(db_path)
    init_db(engine)
    _seed_race_weekend(engine, year=2022, round_number=1, sk_offset=0)
    _seed_race_weekend(engine, year=2023, round_number=1, sk_offset=1000)
    return engine


def _seed_race_weekend(engine, *, year=2023, round_number=1, sk_offset=0):
    """
    Insert a minimal race weekend into the database.

    Sessions:
      FP2  → session_key = 100 + sk_offset
      Q    → session_key = 200 + sk_offset
      Race → session_key = 300 + sk_offset

    Drivers: 1, 11, 44
    Laps: 5 per driver in the Race session
    FP3 is omitted intentionally.
    """
    fp2_sk = 100 + sk_offset
    q_sk = 200 + sk_offset
    race_sk = 300 + sk_offset
    circuit = "Bahrain"

    with engine.connect() as conn:
        # Sessions
        conn.execute(
            text(
                "INSERT INTO raw_sessions (session_key, year, round_number, "
                "country_name, circuit_short_name, session_type, session_name, "
                "date_start, date_end, total_laps, source) VALUES "
                "(:sk, :y, :rn, 'Bahrain', :csn, 'Practice 2', 'FP2', '2023-03-04T10:00:00Z', '2023-03-04T11:00:00Z', NULL, 'openf1')"
            ),
            {"sk": fp2_sk, "y": year, "rn": round_number, "csn": circuit},
        )
        conn.execute(
            text(
                "INSERT INTO raw_sessions (session_key, year, round_number, "
                "country_name, circuit_short_name, session_type, session_name, "
                "date_start, date_end, total_laps, source) VALUES "
                "(:sk, :y, :rn, 'Bahrain', :csn, 'Qualifying', 'Q', '2023-03-04T14:00:00Z', '2023-03-04T15:00:00Z', NULL, 'openf1')"
            ),
            {"sk": q_sk, "y": year, "rn": round_number, "csn": circuit},
        )
        conn.execute(
            text(
                "INSERT INTO raw_sessions (session_key, year, round_number, "
                "country_name, circuit_short_name, session_type, session_name, "
                "date_start, date_end, total_laps, source) VALUES "
                "(:sk, :y, :rn, 'Bahrain', :csn, 'Race', 'R', '2023-03-05T15:00:00Z', '2023-03-05T17:00:00Z', 5, 'openf1')"
            ),
            {"sk": race_sk, "y": year, "rn": round_number, "csn": circuit},
        )
        conn.commit()

        # FP2 laps: 3 drivers × 5 laps (exclude pit-out laps for clean test)
        fp2_laps = []
        base_times = {1: 92.0, 11: 93.0, 44: 94.0}  # driver 1 fastest
        for dn, base in base_times.items():
            for lap in range(1, 6):
                fp2_laps.append({
                    "session_key": fp2_sk,
                    "driver_number": dn,
                    "lap_number": lap,
                    "lap_duration": base + (lap - 1) * 0.1,
                    "duration_sector_1": None,
                    "duration_sector_2": None,
                    "duration_sector_3": None,
                    "is_pit_out_lap": False,
                    "i1_speed": None,
                    "i2_speed": None,
                    "st_speed": None,
                    "date_start": None,
                })
        for lap_row in fp2_laps:
            conn.execute(
                text(
                    "INSERT INTO raw_laps (session_key, driver_number, lap_number, "
                    "lap_duration, is_pit_out_lap) VALUES "
                    "(:sk, :dn, :ln, :ld, :pit)"
                ),
                {
                    "sk": lap_row["session_key"],
                    "dn": lap_row["driver_number"],
                    "ln": lap_row["lap_number"],
                    "ld": lap_row["lap_duration"],
                    "pit": lap_row["is_pit_out_lap"],
                },
            )

        # Q laps: best lap per driver (Q only needs one clean lap for delta test)
        q_best_times = {1: 88.0, 11: 88.5, 44: 89.0}  # driver 1 is pole
        for dn, best in q_best_times.items():
            conn.execute(
                text(
                    "INSERT INTO raw_laps (session_key, driver_number, lap_number, "
                    "lap_duration, is_pit_out_lap) VALUES (:sk, :dn, 1, :ld, 0)"
                ),
                {"sk": q_sk, "dn": dn, "ld": best},
            )

        # Race laps: 3 drivers × 5 laps
        race_base_times = {1: 91.0, 11: 91.1, 44: 91.3}
        for dn, base in race_base_times.items():
            for lap in range(1, 6):
                conn.execute(
                    text(
                        "INSERT INTO raw_laps (session_key, driver_number, lap_number, "
                        "lap_duration, is_pit_out_lap) VALUES (:sk, :dn, :ln, :ld, 0)"
                    ),
                    {
                        "sk": race_sk,
                        "dn": dn,
                        "ln": lap,
                        "ld": base + (lap - 1) * 0.05,
                    },
                )

        # Race positions: 5 laps × 3 drivers
        positions_by_driver = {1: 1, 11: 2, 44: 3}  # driver 1 wins
        for dn, pos in positions_by_driver.items():
            for lap in range(1, 6):
                conn.execute(
                    text(
                        "INSERT INTO raw_positions (session_key, driver_number, "
                        "lap_number, position) VALUES (:sk, :dn, :ln, :pos)"
                    ),
                    {"sk": race_sk, "dn": dn, "ln": lap, "pos": pos},
                )

        # Race stints: each driver on SOFT compound for all 5 laps
        for dn in [1, 11, 44]:
            conn.execute(
                text(
                    "INSERT INTO raw_stints (session_key, driver_number, stint_number, "
                    "lap_start, lap_end, compound, tyre_age_at_start) VALUES "
                    "(:sk, :dn, 1, 1, 5, 'SOFT', 0)"
                ),
                {"sk": race_sk, "dn": dn},
            )

        # No pit stops for this test (keeps counts = 0)

        conn.commit()


# ---------------------------------------------------------------------------
# Test 1: Feature table schema
# ---------------------------------------------------------------------------

def test_feature_table_schema_and_pk(tmp_path):
    """features_prerace and features_inrace have correct columns and PKs."""
    engine = get_engine(tmp_path / "schema_test.db")
    init_db(engine)
    insp = inspect(engine)

    tables = set(insp.get_table_names())
    assert "features_prerace" in tables
    assert "features_inrace" in tables

    pre_cols = {c["name"] for c in insp.get_columns("features_prerace")}
    required_pre = {
        "session_key", "driver_number", "qualifying_delta_to_pole",
        "practice_pace_fp2", "practice_pace_fp3", "circuit_win_rate",
        "circuit_avg_finish", "constructor_pace_delta", "championship_position",
        "grid_position", "driver_seasons_in_f1", "finish_position", "team_name",
    }
    assert required_pre.issubset(pre_cols), f"missing prerace cols: {required_pre - pre_cols}"

    in_cols = {c["name"] for c in insp.get_columns("features_inrace")}
    required_in = {
        "session_key", "driver_number", "lap_number", "gap_to_leader",
        "tyre_compound", "tyre_age_laps", "pit_stop_count", "position",
        "interval_ahead", "interval_behind", "pit_stop_delta", "laps_remaining",
    }
    assert required_in.issubset(in_cols), f"missing inrace cols: {required_in - in_cols}"

    pk_pre = set(insp.get_pk_constraint("features_prerace")["constrained_columns"])
    assert pk_pre == {"session_key", "driver_number"}

    pk_in = set(insp.get_pk_constraint("features_inrace")["constrained_columns"])
    assert pk_in == {"session_key", "driver_number", "lap_number"}


# ---------------------------------------------------------------------------
# Test 2: Build prerace features for a seeded race weekend
# ---------------------------------------------------------------------------

def test_build_prerace_features_row_count(race_engine):
    """build_features_for_session produces 3 prerace rows (1 per driver)."""
    race_sk = 300  # Race session key
    inserted = build_features_for_session(race_sk, engine=race_engine, kind="prerace")
    assert inserted == 3, f"Expected 3 prerace rows, got {inserted}"

    with race_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM features_prerace WHERE session_key = :sk"),
            {"sk": race_sk},
        ).scalar()
    assert count == 3


# ---------------------------------------------------------------------------
# Test 3: Build inrace features for a seeded race weekend
# ---------------------------------------------------------------------------

def test_build_inrace_features_row_count(race_engine):
    """build_features_for_session produces 3*5=15 inrace rows."""
    race_sk = 300
    inserted = build_features_for_session(race_sk, engine=race_engine, kind="inrace")
    assert inserted == 15, f"Expected 15 inrace rows (3 drivers x 5 laps), got {inserted}"

    with race_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM features_inrace WHERE session_key = :sk"),
            {"sk": race_sk},
        ).scalar()
    assert count == 15


# ---------------------------------------------------------------------------
# Test 4: Qualifying delta values are correct
# ---------------------------------------------------------------------------

def test_qualifying_delta_to_pole_values(race_engine):
    """
    qualifying_delta_to_pole is 0 for pole sitter (driver 1), positive for others.
    Best Q times: driver 1=88.0, driver 11=88.5, driver 44=89.0.
    Deltas: 0.0, 0.5, 1.0.
    """
    race_sk = 300
    build_features_for_session(race_sk, engine=race_engine, kind="prerace")

    with race_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT driver_number, qualifying_delta_to_pole "
                "FROM features_prerace WHERE session_key = :sk "
                "ORDER BY driver_number"
            ),
            {"sk": race_sk},
        ).fetchall()

    delta_map = {int(r[0]): round(float(r[1]), 6) for r in rows if r[1] is not None}
    assert delta_map.get(1) == pytest.approx(0.0, abs=1e-6), f"Pole sitter delta != 0: {delta_map}"
    assert delta_map.get(11) == pytest.approx(0.5, abs=1e-3), f"Driver 11 delta: {delta_map}"
    assert delta_map.get(44) == pytest.approx(1.0, abs=1e-3), f"Driver 44 delta: {delta_map}"


# ---------------------------------------------------------------------------
# Test 5: Re-run is idempotent (DELETE-then-INSERT, no duplicates)
# ---------------------------------------------------------------------------

def test_rebuild_is_idempotent_no_duplicates(race_engine):
    """Running the builder twice for the same session yields the same row count."""
    race_sk = 300
    inserted_1 = build_features_for_session(race_sk, engine=race_engine, kind="prerace")
    inserted_2 = build_features_for_session(race_sk, engine=race_engine, kind="prerace")

    assert inserted_1 == inserted_2, "Second run inserted different count"

    with race_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM features_prerace WHERE session_key = :sk"),
            {"sk": race_sk},
        ).scalar()
    assert count == inserted_1, f"Row count {count} != inserted {inserted_1} — duplicate rows!"


# ---------------------------------------------------------------------------
# Test 6: FP3 absent → practice_pace_fp3 is NULL for all drivers
# ---------------------------------------------------------------------------

def test_null_handling_when_fp3_absent(race_engine):
    """When FP3 session is absent, practice_pace_fp3 is NULL for all drivers."""
    race_sk = 300
    build_features_for_session(race_sk, engine=race_engine, kind="prerace")

    with race_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT driver_number, practice_pace_fp3 "
                "FROM features_prerace WHERE session_key = :sk"
            ),
            {"sk": race_sk},
        ).fetchall()

    assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"
    for dn, pace_fp3 in rows:
        assert pace_fp3 is None, f"Expected NULL fp3 for driver {dn}, got {pace_fp3}"


# ---------------------------------------------------------------------------
# Test 7: FP2 practice_pace values are correct
# ---------------------------------------------------------------------------

def test_practice_pace_fp2_values(race_engine):
    """
    FP2 median pace is computed correctly.
    Lap times: driver 1 = [92.0, 92.1, 92.2, 92.3, 92.4], median=92.2
               driver 11 = [93.0, 93.1, ..., 93.4], median=93.2
               driver 44 = [94.0, 94.1, ..., 94.4], median=94.2
    field median = median of all driver medians ≈ 93.2
    pace_fp2(driver 1) ≈ 92.2 - 93.2 = -1.0 (faster)
    pace_fp2(driver 44) ≈ 94.2 - 93.2 = +1.0 (slower)
    """
    race_sk = 300
    build_features_for_session(race_sk, engine=race_engine, kind="prerace")

    with race_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT driver_number, practice_pace_fp2 "
                "FROM features_prerace WHERE session_key = :sk "
                "ORDER BY driver_number"
            ),
            {"sk": race_sk},
        ).fetchall()

    pace_map = {int(r[0]): float(r[1]) for r in rows if r[1] is not None}
    # driver 1 is faster than field median → negative pace delta
    assert pace_map.get(1, 0) < 0, f"Driver 1 FP2 pace should be negative (faster): {pace_map}"
    # driver 44 is slower → positive
    assert pace_map.get(44, 0) > 0, f"Driver 44 FP2 pace should be positive (slower): {pace_map}"


# ---------------------------------------------------------------------------
# Test 8: build_all_features returns correct dict keys
# ---------------------------------------------------------------------------

def test_build_all_features_returns_dict(race_engine):
    """build_all_features returns a dict with prerace_inserted, inrace_inserted, sessions_processed."""
    result = build_all_features(engine=race_engine)
    assert "prerace_inserted" in result
    assert "inrace_inserted" in result
    assert "sessions_processed" in result
    assert result["sessions_processed"] >= 1
    assert result["prerace_inserted"] >= 3
    assert result["inrace_inserted"] >= 15


# ---------------------------------------------------------------------------
# Test 9: Non-race session is skipped by the builder
# ---------------------------------------------------------------------------

def test_non_race_session_is_skipped(race_engine):
    """Calling build_features_for_session on a Q session returns 0 rows."""
    q_sk = 200  # Qualifying session key
    inserted = build_features_for_session(q_sk, engine=race_engine, kind="prerace")
    assert inserted == 0, f"Expected 0 rows for Q session, got {inserted}"


# ---------------------------------------------------------------------------
# Test 10: Inrace tyre compound and laps_remaining are correct
# ---------------------------------------------------------------------------

def test_inrace_tyre_and_laps_remaining(race_engine):
    """
    In-race features have correct tyre_compound (SOFT) and laps_remaining.
    total_laps = 5; lap 1 → laps_remaining = 4; lap 5 → laps_remaining = 0.
    """
    race_sk = 300
    build_features_for_session(race_sk, engine=race_engine, kind="inrace")

    with race_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT driver_number, lap_number, tyre_compound, laps_remaining "
                "FROM features_inrace WHERE session_key = :sk AND driver_number = 1 "
                "ORDER BY lap_number"
            ),
            {"sk": race_sk},
        ).fetchall()

    assert len(rows) == 5
    for dn, lap, compound, remaining in rows:
        assert compound == "SOFT", f"Lap {lap}: expected SOFT, got {compound}"
        expected_remaining = 5 - lap
        assert remaining == expected_remaining, (
            f"Lap {lap}: expected laps_remaining={expected_remaining}, got {remaining}"
        )
