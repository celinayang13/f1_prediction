"""
End-to-end tests for the pre-race model training pipeline (Plan 02-01).

Covers:
  - test_train_prerace_end_to_end: seeds a tmp_path SQLite database, runs
    train_prerace_model, asserts the artifact exists with correct dict keys.
  - test_train_prerace_softmax_sums_to_one: asserts per-session softmax
    win probabilities sum to 1.0 ± 1e-3 (PRERACE-02).
  - test_train_prerace_empty_features_raises: asserts RuntimeError when
    features_prerace is empty (RESEARCH Pitfall 5).

All tests use tmp_path-scoped get_engine + init_db — no real data/f1.db touched.
Seeding pattern follows tests/test_features.py (Phase 1 convention).
"""
from __future__ import annotations

import joblib
import numpy as np
import pytest
from sqlalchemy import text

from f1_prediction.db.engine import get_engine, init_db
from f1_prediction.ml.pipeline import PRERACE_MODEL_PATH, train_prerace_model
from f1_prediction.ml.evaluate import per_session_softmax


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------

def _seed_raw_sessions(conn, *, year: int, round_number: int, race_sk: int, q_sk: int) -> None:
    """Insert a Race session and its Qualifying sibling into raw_sessions."""
    conn.execute(
        text(
            "INSERT INTO raw_sessions (session_key, year, round_number, "
            "country_name, circuit_short_name, session_type, session_name, "
            "date_start, date_end, total_laps, source) VALUES "
            "(:sk, :y, :rn, 'Bahrain', 'Bahrain', 'Race', 'R', "
            "'2022-03-20T15:00:00Z', '2022-03-20T17:00:00Z', 57, 'fastf1')"
        ),
        {"sk": race_sk, "y": year, "rn": round_number},
    )
    conn.execute(
        text(
            "INSERT INTO raw_sessions (session_key, year, round_number, "
            "country_name, circuit_short_name, session_type, session_name, "
            "date_start, date_end, total_laps, source) VALUES "
            "(:sk, :y, :rn, 'Bahrain', 'Bahrain', 'Qualifying', 'Q', "
            "'2022-03-19T14:00:00Z', '2022-03-19T15:00:00Z', NULL, 'fastf1')"
        ),
        {"sk": q_sk, "y": year, "rn": round_number},
    )


def _seed_features_prerace(conn, *, session_key: int, driver_rows: list[dict]) -> None:
    """
    Insert features_prerace rows for a given session.

    Each dict in driver_rows must contain at least: driver_number, finish_position.
    Optional columns default to None / reasonable values.
    """
    for row in driver_rows:
        conn.execute(
            text(
                "INSERT INTO features_prerace ("
                "  session_key, driver_number, qualifying_delta_to_pole, "
                "  practice_pace_fp2, practice_pace_fp3, circuit_win_rate, "
                "  circuit_avg_finish, constructor_pace_delta, "
                "  championship_position, grid_position, driver_seasons_in_f1, "
                "  finish_position, team_name"
                ") VALUES ("
                "  :sk, :dn, :qdelta, :fp2, :fp3, :cwr, :caf, :cpd, "
                "  :champ_pos, :grid_pos, :seasons, :finish_pos, :team"
                ")"
            ),
            {
                "sk": session_key,
                "dn": row["driver_number"],
                "qdelta": row.get("qualifying_delta_to_pole", 0.0),
                "fp2": row.get("practice_pace_fp2", -0.1),
                "fp3": row.get("practice_pace_fp3", None),   # intentional NULL for D-08 test
                "cwr": row.get("circuit_win_rate", 0.1),
                "caf": row.get("circuit_avg_finish", 5.0),
                "cpd": row.get("constructor_pace_delta", -0.05),
                "champ_pos": row.get("championship_position", 1),
                "grid_pos": row.get("grid_position", row["driver_number"]),
                "seasons": row.get("driver_seasons_in_f1", 3),
                "finish_pos": row["finish_position"],
                "team": row.get("team_name", None),  # intentional NULL for encoder unknown branch
            },
        )


