"""
Standalone model training CLI (D-09).

Trains a calibrated XGBoost classifier on historical F1 race features
and serializes the artifact for use in Phase 3 inference.

Usage:
    # Train the pre-race model (qualifying + practice features):
    python scripts/train.py --model prerace

    # Train with a different held-out validation year:
    python scripts/train.py --model prerace --val-year 2023

    # Write artifact to a custom directory (useful for tests):
    python scripts/train.py --model prerace --output-dir /tmp/models

    # In-race model (wired in Plan 02-03):
    python scripts/train.py --model inrace

Exit codes:
    0: success
    1: exception raised during training
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure src/ is on the path for non-installed runs
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import xgboost as xgb  # noqa: E402
from scipy.special import softmax  # noqa: E402
from sklearn.calibration import CalibratedClassifierCV  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss  # noqa: E402
from sqlalchemy import text  # noqa: E402

from f1_prediction.db.engine import get_engine  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (D-03)
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


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_prerace_training(engine, max_year: int = 2024) -> pd.DataFrame:
    """
    Load pre-race features with lap-completion is_winner label.

    The is_winner label uses a lap-completion subquery to exclude DNF leaders
    (e.g., Le Castellet 2022 where driver 16 led but DNF'd at lap 17).
    Only the driver with finish_position=1 AND MAX(lap_number) >= total_laps - 1
    is labeled is_winner=1.

    NULLs are left as-is — XGBoost handles missing values natively (D-05).
    .dropna() is never called on feature columns.

    Args:
        engine: SQLAlchemy Engine connected to data/f1.db.
        max_year: Include races up to and including this year (default 2024).

    Returns:
        DataFrame with columns: session_key, driver_number,
        [7 PRERACE_FEATURE_COLS], is_winner, year, round_number.
    """
    with engine.connect() as conn:
        df = pd.read_sql(
            text("""
                SELECT
                    fp.session_key,
                    fp.driver_number,
                    fp.qualifying_delta_to_pole,
                    fp.practice_pace_fp2,
                    fp.practice_pace_fp3,
                    fp.circuit_win_rate,
                    fp.championship_position,
                    fp.grid_position,
                    fp.driver_seasons_in_f1,
                    CASE
                        WHEN fp.finish_position = 1
                         AND (
                            SELECT MAX(rp2.lap_number)
                            FROM raw_positions rp2
                            WHERE rp2.session_key = fp.session_key
                              AND rp2.driver_number = fp.driver_number
                         ) >= rs.total_laps - 1
                        THEN 1
                        ELSE 0
                    END AS is_winner,
                    rs.year,
                    rs.round_number
                FROM features_prerace fp
                JOIN raw_sessions rs ON fp.session_key = rs.session_key
                WHERE rs.session_type = 'Race'
                  AND fp.finish_position IS NOT NULL
                  AND rs.year <= :max_year
                ORDER BY rs.year, rs.round_number, fp.driver_number
            """),
            conn,
            params={"max_year": max_year},
        )
    return df


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------

def train_prerace_model(
    df: pd.DataFrame,
    train_years: list[int],
    cv: int = 5,
) -> CalibratedClassifierCV:
    """
    Train a calibrated XGBoost pre-race win probability classifier.

    Uses scale_pos_weight to handle extreme class imbalance (1 winner per
    ~20 drivers). CalibratedClassifierCV with isotonic regression converts
    raw XGBoost scores to calibrated probabilities.

    CRITICAL: cv=5 (integer default), NOT cv='prefit' (removed in scikit-learn 1.x).
    CRITICAL: scale_pos_weight (XGBoost), NOT is_unbalance (LightGBM-only).

    Args:
        df: DataFrame from load_prerace_training.
        train_years: List of years to include in the training set.
        cv: Number of cross-validation folds for CalibratedClassifierCV.
            Default is 5 (production setting). Use cv=2 for test fixtures
            with fewer than 5 examples per class.

    Returns:
        Fitted CalibratedClassifierCV wrapping an XGBClassifier.

    Raises:
        ValueError: If no winner rows found in the training set.
    """
    train_mask = df["year"].isin(train_years)
    X_train = df.loc[train_mask, PRERACE_FEATURE_COLS].values
    y_train = df.loc[train_mask, "is_winner"].values

    n_total = len(y_train)
    n_pos = int(y_train.sum())
    if n_pos == 0:
        raise ValueError(
            f"No winner rows found in training years {train_years}. "
            "Check that features_prerace is populated and is_winner derivation is correct."
        )

    scale_pos_weight = float((n_total - n_pos) / n_pos)
    logger.info(
        "Training pre-race model: n_train=%d, n_winners=%d, scale_pos_weight=%.2f",
        n_total, n_pos, scale_pos_weight,
    )

    base_clf = xgb.XGBClassifier(
        n_estimators=500,
        learning_rate=0.05,
        max_depth=6,
        scale_pos_weight=scale_pos_weight,
        objective="binary:logistic",
        eval_metric="logloss",
        verbosity=0,
    )
    model = CalibratedClassifierCV(base_clf, cv=cv, method="isotonic")
    model.fit(X_train, y_train)
    return model


# ---------------------------------------------------------------------------
# Artifact saving
# ---------------------------------------------------------------------------

def save_model(model, path: Path) -> None:
    """
    Serialize a trained model to disk via joblib.

    Creates parent directories as needed.

    Args:
        model: Fitted scikit-learn estimator (e.g. CalibratedClassifierCV).
        path: Destination file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


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


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entrypoint for the standalone model trainer."""
    parser = argparse.ArgumentParser(
        description=(
            "Train a calibrated XGBoost win probability model on historical F1 features. "
            "Produces a serialized CalibratedClassifierCV artifact in --output-dir."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--model",
        choices=["prerace", "inrace"],
        required=True,
        help="Which model to train: prerace (qualifying/practice features) or inrace (lap-by-lap features).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/models"),
        metavar="DIR",
        help="Directory to write the serialized model artifact.",
    )
    parser.add_argument(
        "--val-year",
        type=int,
        default=2024,
        metavar="YEAR",
        help="Held-out validation year (excluded from training). ROADMAP success criterion is 2024.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    try:
        if args.model == "prerace":
            _train_prerace(args)
        elif args.model == "inrace":
            raise NotImplementedError("Wired in Plan 02-03")
        sys.exit(0)
    except Exception as exc:
        logger.error("Training failed: %s", exc, exc_info=True)
        sys.exit(1)


def _train_prerace(args) -> None:
    """Execute the pre-race training pipeline."""
    logger.info("Loading prerace training data (max_year=%d)...", args.val_year)
    df = load_prerace_training(get_engine(), max_year=args.val_year)

    train_years = sorted(y for y in df["year"].unique() if y < args.val_year)
    train_mask = df["year"].isin(train_years)
    val_mask = df["year"] == args.val_year

    logger.info(
        "Data loaded: total_rows=%d, train_rows=%d, val_rows=%d, "
        "train_years=%s, val_year=%d, n_winners_in_train=%d",
        len(df),
        int(train_mask.sum()),
        int(val_mask.sum()),
        train_years,
        args.val_year,
        int(df.loc[train_mask, "is_winner"].sum()),
    )

    model = train_prerace_model(df, train_years, cv=5)

    # Validation on held-out year
    val_df = df[val_mask].copy()
    y_val = val_df["is_winner"].values
    raw_proba_val = model.predict_proba(val_df[PRERACE_FEATURE_COLS].values)[:, 1]

    brier = brier_score_loss(y_val, raw_proba_val)
    ll = log_loss(y_val, raw_proba_val)

    # Apply per-race softmax and assert sum-to-1
    result = predict_with_softmax(model, val_df, PRERACE_FEATURE_COLS)
    sums = result.groupby("session_key")["win_prob"].sum()
    assert ((sums - 1.0).abs() < 0.001).all(), (
        f"Per-race sum-to-1 assertion failed! Violating sessions: "
        f"{sums[(sums - 1.0).abs() >= 0.001].to_dict()}"
    )

    metrics = {
        "model": "prerace",
        "train_years": train_years,
        "val_year": int(args.val_year),
        "n_train": int(train_mask.sum()),
        "n_val": int(val_mask.sum()),
        "brier_score": round(float(brier), 4),
        "log_loss": round(float(ll), 4),
        "n_val_races": int(sums.shape[0]),
        "sum_to_one_min": float(sums.min()),
        "sum_to_one_max": float(sums.max()),
    }
    print(json.dumps(metrics))

    artifact_path = args.output_dir / "prerace_model.joblib"
    save_model(model, artifact_path)
    logger.info("Saved prerace model to %s", artifact_path)


if __name__ == "__main__":
    main()
