"""
End-to-end tests for the in-race model training pipeline (Plan 02-02).

Covers:
  - test_train_inrace_end_to_end: seeds a tmp_path SQLite database with two race
    sessions (2022 and 2025), runs train_inrace_model, asserts the artifact exists
    with correct dict keys (D-11 contract).
  - test_train_inrace_softmax_sums_to_one_per_lap: asserts per-(session_key, lap_number)
    softmax win probabilities sum to 1.0 ± 1e-3 (INRACE-02).
  - test_train_inrace_includes_pit_stop_features: asserts INRACE_FEATURE_COLS contains
    pit_stop_count AND pit_stop_delta (INRACE-03).
  - test_train_inrace_empty_features_raises: asserts RuntimeError when features_inrace
    is empty (RESEARCH Pitfall 5).
  - test_train_inrace_label_join_to_prerace: asserts driver missing finish_position in
    features_prerace is excluded from training via INNER JOIN (RESEARCH Open Question Q1).

All tests use tmp_path-scoped get_engine + init_db — no real data/f1.db touched.
Seeding pattern mirrors tests/test_train_prerace.py (Plan 02-01 convention).
"""
from __future__ import annotations

import joblib
import numpy as np
import pytest
from scipy.special import softmax as scipy_softmax
from sqlalchemy import text

from f1_prediction.db.engine import get_engine, init_db
from f1_prediction.ml.pipeline import train_inrace_model, INRACE_MODEL_PATH
from f1_prediction.ml.loader import INRACE_FEATURE_COLS, INRACE_CAT_COLS, load_inrace_df


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------

def _seed_raw_session(
    conn,
    *,
    session_key: int,
    year: int,
    round_number: int,
    total_laps: int = 5,
) -> None:
    """Insert a single Race session into raw_sessions."""
    conn.execute(
        text(
            "INSERT INTO raw_sessions ("
            "  session_key, year, round_number, country_name, circuit_short_name,"
            "  session_type, session_name, date_start, date_end, total_laps, source"
            ") VALUES ("
            "  :sk, :y, :rn, 'Bahrain', 'Bahrain', 'Race', 'R',"
            "  '2022-03-20T15:00:00Z', '2022-03-20T17:00:00Z', :tl, 'fastf1'"
            ")"
        ),
        {"sk": session_key, "y": year, "rn": round_number, "tl": total_laps},
    )


def _seed_features_prerace_row(
    conn,
    *,
    session_key: int,
    driver_number: int,
    finish_position: int | None = None,
) -> None:
    """Insert a single features_prerace row with a given finish_position."""
    conn.execute(
        text(
            "INSERT INTO features_prerace ("
            "  session_key, driver_number, qualifying_delta_to_pole,"
            "  practice_pace_fp2, practice_pace_fp3, circuit_win_rate,"
            "  circuit_avg_finish, constructor_pace_delta,"
            "  championship_position, grid_position, driver_seasons_in_f1,"
            "  finish_position, team_name"
            ") VALUES ("
            "  :sk, :dn, :qdelta, :fp2, :fp3, :cwr, :caf, :cpd,"
            "  :champ_pos, :grid_pos, :seasons, :finish_pos, :team"
            ")"
        ),
        {
            "sk": session_key,
            "dn": driver_number,
            "qdelta": 0.0 if driver_number == 1 else 0.3,
            "fp2": -0.2 if driver_number == 1 else 0.1,
            "fp3": None,  # intentional NULL for D-08 NaN handling
            "cwr": 0.4 if driver_number == 1 else 0.1,
            "caf": 2.0 if driver_number == 1 else 5.0,
            "cpd": -0.3 if driver_number == 1 else 0.1,
            "champ_pos": driver_number,
            "grid_pos": driver_number,
            "seasons": 5,
            "finish_pos": finish_position,
            "team": "Red Bull" if driver_number == 1 else "Ferrari",
        },
    )