def _build_seeded_engine(tmp_path):
    """
    Build and return a seeded engine with two race sessions:
      - 2022 Round 1 (training set, year <= 2024)
      - 2025 Round 1 (test set, year == 2025)

    Each session has 3 drivers with distinct finish positions.
    One row has practice_pace_fp3=NULL (D-08 NaN handling).
    One row has team_name=NULL (encoder unknown branch).
    """
    db_path = tmp_path / "f1_test.db"
    engine = get_engine(db_path)
    init_db(engine)

    with engine.connect() as conn:
        # 2022 Race (session_key=202201, Q=202200)
        _seed_raw_sessions(conn, year=2022, round_number=1, race_sk=202201, q_sk=202200)
        # 2025 Race (session_key=202501, Q=202500)
        _seed_raw_sessions(conn, year=2025, round_number=1, race_sk=202501, q_sk=202500)

        # 2022 features — training rows (3 drivers)
        _seed_features_prerace(conn, session_key=202201, driver_rows=[
            {
                "driver_number": 1,
                "finish_position": 1,
                "qualifying_delta_to_pole": 0.0,
                "practice_pace_fp2": -0.5,
                "practice_pace_fp3": None,    # NULL — test D-08 NaN handling
                "circuit_win_rate": 0.4,
                "circuit_avg_finish": 2.0,
                "constructor_pace_delta": -0.3,
                "championship_position": 1,
                "grid_position": 1,
                "driver_seasons_in_f1": 5,
                "team_name": "Red Bull",
            },
            {
                "driver_number": 11,
                "finish_position": 2,
                "qualifying_delta_to_pole": 0.2,
                "practice_pace_fp2": 0.1,
                "practice_pace_fp3": 0.0,
                "circuit_win_rate": 0.1,
                "circuit_avg_finish": 4.0,
                "constructor_pace_delta": 0.1,
                "championship_position": 2,
                "grid_position": 2,
                "driver_seasons_in_f1": 4,
                "team_name": "Ferrari",
            },
            {
                "driver_number": 44,
                "finish_position": 3,
                "qualifying_delta_to_pole": 0.5,
                "practice_pace_fp2": 0.3,
                "practice_pace_fp3": 0.2,
                "circuit_win_rate": 0.2,
                "circuit_avg_finish": 3.0,
                "constructor_pace_delta": 0.05,
                "championship_position": 3,
                "grid_position": 3,
                "driver_seasons_in_f1": 8,
                "team_name": None,   # NULL — test encoder unknown branch
            },
        ])

        # 2025 features — test rows (3 drivers)
        _seed_features_prerace(conn, session_key=202501, driver_rows=[
            {
                "driver_number": 1,
                "finish_position": 1,
                "qualifying_delta_to_pole": 0.0,
                "practice_pace_fp2": -0.4,
                "practice_pace_fp3": None,    # NULL — test D-08 NaN handling
                "circuit_win_rate": 0.45,
                "circuit_avg_finish": 1.8,
                "constructor_pace_delta": -0.35,
                "championship_position": 1,
                "grid_position": 1,
                "driver_seasons_in_f1": 8,
                "team_name": "Red Bull",
            },
            {
                "driver_number": 11,
                "finish_position": 2,
                "qualifying_delta_to_pole": 0.3,
                "practice_pace_fp2": 0.15,
                "practice_pace_fp3": 0.05,
                "circuit_win_rate": 0.12,
                "circuit_avg_finish": 3.5,
                "constructor_pace_delta": 0.12,
                "championship_position": 2,
                "grid_position": 2,
                "driver_seasons_in_f1": 7,
                "team_name": "Ferrari",
            },
            {
                "driver_number": 44,
                "finish_position": 3,
                "qualifying_delta_to_pole": 0.6,
                "practice_pace_fp2": 0.25,
                "practice_pace_fp3": 0.15,
                "circuit_win_rate": 0.22,
                "circuit_avg_finish": 2.8,
                "constructor_pace_delta": 0.08,
                "championship_position": 3,
                "grid_position": 3,
                "driver_seasons_in_f1": 11,
                "team_name": None,   # NULL — test encoder unknown branch
            },
        ])

        conn.commit()

    return engine


# ---------------------------------------------------------------------------
# Test 1: End-to-end artifact creation
# ---------------------------------------------------------------------------

