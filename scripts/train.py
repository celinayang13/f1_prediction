"""
Standalone model training CLI (D-09).

Fits the served field-choice win model (inference/field_choice.py) on every
season in the database and writes the artifact plus a .meta.json describing
what it was trained on. Out-of-sample quality is measured separately by
scripts/evaluate.py, which refits the same model season by season.

Usage:
    python scripts/train.py --model prerace
    python scripts/train.py --model inrace

    # Leave later seasons out, or write somewhere else (useful for tests):
    python scripts/train.py --model prerace --max-year 2024 --output-dir /tmp/models

Exit codes:
    0: success
    1: exception raised during training
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure src/ is on the path for non-installed runs
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import scipy  # noqa: E402
import sklearn  # noqa: E402
from sqlalchemy import text  # noqa: E402

from f1_prediction.db.engine import get_engine  # noqa: E402
from f1_prediction.inference.features import (  # noqa: E402
    INRACE_NUMERIC_FEATURE_COLS,
    PRERACE_FEATURE_COLS,
    TYRE_COMPOUND_VALUES,
    build_inrace_feature_matrix,
    encode_tyre_compound,
    predict_inrace_normalized,
    predict_normalized,
)
from f1_prediction.inference.field_choice import FieldChoiceModel  # noqa: E402

logger = logging.getLogger(__name__)


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
                    fp.recent_quali_delta,
                    CASE
                        WHEN fp.finish_position = 1
                         AND (
                            -- Lap-completion filter (Pitfall 3 / Le Castellet 2022 DNF bug):
                            -- FastF1 sessions have real lap numbers; apply strict check.
                            -- OpenF1 sessions store lap_number=0 (API does not return it);
                            -- when max_lap=0 there is no lap data, so trust finish_position.
                            (SELECT MAX(rp2.lap_number)
                             FROM raw_positions rp2
                             WHERE rp2.session_key = fp.session_key
                               AND rp2.driver_number = fp.driver_number) >= rs.total_laps - 1
                            OR
                            COALESCE(
                              (SELECT MAX(rp2.lap_number)
                               FROM raw_positions rp2
                               WHERE rp2.session_key = fp.session_key
                                 AND rp2.driver_number = fp.driver_number),
                              0
                            ) = 0
                         )
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
# In-race data loading
# ---------------------------------------------------------------------------

def load_inrace_training(engine, max_year: int = 2024) -> pd.DataFrame:
    """
    Load in-race features with lap-completion is_winner label.

    Joins features_inrace with features_prerace (for is_winner) and
    raw_sessions (for year/round/total_laps metadata).

    The is_winner CASE expression uses the same lap-completion filter as
    load_prerace_training — including the OR clause for OpenF1 sessions
    where lap_number=0 in raw_positions.

    NULLs are left as-is — XGBoost handles NaN natively (D-05).
    .dropna() is never called on feature columns.
    pit_stop_delta is ~96% NULL — this is expected and intentional.

    Args:
        engine: SQLAlchemy Engine connected to data/f1.db.
        max_year: Include races up to and including this year (default 2024).

    Returns:
        DataFrame with columns: session_key, driver_number, lap_number,
        [9 INRACE_NUMERIC_FEATURE_COLS], tyre_compound (raw string),
        is_winner, year, round_number.
    """
    with engine.connect() as conn:
        df = pd.read_sql(
            text("""
                SELECT
                    fi.session_key,
                    fi.driver_number,
                    fi.lap_number,
                    fi.gap_to_leader,
                    fi.tyre_compound,
                    fi.tyre_age_laps,
                    fi.pit_stop_count,
                    fi.position,
                    fi.interval_ahead,
                    fi.interval_behind,
                    fi.pit_stop_delta,
                    fi.laps_remaining,
                    fp.grid_position,
                    fp.recent_quali_delta,
                    CASE
                        WHEN fp.finish_position = 1
                         AND (
                            -- Lap-completion filter: FastF1 sessions have real lap numbers.
                            -- OpenF1 sessions store lap_number=0; when max_lap=0
                            -- trust finish_position directly (same fix as pre-race loader).
                            (SELECT MAX(rp2.lap_number)
                             FROM raw_positions rp2
                             WHERE rp2.session_key = fp.session_key
                               AND rp2.driver_number = fp.driver_number) >= rs.total_laps - 1
                            OR
                            COALESCE(
                              (SELECT MAX(rp2.lap_number)
                               FROM raw_positions rp2
                               WHERE rp2.session_key = fp.session_key
                                 AND rp2.driver_number = fp.driver_number),
                              0
                            ) = 0
                         )
                        THEN 1
                        ELSE 0
                    END AS is_winner,
                    rs.year,
                    rs.round_number
                FROM features_inrace fi
                JOIN features_prerace fp
                    ON fi.session_key = fp.session_key
                    AND fi.driver_number = fp.driver_number
                JOIN raw_sessions rs ON fi.session_key = rs.session_key
                WHERE rs.session_type = 'Race'
                  AND fp.finish_position IS NOT NULL
                  AND rs.year <= :max_year
                ORDER BY rs.year, fi.session_key, fi.driver_number, fi.lap_number
            """),
            conn,
            params={"max_year": max_year},
        )
    return df


# ---------------------------------------------------------------------------
# Model training
# ---------------------------------------------------------------------------

def train_prerace_model(df: pd.DataFrame, train_years: list[int], cv: int = 4) -> FieldChoiceModel:
    """
    Fit the pre-race field-choice model (see inference/field_choice.py).

    Args:
        df: DataFrame from load_prerace_training.
        train_years: Seasons to train on.
        cv: Race-grouped folds used to choose the L2 penalty.

    Returns:
        Fitted FieldChoiceModel('prerace').

    Raises:
        ValueError: If the training seasons contain no race with exactly one winner.
    """
    return FieldChoiceModel("prerace").fit(df[df["year"].isin(train_years)], cv=cv)


def train_inrace_model(df: pd.DataFrame, train_years: list[int], cv: int = 4) -> FieldChoiceModel:
    """
    Fit the in-race field-choice model: one field per (race, lap).

    Args:
        df: DataFrame from load_inrace_training.
        train_years: Seasons to train on.
        cv: Race-grouped folds used to choose the L2 penalty.

    Returns:
        Fitted FieldChoiceModel('inrace').

    Raises:
        ValueError: If the training seasons contain no lap with exactly one winner.
    """
    return FieldChoiceModel("inrace").fit(df[df["year"].isin(train_years)], cv=cv)


# ---------------------------------------------------------------------------
# Artifact saving
# ---------------------------------------------------------------------------

def save_model(model, path: Path) -> None:
    """
    Serialize a trained model to disk via joblib.

    Creates parent directories as needed.

    Args:
        model: Fitted model (e.g. FieldChoiceModel).
        path: Destination file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def dataset_hash(df: pd.DataFrame) -> str:
    """Short content hash of a training/evaluation frame, for traceability."""
    return hashlib.sha256(
        pd.util.hash_pandas_object(df, index=False).values.tobytes()
    ).hexdigest()[:16]


