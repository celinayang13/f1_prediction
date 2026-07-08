"""
Tests for the pre-race prediction pipeline (Plan 03-02, DASH-02).

Covers:
  - compute_and_store_prerace inserts predictions with prediction_type='prerace',
    lap_number IS NULL, one row per driver, sum(win_probability) ~= 1.0 (D-09,
    PRERACE-02 contract reused).
  - pipeline.prerace_already_computed detects existing 'prerace' rows so a
    second pipeline startup does not recompute/duplicate (D-11 idempotent).

Uses the tmp_engine fixture (isolated SQLite, no data/f1.db writes). A tiny
prerace model is trained via the existing train_prerace_model/save_model
helpers (scripts/train.py) so the test exercises real inference, not a mock.

Security: all SQL uses text() with bound :param names — no f-string SQL (T-03-05).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import text

# Make scripts/train.py importable without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from train import (  # noqa: E402
    load_prerace_training,
    save_model,
    train_prerace_model,
)

SESSION_KEY = 9999
DRIVERS = [1, 11, 44, 63]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def prerace_pipeline_engine(tmp_engine):
    """
    tmp_engine seeded with:
      - 3 historical Race sessions (2022 x2, 2023) with features_prerace +
        finish_position, so a tiny model can be trained.
      - 1 target "latest race" session (2024) with features_prerace rows but
        no finish_position (race hasn't happened yet) — this is what
        compute_and_store_prerace predicts against.
    """
    _seed_training_sessions(tmp_engine)
    _seed_target_session(tmp_engine)
    return tmp_engine


def _seed_training_sessions(engine) -> None:
    """Seed 3 historical Race sessions so train_prerace_model has data to fit."""
    training_sessions = [
        # (session_key, year, round_number, total_laps)
        (101, 2022, 1, 53),
        (102, 2022, 2, 55),
        (103, 2023, 1, 58),
    ]
    winner_by_session = {101: 1, 102: 11, 103: 44}

    with engine.begin() as conn:
        for sk, year, rn, total_laps in training_sessions:
            conn.execute(
                text(
                    "INSERT INTO raw_sessions "
                    "(session_key, year, round_number, session_type, session_name, "
                    "date_start, date_end, total_laps, source) "
                    "VALUES (:sk, :year, :rn, 'Race', 'R', '2022-01-01', '2022-01-01', :tl, 'fastf1')"
                ),
                {"sk": sk, "year": year, "rn": rn, "tl": total_laps},
            )
            winner = winner_by_session[sk]
            for i, dn in enumerate(DRIVERS):
                fp = 1 if dn == winner else i + 2
                conn.execute(
                    text(
                        "INSERT INTO features_prerace "
                        "(session_key, driver_number, qualifying_delta_to_pole, "
                        "practice_pace_fp2, practice_pace_fp3, circuit_win_rate, "
                        "championship_position, grid_position, driver_seasons_in_f1, "
                        "finish_position) "
                        "VALUES (:sk, :dn, :qdp, :fp2, :fp3, :cwr, :champ, :grid, :seasons, :fpos)"
                    ),
                    {
                        "sk": sk,
                        "dn": dn,
                        "qdp": 0.0 if dn == winner else 0.3 * (i + 1),
                        "fp2": 0.1 * i,
                        "fp3": 0.1 * i,
                        "cwr": 0.3 if dn == winner else 0.05,
                        "champ": i + 1,
                        "grid": i + 1,
                        "seasons": 3,
                        "fpos": fp,
                    },
                )
            for dn in DRIVERS:
                for lap in range(1, total_laps):
                    conn.execute(
                        text(
                            "INSERT INTO raw_positions "
                            "(session_key, driver_number, lap_number, position) "
                            "VALUES (:sk, :dn, :ln, :pos)"
                        ),
                        {"sk": sk, "dn": dn, "ln": lap, "pos": 1 if dn == winner else 2},
                    )


def _seed_target_session(engine) -> None:
    """Seed the 'latest race' session — features exist, finish_position does not."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO raw_sessions "
                "(session_key, year, round_number, session_type, session_name, "
                "date_start, date_end, total_laps, source) "
                "VALUES (:sk, 2024, 1, 'Race', 'R', '2024-01-01', '2024-01-01', 58, 'fastf1')"
            ),
            {"sk": SESSION_KEY},
        )
        for i, dn in enumerate(DRIVERS):
            conn.execute(
                text(
                    "INSERT INTO features_prerace "
                    "(session_key, driver_number, qualifying_delta_to_pole, "
                    "practice_pace_fp2, practice_pace_fp3, circuit_win_rate, "
                    "championship_position, grid_position, driver_seasons_in_f1) "
                    "VALUES (:sk, :dn, :qdp, :fp2, :fp3, :cwr, :champ, :grid, :seasons)"
                ),
                {
                    "sk": SESSION_KEY,
                    "dn": dn,
                    "qdp": 0.1 * i,
                    "fp2": 0.1 * i,
                    "fp3": 0.1 * i,
                    "cwr": 0.2,
                    "champ": i + 1,
                    "grid": i + 1,
                    "seasons": 3,
                },
            )