def _seed_features_inrace_rows(
    conn,
    *,
    session_key: int,
    driver_numbers: list[int],
    lap_count: int = 5,
) -> None:
    """
    Insert features_inrace rows for a set of drivers across lap_count laps.

    Includes realistic values: tyre_compound cycles through SOFT/MEDIUM/HARD,
    with one NULL entry to exercise the OrdinalEncoder unknown branch.
    pit_stop_count is 0 then 1 after lap 3. pit_stop_delta is NULL except on pit lap.
    laps_remaining = lap_count - lap_number (total_laps == lap_count in seeded session).
    """
    compounds = ["SOFT", "MEDIUM", "HARD", None]  # includes NULL for encoder unknown branch

    for driver_number in driver_numbers:
        for lap_number in range(1, lap_count + 1):
            # pit stop happens after lap 3 → pit_stop_count jumps to 1 on lap 4
            pit_stop_count = 1 if lap_number >= 4 else 0
            # pit_stop_delta only non-NULL on the pit lap itself (lap 4)
            pit_stop_delta = 2.5 if lap_number == 4 else None

            # Assign compound cycling through the list to exercise all categories
            compound_idx = (driver_number + lap_number) % len(compounds)
            tyre_compound = compounds[compound_idx]

            conn.execute(
                text(
                    "INSERT INTO features_inrace ("
                    "  session_key, driver_number, lap_number,"
                    "  gap_to_leader, tyre_compound, tyre_age_laps,"
                    "  pit_stop_count, position, interval_ahead, interval_behind,"
                    "  pit_stop_delta, laps_remaining"
                    ") VALUES ("
                    "  :sk, :dn, :ln,"
                    "  :gtl, :tc, :tal,"
                    "  :psc, :pos, :ia, :ib,"
                    "  :psd, :lr"
                    ")"
                ),
                {
                    "sk": session_key,
                    "dn": driver_number,
                    "ln": lap_number,
                    "gtl": (driver_number - 1) * 0.5 + lap_number * 0.1,  # increases per driver and lap
                    "tc": tyre_compound,
                    "tal": lap_number if pit_stop_count == 0 else lap_number - 3,
                    "psc": pit_stop_count,
                    "pos": driver_number,
                    "ia": None if driver_number == 1 else 0.5,  # leader has no car ahead
                    "ib": None if driver_number == len(driver_numbers) else 0.5,
                    "psd": pit_stop_delta,
                    "lr": lap_count - lap_number,
                },
            )


def _build_seeded_engine(tmp_path, *, include_driver_44_prerace: bool = True):
    """
    Build and return a seeded engine with in-race training and test data.

    Sessions seeded:
      - 2022 Round 1 Race (training set, year <= 2024, session_key=202201)
      - 2025 Round 1 Race (test set, year == 2025, session_key=202501)
      - 2025 Round 2 Race (test set, year == 2025, session_key=202502)

    Each session has 3 drivers (1, 11, 44) with 5 laps.

    features_prerace rows provide finish_position labels joined by train_inrace_model.

    Args:
        tmp_path: pytest tmp_path fixture directory.
        include_driver_44_prerace: if False, driver 44 is excluded from
            features_prerace (used by test_train_inrace_label_join_to_prerace).
    """
    db_path = tmp_path / "f1_test.db"
    engine = get_engine(db_path)
    init_db(engine)

    driver_numbers = [1, 11, 44]
    lap_count = 5

    with engine.connect() as conn:
        # --- Raw sessions ---
        # Training sessions (2022)
        _seed_raw_session(conn, session_key=202201, year=2022, round_number=1, total_laps=lap_count)
        # Test sessions (2025)
        _seed_raw_session(conn, session_key=202501, year=2025, round_number=1, total_laps=lap_count)
        _seed_raw_session(conn, session_key=202502, year=2025, round_number=2, total_laps=lap_count)

        # --- features_prerace (label source via INNER JOIN) ---
        # 2022 round 1: driver 1 wins
        for dn in driver_numbers:
            if not include_driver_44_prerace and dn == 44:
                # Skip driver 44 prerace row — used for label join exclusion test
                continue
            _seed_features_prerace_row(
                conn,
                session_key=202201,
                driver_number=dn,
                finish_position=dn,  # driver 1 = pos 1 (winner), 11 = pos 2, 44 = pos 3
            )

        # 2025 round 1: driver 1 wins
        for dn in driver_numbers:
            if not include_driver_44_prerace and dn == 44:
                continue
            _seed_features_prerace_row(
                conn,
                session_key=202501,
                driver_number=dn,
                finish_position=dn,
            )

        # 2025 round 2: driver 11 wins (ensures 2 winner rows in test for cv clamping)
        finish_positions_r2 = {1: 2, 11: 1, 44: 3}
        for dn in driver_numbers:
            if not include_driver_44_prerace and dn == 44:
                continue
            _seed_features_prerace_row(
                conn,
                session_key=202502,
                driver_number=dn,
                finish_position=finish_positions_r2[dn],
            )

        # --- features_inrace ---
        # 2022 round 1 (training)
        _seed_features_inrace_rows(conn, session_key=202201, driver_numbers=driver_numbers, lap_count=lap_count)
        # 2025 round 1 (test)
        _seed_features_inrace_rows(conn, session_key=202501, driver_numbers=driver_numbers, lap_count=lap_count)
        # 2025 round 2 (test)
        _seed_features_inrace_rows(conn, session_key=202502, driver_numbers=driver_numbers, lap_count=lap_count)

        conn.commit()

    return engine


