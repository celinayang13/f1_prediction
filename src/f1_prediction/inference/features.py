"""
Shared inference feature constants and helpers (Pitfall 4/5 — single source of truth).

Extracted verbatim from scripts/train.py so that training-time and
inference-time feature columns cannot drift. Both scripts/train.py and the
Phase 3 pipeline import these symbols from this module.

CRITICAL: Do not change any function signatures, column orderings, or
groupby keys here — a reorder silently corrupts predict_proba output with
no exception (Pitfall 5).
"""
from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd
from scipy.special import softmax

# ---------------------------------------------------------------------------
# Constants (D-03 / D-04)
# ---------------------------------------------------------------------------

PRERACE_FEATURE_COLS = [
    "qualifying_delta_to_pole",
    "practice_pace_fp2",
    "practice_pace_fp3",
    "circuit_win_rate",
    "championship_position",
    "grid_position",
    "driver_seasons_in_f1",
]

# D-04: 9 numeric in-race features (tyre_compound handled separately via one-hot)
INRACE_NUMERIC_FEATURE_COLS = [
    "gap_to_leader",
    "tyre_age_laps",
    "pit_stop_count",
    "position",
    "interval_ahead",
    "interval_behind",
    "pit_stop_delta",
    "lap_number",
    "laps_remaining",
]

# Canonical tyre compound values — always produce these 5 columns (RESEARCH.md Pattern 3)
TYRE_COMPOUND_VALUES = ["HARD", "INTERMEDIATE", "MEDIUM", "SOFT", "WET"]


def encode_tyre_compound(df: pd.DataFrame) -> pd.DataFrame:
    """
    One-hot encode the tyre_compound column into 5 canonical binary columns.

    Uses pd.get_dummies with dummy_na=False so that NULL (no tyre data) rows
    become all-zero rows — correct sentinel for XGBoost NaN handling.
    Reindexes the output to the canonical 5 columns (TYRE_COMPOUND_VALUES)
    so that absent compounds (e.g., no WET laps in a dry race) still produce
    zero-valued columns. This ensures the trained model's input schema is stable
    across different DB snapshots.

    Args:
        df: DataFrame containing a 'tyre_compound' column.

    Returns:
        DataFrame with exactly 5 columns: tyre_HARD, tyre_INTERMEDIATE,
        tyre_MEDIUM, tyre_SOFT, tyre_WET. Same index as df.
    """
    dummies = pd.get_dummies(df["tyre_compound"], prefix="tyre", dummy_na=False)
    canonical_cols = [f"tyre_{v}" for v in TYRE_COMPOUND_VALUES]
    dummies = dummies.reindex(columns=canonical_cols, fill_value=0)
    return dummies


def build_inrace_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the in-race feature matrix (14 columns) from a raw inrace DataFrame.

    Concatenates the 9 numeric columns (INRACE_NUMERIC_FEATURE_COLS) with
    the 5 one-hot tyre columns (encode_tyre_compound), in that order.
    Caller passes .values to fit/predict.

    Args:
        df: DataFrame from load_inrace_training (or a slice of it).

    Returns:
        DataFrame with 14 columns: 9 numeric + 5 tyre dummies.
    """
    numeric_part = df[INRACE_NUMERIC_FEATURE_COLS]
    tyre_part = encode_tyre_compound(df)
    return pd.concat([numeric_part, tyre_part], axis=1)


# ---------------------------------------------------------------------------
# Prediction + softmax normalization
# ---------------------------------------------------------------------------

def predict_with_softmax(
    model,
    df: pd.DataFrame,
    feature_cols: list[str],
) -> pd.DataFrame:
    """
    Predict win probabilities and apply per-race softmax normalization.

    Softmax is applied per session_key (per race), NOT globally across all
    rows. This ensures probabilities sum to 1.0 within each race (PRERACE-02).

    Args:
        model: Fitted CalibratedClassifierCV.
        df: DataFrame containing feature_cols and a 'session_key' column.
        feature_cols: List of feature column names to pass to predict_proba.

    Returns:
        df with two new columns added:
          - raw_prob: raw probability from predict_proba (before softmax)
          - win_prob: per-race softmax-normalized win probability
    """
    df = df.copy()
    raw_proba = model.predict_proba(df[feature_cols].values)[:, 1]
    df["raw_prob"] = raw_proba
    df["win_prob"] = df.groupby("session_key")["raw_prob"].transform(
        lambda x: softmax(x.values)
    )
    return df


def predict_inrace_with_softmax(
    model,
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Predict in-race win probabilities and apply per-(session_key, lap_number)
    softmax normalization.

    Each lap within a race is a separate prediction event (INRACE-02). Softmax
    is applied per (session_key, lap_number) group — NOT per session_key alone.
    This ensures each lap's probability distribution sums to 1.0 independently.

    Args:
        model: Fitted CalibratedClassifierCV (in-race).
        df: DataFrame containing INRACE_NUMERIC_FEATURE_COLS and tyre_compound,
            plus 'session_key' and 'lap_number' columns.

    Returns:
        df copy with two new columns:
          - raw_prob: raw probability from predict_proba (before softmax)
          - win_prob: per-(session_key, lap_number) softmax-normalized probability
    """
    df = df.copy()
    X = build_inrace_feature_matrix(df).values
    raw_proba = model.predict_proba(X)[:, 1]
    df["raw_prob"] = raw_proba
    df["win_prob"] = df.groupby(["session_key", "lap_number"])["raw_prob"].transform(
        lambda x: softmax(x.values)
    )
    return df


# ---------------------------------------------------------------------------
# Model loading (inverse of scripts/train.py::save_model)
# ---------------------------------------------------------------------------

def load_model(path: Path):
    """
    Deserialize a trained model from disk via joblib.

    Args:
        path: Path to a .joblib model artifact (e.g. saved by
            scripts/train.py::save_model).

    Returns:
        The deserialized model object (e.g. CalibratedClassifierCV).
    """
    return joblib.load(path)