@pytest.fixture
def tiny_prerace_model_path(prerace_pipeline_engine, tmp_path):
    """Train a tiny prerace model on the seeded historical sessions and save it."""
    df = load_prerace_training(prerace_pipeline_engine, max_year=2024)
    model = train_prerace_model(df, train_years=[2022, 2023], cv=2)
    path = tmp_path / "prerace_model.joblib"
    save_model(model, path)
    return path


# ---------------------------------------------------------------------------
# Test 1: happy path — sum-to-1, prediction_type='prerace', lap_number NULL
# ---------------------------------------------------------------------------

def test_compute_and_store_prerace_sums_to_one(prerace_pipeline_engine, tiny_prerace_model_path):
    """
    compute_and_store_prerace inserts one 'prerace' row per driver for the
    target session, with lap_number NULL and win_probability summing to ~1.0.
    """
    from f1_prediction.inference.prerace import compute_and_store_prerace

    compute_and_store_prerace(
        SESSION_KEY, model_path=tiny_prerace_model_path, engine=prerace_pipeline_engine
    )

    with prerace_pipeline_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT driver_number, win_probability, prediction_type, lap_number "
                "FROM predictions WHERE session_key = :sk"
            ),
            {"sk": SESSION_KEY},
        ).fetchall()

    assert len(rows) == len(DRIVERS), f"Expected {len(DRIVERS)} prerace rows, got {len(rows)}"
    for row in rows:
        assert row[2] == "prerace", f"Expected prediction_type='prerace', got {row[2]}"
        assert row[3] is None, f"Expected lap_number IS NULL for prerace rows, got {row[3]}"

    total = sum(row[1] for row in rows)
    assert abs(total - 1.0) < 0.001, f"win_probability sum {total} not within 0.001 of 1.0"


# ---------------------------------------------------------------------------
# Test 2: idempotency (D-11) — a second pipeline startup does not duplicate
# ---------------------------------------------------------------------------

def test_prerace_idempotent_on_second_startup(prerace_pipeline_engine, tiny_prerace_model_path):
    """
    D-11: prerace_already_computed detects existing rows so the pipeline's
    startup logic skips recomputation — a second startup call inserts zero
    new rows for the session.
    """
    from f1_prediction.inference.prerace import compute_and_store_prerace
    from f1_prediction.pipeline import prerace_already_computed

    assert prerace_already_computed(prerace_pipeline_engine, SESSION_KEY) is False

    compute_and_store_prerace(
        SESSION_KEY, model_path=tiny_prerace_model_path, engine=prerace_pipeline_engine
    )
    assert prerace_already_computed(prerace_pipeline_engine, SESSION_KEY) is True

    with prerace_pipeline_engine.connect() as conn:
        count_before = conn.execute(
            text("SELECT COUNT(*) FROM predictions WHERE session_key = :sk"),
            {"sk": SESSION_KEY},
        ).scalar()

    # Mirror the pipeline's startup guard: only recompute if not already present.
    if not prerace_already_computed(prerace_pipeline_engine, SESSION_KEY):
        compute_and_store_prerace(
            SESSION_KEY, model_path=tiny_prerace_model_path, engine=prerace_pipeline_engine
        )

    with prerace_pipeline_engine.connect() as conn:
        count_after = conn.execute(
            text("SELECT COUNT(*) FROM predictions WHERE session_key = :sk"),
            {"sk": SESSION_KEY},
        ).scalar()

    assert count_before == count_after == len(DRIVERS), (
        f"Second startup call must not duplicate prerace rows (D-11): "
        f"before={count_before}, after={count_after}"
    )