def code_revision() -> str:
    """Current git commit (with a -dirty suffix for uncommitted changes), or 'unknown'."""
    try:
        return subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def save_metadata(model: FieldChoiceModel, artifact_path: Path, train_df: pd.DataFrame) -> dict:
    """
    Write <artifact>.meta.json next to a model artifact: what it was trained
    on and the fitted coefficients, so served probabilities can be traced.
    """
    meta = {
        "model": model.kind,
        "algorithm": "field-choice (conditional logit)",
        "train_years": sorted(int(y) for y in train_df["year"].unique()),
        "n_train_races": int(train_df["session_key"].nunique()),
        "n_train_fields": model.n_fields_,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest()[:16],
        "train_dataset_hash": dataset_hash(train_df),
        "l2_penalty": model.l2_,
        "coefficients_on_standardized_inputs": model.coefficients(),
        "normalization": "softmax over the field (scores / sum of scores)",
        "library_versions": {
            "scipy": scipy.__version__, "scikit-learn": sklearn.__version__,
            "numpy": np.__version__, "pandas": pd.__version__, "joblib": joblib.__version__,
        },
        "code_revision": code_revision(),
    }
    artifact_path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entrypoint: train one served model on every season up to --max-year."""
    parser = argparse.ArgumentParser(
        description=(
            "Train a field-choice win probability model on historical F1 features. "
            "Writes <model>_model.joblib and <model>_model.meta.json to --output-dir. "
            "Out-of-sample quality is measured by scripts/evaluate.py, not here."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--model",
        choices=["prerace", "inrace"],
        required=True,
        help="Which model to train: prerace (grid and recent qualifying pace) or inrace (lap-by-lap race state).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/models"),
        metavar="DIR",
        help="Directory to write the serialized model artifact.",
    )
    parser.add_argument(
        "--max-year",
        type=int,
        default=9999,
        metavar="YEAR",
        help="Train on seasons up to and including this year (default: every season in the database).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    try:
        load, train = (
            (load_prerace_training, train_prerace_model) if args.model == "prerace"
            else (load_inrace_training, train_inrace_model)
        )
        df = load(get_engine(), max_year=args.max_year)
        train_years = sorted(int(y) for y in df["year"].unique())
        logger.info("Training %s model on seasons %s (%d rows)", args.model, train_years, len(df))
        model = train(df, train_years)

        # Every field's probabilities must sum to 1
        keys = ["session_key"] if args.model == "prerace" else ["session_key", "lap_number"]
        result = (
            predict_normalized(model, df, PRERACE_FEATURE_COLS) if args.model == "prerace"
            else predict_inrace_normalized(model, df)
        )
        sums = result.groupby(keys)["win_prob"].sum()
        assert ((sums - 1.0).abs() < 0.001).all(), (
            f"Sum-to-1 assertion failed for fields: {sums[(sums - 1.0).abs() >= 0.001].head(5).to_dict()}"
        )

        artifact_path = args.output_dir / f"{args.model}_model.joblib"
        save_model(model, artifact_path)
        print(json.dumps(save_metadata(model, artifact_path, df)))
        logger.info("Saved %s model to %s", args.model, artifact_path)
        sys.exit(0)
    except Exception as exc:
        logger.error("Training failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