def test_train_prerace_end_to_end(tmp_path, monkeypatch):
    """
    Seeds a tmp_path SQLite database with two race sessions (2022 and 2025),
    monkeypatches the artifact path, calls train_prerace_model, asserts the
    artifact file exists and contains the correct dict structure.
    """
    engine = _build_seeded_engine(tmp_path)

    # Monkeypatch artifact path so it writes under tmp_path, not real data/models/
    artifact_path = tmp_path / "models" / "prerace_model.joblib"
    monkeypatch.setattr("f1_prediction.ml.pipeline.PRERACE_MODEL_PATH", artifact_path)

    result = train_prerace_model(engine, model_path=artifact_path)

    # Artifact file must exist on disk
    assert artifact_path.exists(), f"Artifact not written to {artifact_path}"

    # Load and check dict structure
    loaded = joblib.load(artifact_path)
    expected_keys = {"model", "feature_cols", "cat_cols", "encoder", "trained_on", "tested_on"}
    assert set(loaded.keys()) == expected_keys, (
        f"Artifact keys mismatch. Got: {set(loaded.keys())}"
    )
    assert loaded["trained_on"] == "2022-2024", f"trained_on mismatch: {loaded['trained_on']}"
    assert loaded["tested_on"] == "2025", f"tested_on mismatch: {loaded['tested_on']}"

    # Return value from function must match the artifact dict
    assert result["trained_on"] == "2022-2024"
    assert result["tested_on"] == "2025"
    assert set(result.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Test 2: Per-session softmax sums to 1.0 (PRERACE-02)
# ---------------------------------------------------------------------------

def test_train_prerace_softmax_sums_to_one(tmp_path, monkeypatch):
    """
    Trains on seeded data, calls predict_proba on 2025 test rows, applies
    per_session_softmax grouped by session_key, asserts each session sum
    is within 1e-3 of 1.0 (PRERACE-02).
    """
    import pandas as pd

    engine = _build_seeded_engine(tmp_path)
    artifact_path = tmp_path / "models" / "prerace_model.joblib"
    monkeypatch.setattr("f1_prediction.ml.pipeline.PRERACE_MODEL_PATH", artifact_path)

    artifact = train_prerace_model(engine, model_path=artifact_path)

    from f1_prediction.ml.loader import load_prerace_df, PRERACE_FEATURE_COLS, PRERACE_CAT_COLS
    import numpy as np

    # Reload the test rows for 2025
    with engine.connect() as conn:
        df = load_prerace_df(conn)
    test_df = df[df["year"] == 2025].copy()
    assert len(test_df) > 0, "No 2025 test rows found"

    # Encode categoricals using the saved encoder
    encoder = artifact["encoder"]
    X_test = test_df[PRERACE_FEATURE_COLS].copy()
    X_test[PRERACE_CAT_COLS] = encoder.transform(X_test[PRERACE_CAT_COLS])

    # Predict win-class probabilities
    model = artifact["model"]
    proba = model.predict_proba(X_test.values)  # shape: (n, 2)
    test_df = test_df.copy()
    test_df["win_score"] = proba[:, 1]

    # Apply per-session softmax and verify sum to 1.0
    test_df["win_prob"] = per_session_softmax(test_df, score_col="win_score", session_col="session_key")
    for sk, group in test_df.groupby("session_key"):
        total = group["win_prob"].sum()
        assert abs(total - 1.0) < 1e-3, (
            f"Session {sk}: win probabilities sum to {total:.6f}, expected 1.0 ± 1e-3"
        )


# ---------------------------------------------------------------------------
# Test 3: Empty features_prerace raises RuntimeError (RESEARCH Pitfall 5)
# ---------------------------------------------------------------------------

def test_train_prerace_empty_features_raises(tmp_path, monkeypatch):
    """
    Seeds raw_sessions but leaves features_prerace empty.
    Asserts train_prerace_model raises RuntimeError mentioning
    'features_prerace is empty — run scripts/backfill.py first'.
    """
    db_path = tmp_path / "f1_empty.db"
    engine = get_engine(db_path)
    init_db(engine)

    # Seed raw_sessions only — no features_prerace rows
    with engine.connect() as conn:
        _seed_raw_sessions(conn, year=2022, round_number=1, race_sk=202201, q_sk=202200)
        _seed_raw_sessions(conn, year=2025, round_number=1, race_sk=202501, q_sk=202500)
        conn.commit()

    artifact_path = tmp_path / "models" / "prerace_model_empty.joblib"
    monkeypatch.setattr("f1_prediction.ml.pipeline.PRERACE_MODEL_PATH", artifact_path)

    with pytest.raises(RuntimeError, match="features_prerace is empty"):
        train_prerace_model(engine, model_path=artifact_path)