# ---------------------------------------------------------------------------
# Test 1: End-to-end artifact creation (D-11 contract)
# ---------------------------------------------------------------------------

def test_train_inrace_end_to_end(tmp_path, monkeypatch):
    """
    Seeds a tmp_path SQLite database with two race sessions (year=2022 round=1 and
    year=2025 round=1), each with 3 drivers and 5 laps per driver. Monkeypatches
    INRACE_MODEL_PATH to tmp_path/models/inrace_model.joblib. Calls train_inrace_model,
    asserts the artifact file exists, loads it back with joblib, asserts the dict has
    exactly the keys {model, feature_cols, cat_cols, encoder, trained_on, tested_on},
    trained_on == "2022-2024", tested_on == "2025", and cat_cols == ["tyre_compound"].
    """
    engine = _build_seeded_engine(tmp_path)

    artifact_path = tmp_path / "models" / "inrace_model.joblib"
    monkeypatch.setattr("f1_prediction.ml.pipeline.INRACE_MODEL_PATH", artifact_path)

    result = train_inrace_model(engine, model_path=artifact_path)

    # Artifact file must exist on disk
    assert artifact_path.exists(), f"Artifact not written to {artifact_path}"

    # Load and check dict structure (D-11 contract)
    loaded = joblib.load(artifact_path)
    expected_keys = {"model", "feature_cols", "cat_cols", "encoder", "trained_on", "tested_on"}
    assert set(loaded.keys()) == expected_keys, (
        f"Artifact keys mismatch. Got: {set(loaded.keys())}"
    )
    assert loaded["trained_on"] == "2022-2024", f"trained_on mismatch: {loaded['trained_on']}"
    assert loaded["tested_on"] == "2025", f"tested_on mismatch: {loaded['tested_on']}"
    assert loaded["cat_cols"] == ["tyre_compound"], f"cat_cols mismatch: {loaded['cat_cols']}"

    # Return value from function must match the artifact dict
    assert set(result.keys()) == expected_keys
    assert result["trained_on"] == "2022-2024"
    assert result["tested_on"] == "2025"
    assert result["cat_cols"] == ["tyre_compound"]


# ---------------------------------------------------------------------------
# Test 2: Per-(session_key, lap_number) softmax sums to 1.0 (INRACE-02)
# ---------------------------------------------------------------------------

def test_train_inrace_softmax_sums_to_one_per_lap(tmp_path, monkeypatch):
    """
    Trains on seeded data, loads the artifact, calls model.predict_proba on the
    2025 test rows, and asserts that softmax applied per (session_key, lap_number)
    group sums to within 1e-3 of 1.0 for every group (INRACE-02 — distribution
    updated per lap, not per session as in pre-race).
    """
    import pandas as pd

    engine = _build_seeded_engine(tmp_path)
    artifact_path = tmp_path / "models" / "inrace_model.joblib"
    monkeypatch.setattr("f1_prediction.ml.pipeline.INRACE_MODEL_PATH", artifact_path)

    artifact = train_inrace_model(engine, model_path=artifact_path)

    # Reload the full DataFrame including year
    with engine.connect() as conn:
        df = load_inrace_df(conn)

    test_df = df[df["year"] == 2025].copy()
    assert len(test_df) > 0, "No 2025 test rows found"

    # Encode categoricals using the saved encoder
    encoder = artifact["encoder"]
    X_test = test_df[INRACE_FEATURE_COLS].copy()
    X_test[INRACE_CAT_COLS] = encoder.transform(X_test[INRACE_CAT_COLS])

    # Predict win-class probabilities
    model = artifact["model"]
    proba = model.predict_proba(X_test.values)  # shape: (n, 2)
    test_df = test_df.copy()
    test_df["win_score"] = proba[:, 1]

    # Apply per-(session_key, lap_number) softmax — INRACE-02 requires per-lap grouping
    for (sk, ln), group in test_df.groupby(["session_key", "lap_number"]):
        win_probs = scipy_softmax(group["win_score"].values)
        total = float(win_probs.sum())
        assert abs(total - 1.0) < 1e-3, (
            f"Session {sk} Lap {ln}: softmax win probs sum to {total:.6f}, expected 1.0 ± 1e-3"
        )


