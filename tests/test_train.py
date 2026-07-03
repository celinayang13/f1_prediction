"""
Tests for the pre-race model training pipeline (Plan 02-02).

Covers:
  - Feature set matches the 7 D-03 columns (PRERACE-01)
  - is_winner label excludes DNF leaders via lap-completion filter (Pitfall 3)
  - CalibratedClassifierCV is returned by train_prerace_model
  - Per-race softmax probabilities sum to 1.0 ± 1e-6 (PRERACE-02)
  - Model artifact exists and is loadable (PRERACE-03)
  - Static AST check: cv='prefit' absent (Pitfall 2 regression)
  - Static AST check: scale_pos_weight present, is_unbalance absent (Pitfall 7 regression)

All unit tests use prerace_train_engine (isolated SQLite, no data/f1.db writes).
Tests 6 and 7 are static source-text checks (no fixture needed).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

# Make scripts/train.py importable without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from f1_prediction.db.engine import get_engine, init_db  # noqa: E402
from train import (  # noqa: E402
    INRACE_NUMERIC_FEATURE_COLS,
    PRERACE_FEATURE_COLS,
    TYRE_COMPOUND_VALUES,
    build_inrace_feature_matrix,
    encode_tyre_compound,
    load_inrace_training,
    load_prerace_training,
    predict_inrace_with_softmax,
    predict_with_softmax,
    save_model,
    train_inrace_model,
    train_prerace_model,
)
from sklearn.calibration import CalibratedClassifierCV  # noqa: E402


# ---------------------------------------------------------------------------
# Test fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def prerace_train_engine(tmp_path):
    """
    Isolated SQLite engine seeded with minimal training data:
    - 4 Race sessions (2022 R1, 2022 R2, 2023 R1, 2024 R1)
    - 3 drivers per session: 1, 11, 44
    - Driver 1 wins sessions 1/2/3; driver 11 wins session 4
    - Session 2 (total_laps=70): driver 44 has finish_position=1 (DNF bug)
      but only 17 laps completed — the lap-completion filter must set is_winner=0
    All SQL uses text() with bound :param names — no f-string SQL (T-03-05).
    """
    db_path = tmp_path / "test_train.db"
    engine = get_engine(db_path)
    init_db(engine)
    _seed_prerace_data(engine)
    return engine


def _seed_prerace_data(engine) -> None:
    """Seed raw_sessions, features_prerace, and raw_positions for 4 race sessions."""
    sessions = [
        # (session_key, year, round_number, total_laps, meeting_key)
        (1, 2022, 1, 53, 1001),
        (2, 2022, 2, 70, 1002),
        (3, 2023, 1, 58, 2001),
        (4, 2024, 1, 57, 3001),
    ]

    # winner_by_session: {session_key: driver_number_that_wins}
    # Session 2 has a DNF bug: driver 44 also has finish_position=1
    # but only completes 17 laps → must be excluded by lap-completion filter
    winner_by_session = {1: 1, 2: 1, 3: 1, 4: 11}
    dnf_driver = (2, 44)  # (session_key, driver_number) — DNF leader bug

    drivers = [1, 11, 44]

    with engine.begin() as conn:
        # Insert raw_sessions
        for sk, year, rn, total_laps, mk in sessions:
            conn.execute(
                text(
                    "INSERT INTO raw_sessions "
                    "(session_key, year, round_number, session_type, session_name, "
                    "date_start, date_end, total_laps, source, meeting_key) "
                    "VALUES (:sk, :year, :rn, 'Race', 'R', '2022-01-01', '2022-01-01', "
                    ":tl, 'fastf1', :mk)"
                ),
                {"sk": sk, "year": year, "rn": rn, "tl": total_laps, "mk": mk},
            )

        # Insert features_prerace
        for sk, year, rn, total_laps, mk in sessions:
            real_winner = winner_by_session[sk]
            for i, dn in enumerate(drivers):
                # Determine finish_position
                if dn == real_winner:
                    fp = 1
                elif (sk, dn) == dnf_driver:
                    # DNF leader bug: set finish_position=1 even though driver DNF'd
                    fp = 1
                else:
                    fp = i + 1 if i > 0 else 2  # positions 2 or 3

                # Qualifying delta: driver 1 has 0.0, driver 11 has 0.5, driver 44 has 1.0
                delta_map = {1: 0.0, 11: 0.5, 44: 1.0}
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
                        "qdp": delta_map[dn],
                        "fp2": 0.1 * i,
                        "fp3": 0.1 * i if dn != 44 else None,  # driver 44 has NULL fp3 (sprint)
                        "cwr": 0.3 if dn == 1 else None,  # only driver 1 has a win rate
                        "champ": i + 1,
                        "grid": i + 1,
                        "seasons": 3 - i,
                        "fpos": fp,
                    },
                )

        # Insert raw_positions (lap-completion data)
        # Finishers: complete total_laps - 1 laps
        # DNF (session_key=2, driver_number=44): only 17 laps
        for sk, year, rn, total_laps, mk in sessions:
            for dn in drivers:
                if (sk, dn) == dnf_driver:
                    max_lap = 17  # DNF at lap 17
                else:
                    max_lap = total_laps - 1  # normal finisher

                pos_map = {1: 1, 11: 2, 44: 3}
                for lap in range(1, max_lap + 1):
                    conn.execute(
                        text(
                            "INSERT INTO raw_positions "
                            "(session_key, driver_number, lap_number, position) "
                            "VALUES (:sk, :dn, :ln, :pos)"
                        ),
                        {"sk": sk, "dn": dn, "ln": lap, "pos": pos_map[dn]},
                    )


# ---------------------------------------------------------------------------
# Test 1: Feature set
# ---------------------------------------------------------------------------

def test_prerace_feature_set(prerace_train_engine):
    """PRERACE-01: load_prerace_training returns exactly the 7 D-03 feature columns."""
    df = load_prerace_training(prerace_train_engine, max_year=2024)

    # All 7 feature columns must be present
    for col in PRERACE_FEATURE_COLS:
        assert col in df.columns, f"Missing feature column: {col}"

    # Metadata columns must be present
    for col in ("session_key", "driver_number", "is_winner", "year", "round_number"):
        assert col in df.columns, f"Missing metadata column: {col}"

    # No extra columns beyond the expected set
    expected_cols = set(PRERACE_FEATURE_COLS) | {"session_key", "driver_number", "is_winner", "year", "round_number"}
    extra_cols = set(df.columns) - expected_cols
    assert extra_cols == set(), f"Unexpected extra columns: {extra_cols}"

    # 4 sessions × 3 drivers = 12 rows
    assert len(df) == 12, f"Expected 12 rows, got {len(df)}"


# ---------------------------------------------------------------------------
# Test 2: DNF winner exclusion (Pitfall 3)
# ---------------------------------------------------------------------------

def test_prerace_is_winner_excludes_dnf(prerace_train_engine):
    """
    PITFALL-3 regression: session_key=2 driver_number=44 has finish_position=1
    but only completed 17 of 70 laps — must be labeled is_winner=0.
    Exactly one is_winner=1 per session.
    """
    df = load_prerace_training(prerace_train_engine, max_year=2024)

    # The DNF row must be labeled 0
    dnf_row = df[(df["session_key"] == 2) & (df["driver_number"] == 44)]
    assert len(dnf_row) == 1, "Expected exactly one row for session_key=2, driver_number=44"
    assert dnf_row.iloc[0]["is_winner"] == 0, (
        f"DNF leader (session_key=2, driver_number=44) should be is_winner=0, "
        f"got {dnf_row.iloc[0]['is_winner']}"
    )

    # Exactly one winner per session
    per_session_winners = df.groupby("session_key")["is_winner"].sum()
    assert per_session_winners.tolist() == [1, 1, 1, 1], (
        f"Expected exactly 1 winner per session, got: {per_session_winners.to_dict()}"
    )


# ---------------------------------------------------------------------------
# Test 3: train_prerace_model returns CalibratedClassifierCV
# ---------------------------------------------------------------------------

def test_prerace_train_returns_calibrated_model(prerace_train_engine):
    """train_prerace_model returns a CalibratedClassifierCV with predict_proba."""
    df = load_prerace_training(prerace_train_engine, max_year=2024)
    model = train_prerace_model(df, train_years=[2022, 2023], cv=2)

    assert isinstance(model, CalibratedClassifierCV), (
        f"Expected CalibratedClassifierCV, got {type(model).__name__}"
    )
    assert hasattr(model, "predict_proba"), "Model missing predict_proba method"

    # Predict on the 3 drivers in session 4 (year 2024)
    val_df = df[df["year"] == 2024]
    assert len(val_df) == 3, f"Expected 3 val rows (1 session × 3 drivers), got {len(val_df)}"

    proba = model.predict_proba(val_df[PRERACE_FEATURE_COLS].values)
    assert proba.shape == (3, 2), (
        f"predict_proba shape should be (3, 2), got {proba.shape}"
    )
    # Each row's probabilities must sum to ~1.0
    assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-6), (
        f"predict_proba rows do not sum to 1.0: {proba.sum(axis=1)}"
    )


# ---------------------------------------------------------------------------
# Test 4: Per-race softmax sums to 1.0
# ---------------------------------------------------------------------------

def test_prerace_sum_to_one(prerace_train_engine):
    """PRERACE-02: per-race win probabilities sum to 1.0 ± 1e-6."""
    df = load_prerace_training(prerace_train_engine, max_year=2024)
    model = train_prerace_model(df, train_years=[2022, 2023], cv=2)

    val_df = df[df["year"] == 2024].copy()
    result = predict_with_softmax(model, val_df, PRERACE_FEATURE_COLS)

    sums = result.groupby("session_key")["win_prob"].sum()
    assert ((sums - 1.0).abs() < 1e-6).all(), (
        f"Per-race probabilities do not sum to 1.0. Actual sums: {sums.to_dict()}"
    )


# ---------------------------------------------------------------------------
# Test 5: Model artifact save/load round-trip
# ---------------------------------------------------------------------------

def test_prerace_artifact_exists(tmp_path, prerace_train_engine):
    """PRERACE-03: save_model writes a loadable CalibratedClassifierCV artifact."""
    df = load_prerace_training(prerace_train_engine, max_year=2024)
    model = train_prerace_model(df, train_years=[2022, 2023], cv=2)

    artifact_path = tmp_path / "prerace_model.joblib"
    save_model(model, artifact_path)

    # File must exist and be non-empty
    assert artifact_path.exists(), f"Artifact not found at {artifact_path}"
    assert artifact_path.stat().st_size > 0, "Artifact file is empty"

    # Round-trip: loaded model must have predict_proba
    loaded = joblib.load(artifact_path)
    assert hasattr(loaded, "predict_proba"), "Loaded artifact missing predict_proba"
    assert type(loaded).__name__ == "CalibratedClassifierCV", (
        f"Loaded artifact has unexpected type: {type(loaded).__name__}"
    )


# ---------------------------------------------------------------------------
# Test 6: Static AST — no cv='prefit' (Pitfall 2 regression)
# ---------------------------------------------------------------------------

def test_prerace_no_cv_prefit_in_source():
    """
    AST-level regression: train.py must NOT pass cv='prefit' to any call.
    cv='prefit' was removed in scikit-learn 1.x and raises InvalidParameterError.
    This check scans actual AST Call nodes, ignoring comments and docstrings.
    """
    train_src = Path(__file__).resolve().parents[1] / "scripts" / "train.py"
    tree = ast.parse(train_src.read_text())

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in (node.keywords or []):
            if kw.arg == "cv" and isinstance(kw.value, ast.Constant):
                assert kw.value.value != "prefit", (
                    f"cv='prefit' found in train.py at line {node.lineno} — "
                    "this was removed in scikit-learn 1.x. Use cv=5 (integer)."
                )


# ---------------------------------------------------------------------------
# Test 7: Static AST — scale_pos_weight present, is_unbalance absent (Pitfall 7)
# ---------------------------------------------------------------------------

def test_prerace_uses_scale_pos_weight():
    """
    AST-level regression: train.py must use scale_pos_weight (XGBoost) and
    must NOT use is_unbalance (LightGBM-only parameter silently ignored by XGBoost).
    """
    train_src = Path(__file__).resolve().parents[1] / "scripts" / "train.py"
    tree = ast.parse(train_src.read_text())

    all_kwargs = [
        kw.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in (node.keywords or [])
    ]

    assert "scale_pos_weight" in all_kwargs, (
        "scale_pos_weight kwarg not found in any call in train.py — "
        "XGBoost requires scale_pos_weight for class imbalance (not is_unbalance)"
    )
    assert "is_unbalance" not in all_kwargs, (
        "is_unbalance kwarg found in train.py — this is a LightGBM-only parameter "
        "silently ignored by XGBoost. Use scale_pos_weight instead."
    )


# ===========================================================================
# In-race tests (Plan 02-03)
# ===========================================================================
# Tests 8–15 cover INRACE-01 through INRACE-03.
# All use inrace_train_engine (isolated SQLite, no data/f1.db writes).
# ===========================================================================

@pytest.fixture
def inrace_train_engine(tmp_path):
    """
    Isolated SQLite engine seeded with minimal in-race training data.

    Reuses the same 4 race sessions and features_prerace rows as
    prerace_train_engine, plus seeds features_inrace with 4 lap snapshots
    per (session, driver).

    DNF scenario: session_key=2, driver_number=44 only completes 17 laps
    in raw_positions — lap-20 and lap-30 inrace rows are omitted for that
    driver to mirror the real DNF situation.

    Tyre compounds cycle SOFT → MEDIUM → HARD across laps to exercise
    the one-hot encoder across 3 compounds (WET and INTERMEDIATE absent
    intentionally — encode_tyre_compound must still produce those columns).

    All SQL uses text() with bound :param names — no f-string SQL (T-03-05).
    """
    db_path = tmp_path / "test_inrace_train.db"
    engine = get_engine(db_path)
    init_db(engine)
    _seed_prerace_data(engine)   # reuse existing helper for sessions + prerace rows
    _seed_inrace_data(engine)
    return engine


def _seed_inrace_data(engine) -> None:
    """
    Seed features_inrace with 4 lap snapshots per (session, driver).

    Sessions: (sk=1, tl=53), (sk=2, tl=70), (sk=3, tl=58), (sk=4, tl=57)
    Drivers: 1, 11, 44
    Lap snapshots: 1, 10, 20, 30

    Skip rows for session_key=2, driver_number=44 at laps 20 and 30
    (DNF at lap 17 — no lap data after that point).

    Tyre compounds rotate: lap 1→SOFT, lap 10→SOFT, lap 20→MEDIUM, lap 30→HARD.
    No WET or INTERMEDIATE laps — encode_tyre_compound must still produce columns.

    Position and gap_to_leader vary dramatically across laps to ensure the
    model produces lap-varying predictions (required by test_inrace_lap_by_lap_variation):
      Laps 1, 10: driver 11 leads (position=1, gap=0); driver 1 is 3rd then 2nd
      Laps 20, 30: driver 1 takes over (position=1, gap=0); driver 11 is 2nd
    This creates clearly different feature values across laps so the model
    sees different race states at each lap snapshot.
    """
    sessions = [
        # (session_key, total_laps)
        (1, 53),
        (2, 70),
        (3, 58),
        (4, 57),
    ]
    drivers = [1, 11, 44]
    lap_snapshots = [1, 10, 20, 30]
    dnf_driver = (2, 44)  # DNF at lap 17

    # Tyre compound by lap
    tyre_by_lap = {1: "SOFT", 10: "SOFT", 20: "MEDIUM", 30: "HARD"}

    # Position varies by lap: driver 11 leads early laps, driver 1 takes over later
    lap_position = {
        1:  {1: 3, 11: 1, 44: 2},
        10: {1: 2, 11: 1, 44: 3},
        20: {1: 1, 11: 2, 44: 3},
        30: {1: 1, 11: 2, 44: 3},
    }
    # Gap to leader matches position (0 for leader, positive for others)
    lap_gap = {
        1:  {1: 4.0, 11: 0.0, 44: 0.8},
        10: {1: 1.5, 11: 0.0, 44: 6.0},
        20: {1: 0.0, 11: 2.0, 44: 12.0},
        30: {1: 0.0, 11: 4.5, 44: 20.0},
    }

    with engine.begin() as conn:
        for sk, total_laps in sessions:
            for dn in drivers:
                for lap in lap_snapshots:
                    # Skip laps after DNF
                    if (sk, dn) == dnf_driver and lap > 17:
                        continue
                    tyre_compound = tyre_by_lap[lap]
                    tyre_age = lap % 15  # simple age approximation
                    pit_count = lap // 25
                    pos = lap_position[lap][dn]
                    gap = lap_gap[lap][dn]
                    interval_a = None if pos == 1 else gap * 0.4
                    interval_b = None if pos == 3 else gap * 0.3
                    # pit_stop_delta is NULL most laps (expected 96% NULL in real data)
                    pit_delta = 22.5 if (lap == 20 and dn == 11) else None
                    laps_remaining = total_laps - lap

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
                            "gap": gap,
                            "tc": tyre_compound,
                            "ta": tyre_age,
                            "pc": pit_count,
                            "pos": pos,
                            "ia": interval_a,
                            "ib": interval_b,
                            "pd": pit_delta,
                            "lr": laps_remaining,
                        },
                    )


# ---------------------------------------------------------------------------
# Test 8: In-race feature set (INRACE-01)
# ---------------------------------------------------------------------------

def test_inrace_feature_set(inrace_train_engine):
    """INRACE-01: load_inrace_training returns all required column groups."""
    df = load_inrace_training(inrace_train_engine, max_year=2024)

    # All 9 numeric inrace feature columns must be present
    for col in INRACE_NUMERIC_FEATURE_COLS:
        assert col in df.columns, f"Missing inrace feature column: {col}"

    # tyre_compound (raw string) and metadata columns must also be present
    for col in ("tyre_compound", "session_key", "driver_number", "lap_number",
                "is_winner", "year", "round_number"):
        assert col in df.columns, f"Missing column: {col}"

    # Row count: 4 sessions × 3 drivers × 4 laps = 48, minus 2 DNF-skipped rows
    # (session_key=2, driver_number=44: laps 20 and 30 omitted)
    assert len(df) == 46, f"Expected 46 rows (48 - 2 DNF skipped), got {len(df)}"


# ---------------------------------------------------------------------------
# Test 9: Tyre one-hot columns (INRACE-01, Pattern 3)
# ---------------------------------------------------------------------------

def test_inrace_tyre_one_hot_columns(inrace_train_engine):
    """
    encode_tyre_compound always returns the canonical 5 columns, even when
    the seeded data has no WET or INTERMEDIATE rows.
    """
    df = load_inrace_training(inrace_train_engine, max_year=2024)
    dummies = encode_tyre_compound(df)

    # Exactly the canonical 5 columns in order
    expected_cols = [f"tyre_{v}" for v in TYRE_COMPOUND_VALUES]
    assert list(dummies.columns) == expected_cols, (
        f"Expected tyre columns {expected_cols}, got {list(dummies.columns)}"
    )

    # Each row has exactly one column == 1 (or True) and the rest == 0
    row_sums = dummies.sum(axis=1)
    assert (row_sums == 1).all(), (
        f"Each row should have exactly 1 active tyre column. Sums: {row_sums.value_counts().to_dict()}"
    )

    # WET and INTERMEDIATE must exist as columns even though no rows use them
    assert "tyre_WET" in dummies.columns, "tyre_WET column missing (no WET in data but column must exist)"
    assert "tyre_INTERMEDIATE" in dummies.columns, "tyre_INTERMEDIATE column missing"
    assert (dummies["tyre_WET"] == 0).all(), "tyre_WET column should be all-zero"
    assert (dummies["tyre_INTERMEDIATE"] == 0).all(), "tyre_INTERMEDIATE column should be all-zero"


# ---------------------------------------------------------------------------
# Test 10: Pit stop and tyre features present (INRACE-03)
# ---------------------------------------------------------------------------

def test_inrace_pit_tyre_features_present(inrace_train_engine):
    """INRACE-03: pit_stop_count, tyre_compound, tyre_age_laps are present and non-empty."""
    df = load_inrace_training(inrace_train_engine, max_year=2024)

    for col in ("pit_stop_count", "tyre_compound", "tyre_age_laps"):
        assert col in df.columns, f"INRACE-03: missing column {col}"
        assert df[col].notna().any(), f"INRACE-03: all values NULL for {col}"


# ---------------------------------------------------------------------------
# Test 11: train_inrace_model returns CalibratedClassifierCV
# ---------------------------------------------------------------------------

def test_inrace_train_returns_calibrated_model(inrace_train_engine):
    """train_inrace_model returns a CalibratedClassifierCV with predict_proba."""
    df = load_inrace_training(inrace_train_engine, max_year=2024)
    model = train_inrace_model(df, train_years=[2022, 2023], cv=2)

    assert isinstance(model, CalibratedClassifierCV), (
        f"Expected CalibratedClassifierCV, got {type(model).__name__}"
    )
    assert hasattr(model, "predict_proba"), "Model missing predict_proba method"


# ---------------------------------------------------------------------------
# Test 12: Per-(session_key, lap_number) softmax sums to 1.0 (INRACE-02)
# ---------------------------------------------------------------------------

def test_inrace_sum_to_one(inrace_train_engine):
    """INRACE-02: per-(session_key, lap_number) win probabilities sum to 1.0 ± 1e-6."""
    df = load_inrace_training(inrace_train_engine, max_year=2024)
    model = train_inrace_model(df, train_years=[2022, 2023], cv=2)

    val_df = df[df["year"] == 2024].copy()
    result = predict_inrace_with_softmax(model, val_df)

    sums = result.groupby(["session_key", "lap_number"])["win_prob"].sum()
    assert ((sums - 1.0).abs() < 1e-6).all(), (
        f"Per-lap probabilities do not sum to 1.0. Actual sums: {sums.to_dict()}"
    )


# ---------------------------------------------------------------------------
# Test 13: Lap-by-lap variation (INRACE-02)
# ---------------------------------------------------------------------------

def test_inrace_lap_by_lap_variation(inrace_train_engine):
    """
    INRACE-02 regression: per-lap probability distributions must not be
    identical across laps. The model must produce distinct outputs for
    different lap snapshots in the same session.
    """
    df = load_inrace_training(inrace_train_engine, max_year=2024)
    model = train_inrace_model(df, train_years=[2022, 2023], cv=2)

    val_df = df[df["year"] == 2024].copy()
    result = predict_inrace_with_softmax(model, val_df)

    # Pick the session in val
    val_session = int(val_df["session_key"].iloc[0])
    session_result = result[result["session_key"] == val_session]

    # Pivot: rows=driver_number, columns=lap_number, values=win_prob
    pivot = session_result.pivot(
        index="driver_number", columns="lap_number", values="win_prob"
    )
    # If lap-by-lap variation exists, std across lap columns is non-zero for at least one driver
    assert pivot.std(axis=1).sum() > 0, (
        "Win probability distributions are identical across laps — "
        "lap-by-lap variation (INRACE-02) is not working. "
        f"Pivot:\n{pivot}"
    )


# ---------------------------------------------------------------------------
# Test 14: Model artifact save/load round-trip (INRACE-03)
# ---------------------------------------------------------------------------

def test_inrace_artifact_exists(tmp_path, inrace_train_engine):
    """In-race model artifact: save_model writes a loadable CalibratedClassifierCV."""
    df = load_inrace_training(inrace_train_engine, max_year=2024)
    model = train_inrace_model(df, train_years=[2022, 2023], cv=2)

    artifact_path = tmp_path / "inrace_model.joblib"
    save_model(model, artifact_path)

    assert artifact_path.exists(), f"Artifact not found at {artifact_path}"
    assert artifact_path.stat().st_size > 0, "Artifact file is empty"

    loaded = joblib.load(artifact_path)
    assert hasattr(loaded, "predict_proba"), "Loaded artifact missing predict_proba"
    assert type(loaded).__name__ == "CalibratedClassifierCV", (
        f"Loaded artifact has unexpected type: {type(loaded).__name__}"
    )


# ---------------------------------------------------------------------------
# Test 15: Static source check — in-race softmax grouping (INRACE-02)
# ---------------------------------------------------------------------------

def test_inrace_softmax_grouped_by_lap_in_source():
    """
    Static regression: train.py must group in-race softmax by
    (session_key, lap_number), not by session_key alone.
    Grouping by session_key only would average across laps, violating INRACE-02.
    """
    train_src = Path(__file__).resolve().parents[1] / "scripts" / "train.py"
    src = train_src.read_text()

    # Accept either single or double quotes
    assert (
        'groupby(["session_key", "lap_number"])' in src
        or "groupby(['session_key', 'lap_number'])" in src
    ), (
        "scripts/train.py does not contain groupby(['session_key', 'lap_number']) — "
        "in-race softmax must group by both session_key AND lap_number (INRACE-02). "
        "Grouping by session_key alone would collapse all laps into one distribution."
    )
