"""
Tests for the in-race prediction pipeline (Plan 03-03, DASH-01/DASH-03).

Covers:
  - compute_and_store_inrace_latest_lap rebuilds in-race features for the
    full session (Pattern 1 — reuses build_features_for_session(kind="inrace"),
    does not hand-roll incremental per-lap joins), slices to MAX(lap_number),
    and inserts one 'inrace' prediction row per driver for that lap with
    win_probability summing to ~1.0 (INRACE-02 contract reused).
  - pipeline.run_inference_after_poll gates the post-poll inference hook on
    poll_result["laps_inserted"] > 0 (D-09).

Uses the tmp_engine fixture (isolated SQLite, no data/f1.db writes). A tiny
inrace model is trained via the existing train_inrace_model/save_model
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
    load_inrace_training,
    save_model,
    train_inrace_model,
)

DRIVERS = [1, 11, 44]

# (session_key, year, round_number, total_laps, winner_driver_number)
TRAINING_SESSIONS = [
    (101, 2022, 1, 20, 1),
    (102, 2022, 2, 20, 11),
    (103, 2023, 1, 20, 44),
]
LAP_SNAPSHOTS = [1, 10, 20]
TYRE_BY_LAP = {1: "SOFT", 10: "MEDIUM", 20: "HARD"}

TARGET_SESSION_KEY = 9999
TARGET_TOTAL_LAPS = 58
TARGET_LATEST_LAP = 3
# Distinct lap durations per driver so cumulative gap_to_leader differs (driver 1 leads).
TARGET_LAP_DURATIONS = {1: 90.0, 11: 91.0, 44: 92.5}
TARGET_POSITIONS = {1: 1, 11: 2, 44: 3}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def inrace_pipeline_engine(tmp_engine):
    """
    tmp_engine seeded with:
      - 3 historical Race sessions (2022 x2, 2023) with features_prerace
        (finish_position) + features_inrace lap snapshots, so a tiny inrace
        model can be trained.
      - 1 target "latest race" session (2024, session_key=9999) currently at
        lap 3 — seeded via raw_laps/raw_positions/raw_stints (NOT
        features_inrace directly), because compute_and_store_inrace_latest_lap
        rebuilds features_inrace from raw tables (Pattern 1).
    """
    _seed_training_sessions(tmp_engine)
    _seed_target_session(tmp_engine)
    return tmp_engine


def _seed_training_sessions(engine) -> None:
    """Seed 3 historical Race sessions so train_inrace_model has data to fit."""
    with engine.begin() as conn:
        for sk, year, rn, total_laps, winner in TRAINING_SESSIONS:
            conn.execute(
                text(
                    "INSERT INTO raw_sessions "
                    "(session_key, year, round_number, session_type, session_name, "
                    "date_start, date_end, total_laps, source) "
                    "VALUES (:sk, :year, :rn, 'Race', 'R', '2022-01-01', '2022-01-01', :tl, 'fastf1')"
                ),
                {"sk": sk, "year": year, "rn": rn, "tl": total_laps},
            )

            for i, dn in enumerate(DRIVERS):
                finish_position = 1 if dn == winner else i + 2
                conn.execute(
                    text(
                        "INSERT INTO features_prerace "
                        "(session_key, driver_number, finish_position) "
                        "VALUES (:sk, :dn, :fpos)"
                    ),
                    {"sk": sk, "dn": dn, "fpos": finish_position},
                )

                for lap in LAP_SNAPSHOTS:
                    # raw_positions: satisfies load_inrace_training's lap-completion
                    # check (MAX(lap_number) >= total_laps - 1) for the winner label.
                    position = 1 if dn == winner else i + 2
                    conn.execute(
                        text(
                            "INSERT INTO raw_positions "
                            "(session_key, driver_number, lap_number, position) "
                            "VALUES (:sk, :dn, :lap, :pos)"
                        ),
                        {"sk": sk, "dn": dn, "lap": lap, "pos": position},
                    )

                    gap_to_leader = 0.0 if dn == winner else 2.0 * (i + 1) + 0.1 * lap
                    conn.execute(
                        text(
                            "INSERT INTO features_inrace "
                            "(session_key, driver_number, lap_number, gap_to_leader, "
                            "tyre_compound, tyre_age_laps, pit_stop_count, position, "
                            "interval_ahead, interval_behind, pit_stop_delta, laps_remaining) "
                            "VALUES (:sk, :dn, :lap, :gap, :tc, :ta, :pc, :pos, "
                            ":ia, :ib, :pd, :lr)"
                        ),
                        {
                            "sk": sk,
                            "dn": dn,
                            "lap": lap,
                            "gap": gap_to_leader,
                            "tc": TYRE_BY_LAP[lap],
                            "ta": lap,
                            "pc": 0,
                            "pos": position,
                            "ia": None if position == 1 else gap_to_leader * 0.5,
                            "ib": None,
                            "pd": None,
                            "lr": total_laps - lap,
                        },
                    )


def _seed_target_session(engine) -> None:
    """Seed the 'latest race' session at lap 3 via raw_* tables (Pattern 1 input)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO raw_sessions "
                "(session_key, year, round_number, session_type, session_name, "
                "date_start, date_end, total_laps, source) "
                "VALUES (:sk, 2024, 5, 'Race', 'R', '2024-05-01', '2024-05-01', :tl, 'openf1')"
            ),
            {"sk": TARGET_SESSION_KEY, "tl": TARGET_TOTAL_LAPS},
        )
        for dn in DRIVERS:
            conn.execute(
                text(
                    "INSERT INTO raw_stints "
                    "(session_key, driver_number, stint_number, lap_start, lap_end, "
                    "compound, tyre_age_at_start) "
                    "VALUES (:sk, :dn, 1, 1, :tl, 'MEDIUM', 0)"
                ),
                {"sk": TARGET_SESSION_KEY, "dn": dn, "tl": TARGET_TOTAL_LAPS},
            )
            for lap in range(1, TARGET_LATEST_LAP + 1):
                conn.execute(
                    text(
                        "INSERT INTO raw_laps "
                        "(session_key, driver_number, lap_number, lap_duration) "
                        "VALUES (:sk, :dn, :lap, :dur)"
                    ),
                    {
                        "sk": TARGET_SESSION_KEY,
                        "dn": dn,
                        "lap": lap,
                        "dur": TARGET_LAP_DURATIONS[dn],
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO raw_positions "
                        "(session_key, driver_number, lap_number, position) "
                        "VALUES (:sk, :dn, :lap, :pos)"
                    ),
                    {
                        "sk": TARGET_SESSION_KEY,
                        "dn": dn,
                        "lap": lap,
                        "pos": TARGET_POSITIONS[dn],
                    },
                )