# ---------------------------------------------------------------------------
# Test 3: INRACE_FEATURE_COLS contains pit-stop features (INRACE-03)
# ---------------------------------------------------------------------------

def test_train_inrace_includes_pit_stop_features():
    """
    Asserts INRACE_FEATURE_COLS contains both pit_stop_count AND pit_stop_delta.
    Pit-stop signal must be wired into the training matrix (INRACE-03).
    """
    assert "pit_stop_count" in INRACE_FEATURE_COLS, (
        f"pit_stop_count not in INRACE_FEATURE_COLS: {INRACE_FEATURE_COLS}"
    )
    assert "pit_stop_delta" in INRACE_FEATURE_COLS, (
        f"pit_stop_delta not in INRACE_FEATURE_COLS: {INRACE_FEATURE_COLS}"
    )


# ---------------------------------------------------------------------------
# Test 4: Empty features_inrace raises RuntimeError (RESEARCH Pitfall 5)
# ---------------------------------------------------------------------------

def test_train_inrace_empty_features_raises(tmp_path, monkeypatch):
    """
    Seeds raw_sessions but leaves features_inrace empty. Asserts train_inrace_model
    raises RuntimeError mentioning 'features_inrace is empty — run scripts/backfill.py first'.
    """
    db_path = tmp_path / "f1_empty.db"
    engine = get_engine(db_path)
    init_db(engine)

    # Seed raw_sessions only — no features_inrace rows (and no features_prerace rows)
    with engine.connect() as conn:
        _seed_raw_session(conn, session_key=202201, year=2022, round_number=1, total_laps=5)
        _seed_raw_session(conn, session_key=202501, year=2025, round_number=1, total_laps=5)
        conn.commit()

    artifact_path = tmp_path / "models" / "inrace_model_empty.joblib"
    monkeypatch.setattr("f1_prediction.ml.pipeline.INRACE_MODEL_PATH", artifact_path)

    with pytest.raises(RuntimeError, match="features_inrace is empty"):
        train_inrace_model(engine, model_path=artifact_path)


# ---------------------------------------------------------------------------
# Test 5: Label join excludes driver missing finish_position (RESEARCH Open Q1)
# ---------------------------------------------------------------------------

def test_train_inrace_label_join_to_prerace(tmp_path, monkeypatch):
    """
    Seeds features_inrace with 3 drivers but only seeds features_prerace.finish_position
    for 2 of them (driver 44 has no finish_position in features_prerace). After training,
    asserts the driver with the missing finish_position label was excluded from training
    (label join is INNER, not OUTER). This validates RESEARCH Open Questions Q1 resolution.
    """
    # include_driver_44_prerace=False leaves driver 44 out of features_prerace
    engine = _build_seeded_engine(tmp_path, include_driver_44_prerace=False)

    artifact_path = tmp_path / "models" / "inrace_model_label_join.joblib"
    monkeypatch.setattr("f1_prediction.ml.pipeline.INRACE_MODEL_PATH", artifact_path)

    # Training must succeed (2 drivers have labels)
    result = train_inrace_model(engine, model_path=artifact_path)
    assert artifact_path.exists(), "Artifact must be written even with one driver excluded"

    # The loaded DataFrame should only contain drivers with prerace labels
    with engine.connect() as conn:
        df = load_inrace_df(conn)

    # Driver 44 has features_inrace rows but NO features_prerace row →
    # INNER JOIN must exclude driver 44 entirely
    driver_numbers_in_df = set(df["driver_number"].unique())
    assert 44 not in driver_numbers_in_df, (
        f"Driver 44 should be excluded by INNER JOIN (no finish_position label), "
        f"but was found in load_inrace_df result. Drivers present: {driver_numbers_in_df}"
    )
    # Drivers 1 and 11 must be present
    assert 1 in driver_numbers_in_df, "Driver 1 must be present (has prerace label)"
    assert 11 in driver_numbers_in_df, "Driver 11 must be present (has prerace label)"