@pytest.fixture
def tiny_inrace_model_path(inrace_pipeline_engine, tmp_path):
    """Train a tiny inrace model on the seeded historical sessions and save it."""
    df = load_inrace_training(inrace_pipeline_engine, max_year=2024)
    model = train_inrace_model(df, train_years=[2022, 2023], cv=2)
    path = tmp_path / "inrace_model.joblib"
    save_model(model, path)
    return path


# ---------------------------------------------------------------------------
# Test 1: happy path — rebuild-then-slice, sum-to-1, prediction_type='inrace'
# ---------------------------------------------------------------------------

def test_compute_and_store_inrace_latest_lap_sums_to_one(
    inrace_pipeline_engine, tiny_inrace_model_path
):
    """
    compute_and_store_inrace_latest_lap rebuilds in-race features for the
    target session (Pattern 1), slices to MAX(lap_number), and inserts one
    'inrace' row per driver for that lap with win_probability summing to ~1.0.
    """
    from f1_prediction.inference.inrace import compute_and_store_inrace_latest_lap

    inserted = compute_and_store_inrace_latest_lap(
        TARGET_SESSION_KEY, model_path=tiny_inrace_model_path, engine=inrace_pipeline_engine
    )
    assert inserted == len(DRIVERS)

    with inrace_pipeline_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT driver_number, win_probability, prediction_type, lap_number "
                "FROM predictions WHERE session_key = :sk"
            ),
            {"sk": TARGET_SESSION_KEY},
        ).fetchall()

    assert len(rows) == len(DRIVERS), f"Expected {len(DRIVERS)} inrace rows, got {len(rows)}"
    for row in rows:
        assert row[2] == "inrace", f"Expected prediction_type='inrace', got {row[2]}"
        assert row[3] == TARGET_LATEST_LAP, (
            f"Expected lap_number == MAX(lap_number) == {TARGET_LATEST_LAP}, got {row[3]}"
        )

    total = sum(row[1] for row in rows)
    assert abs(total - 1.0) < 0.001, f"win_probability sum {total} not within 0.001 of 1.0"


# ---------------------------------------------------------------------------
# Test 2: hook gating (D-09) — inference triggers only when laps_inserted > 0
# ---------------------------------------------------------------------------

def test_run_inference_after_poll_gates_on_laps_inserted(
    inrace_pipeline_engine, tiny_inrace_model_path, monkeypatch
):
    """
    run_inference_after_poll(engine, session_key, poll_result) triggers
    compute_and_store_inrace_latest_lap only when poll_result["laps_inserted"]
    is > 0; a laps_inserted == 0 result is a no-op (zero new prediction rows).
    """
    import f1_prediction.pipeline as pipeline_module

    calls: list[int] = []

    def _fake_compute(session_key, *, engine=None):
        calls.append(session_key)
        return 1

    monkeypatch.setattr(
        pipeline_module, "compute_and_store_inrace_latest_lap", _fake_compute
    )

    # laps_inserted == 0 -> no-op, must not call the inference hook.
    result_noop = pipeline_module.run_inference_after_poll(
        inrace_pipeline_engine, TARGET_SESSION_KEY, {"laps_inserted": 0}
    )
    assert calls == [], "run_inference_after_poll must not trigger inference when laps_inserted == 0"
    assert result_noop == 0

    with inrace_pipeline_engine.connect() as conn:
        count_after_noop = conn.execute(
            text("SELECT COUNT(*) FROM predictions WHERE session_key = :sk"),
            {"sk": TARGET_SESSION_KEY},
        ).scalar()
    assert count_after_noop == 0, "laps_inserted == 0 must insert zero new prediction rows"

    # laps_inserted > 0 -> hook triggers inference exactly once.
    result_triggered = pipeline_module.run_inference_after_poll(
        inrace_pipeline_engine, TARGET_SESSION_KEY, {"laps_inserted": 2}
    )
    assert calls == [TARGET_SESSION_KEY], (
        "run_inference_after_poll must trigger inference when laps_inserted > 0"
    )
    assert result_triggered == 1
