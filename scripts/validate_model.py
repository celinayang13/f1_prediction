"""
Standalone model validation CLI (Phase 02 Plan 04).

Loads saved pre-race and in-race model artifacts from disk and validates that
they produce probability distributions summing to 1.0 ± tolerance on held-out
2024 data. Confirms ROADMAP success criteria PRERACE-02 and INRACE-02.

This exercises the full Phase 3 integration contract:
  joblib.load → predict_proba → per-group softmax → sum-to-1 assertion

Usage:
    # Full validation (prerace + inrace) on held-out 2024 data:
    python scripts/validate_model.py

    # Custom validation year:
    python scripts/validate_model.py --val-year 2024

    # Custom model paths:
    python scripts/validate_model.py --prerace-model data/models/prerace_model.joblib \
                                     --inrace-model data/models/inrace_model.joblib

    # Validate only the pre-race model:
    python scripts/validate_model.py --skip-inrace

    # Validate only the in-race model:
    python scripts/validate_model.py --skip-prerace

    # Custom tolerance (default 0.001 per ROADMAP PRERACE-02/INRACE-02):
    python scripts/validate_model.py --tolerance 0.001

Exit codes:
    0: all requested validations passed
    1: validation failure (sum-to-1 violated) or exception raised
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure src/ is on the path for non-installed runs
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# Ensure scripts/ is on the path so we can import from train.py
sys.path.insert(0, str(Path(__file__).resolve().parent))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.special import softmax  # noqa: E402
from sklearn.metrics import brier_score_loss, log_loss  # noqa: E402

from f1_prediction.db.engine import get_engine  # noqa: E402
from train import (  # noqa: E402
    PRERACE_FEATURE_COLS,
    build_inrace_feature_matrix,
    load_inrace_training,
    load_prerace_training,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core validation helpers
# ---------------------------------------------------------------------------

def _assert_sums_within_tolerance(sums: pd.Series, tolerance: float) -> None:
    """
    Assert that all per-group probability sums are within tolerance of 1.0.

    This is a separately-defined module-level helper (not nested inside
    validate_sum_to_one) so that tests can call it directly with manufactured
    pd.Series inputs to exercise the violation-detection path without going
    through softmax (which mathematically guarantees sum-to-1 and would make
    a violation impossible to construct in a test).

    Args:
        sums: pd.Series of per-group probability sums. Index = group keys,
              values = computed sums (should be ~1.0 for valid distributions).
        tolerance: Maximum allowed deviation from 1.0 (e.g., 0.001).

    Raises:
        AssertionError: If any group sum deviates from 1.0 by more than tolerance.
                        The error message names the offending group keys and their
                        actual sums.
    """
    violations = sums[(sums - 1.0).abs() > tolerance]
    if len(violations) > 0:
        raise AssertionError(
            f"Sum-to-1 violation for groups: {violations.index.tolist()} "
            f"(sums: {violations.values.tolist()})"
        )


def validate_sum_to_one(
    raw_proba: np.ndarray,
    group_keys: pd.DataFrame,
    group_cols: list[str],
    tolerance: float = 0.001,
) -> dict:
    """
    Apply per-group softmax to raw probabilities and assert sum-to-1.

    Softmax is applied independently within each group defined by group_cols.
    For pre-race validation, group by session_key (one race = one group).
    For in-race validation, group by (session_key, lap_number) so each lap
    snapshot is a separate inference event (INRACE-02).

    Args:
        raw_proba: 1-D numpy array of raw predict_proba scores (same length as
                   group_keys). These are the raw classifier outputs BEFORE softmax.
        group_keys: DataFrame containing the grouping columns (same length as
                    raw_proba). Must contain the columns named in group_cols.
        group_cols: List of column names to group by, e.g., ["session_key"] or
                    ["session_key", "lap_number"].
        tolerance: Maximum allowed deviation from 1.0 (default 0.001 per ROADMAP).

    Returns:
        dict with keys:
          - n_groups (int): number of groups validated
          - min_sum (float): minimum per-group probability sum
          - max_sum (float): maximum per-group probability sum
          - violations (list): always [] on success (AssertionError raised otherwise)

    Raises:
        AssertionError: If any group's softmax probabilities do not sum to
                        1.0 within tolerance. Message includes offending group keys.
    """
    df = group_keys.copy()
    df["raw_prob"] = raw_proba
    df["win_prob"] = df.groupby(group_cols)["raw_prob"].transform(
        lambda x: softmax(x.values)
    )
    sums = df.groupby(group_cols)["win_prob"].sum()

    # Delegate the assertion to the separately-testable helper
    _assert_sums_within_tolerance(sums, tolerance)

    return {
        "n_groups": int(len(sums)),
        "min_sum": float(sums.min()),
        "max_sum": float(sums.max()),
        "violations": [],
    }


# ---------------------------------------------------------------------------
# Per-model validation functions
# ---------------------------------------------------------------------------

def validate_prerace_model(model_path: Path, val_year: int, tolerance: float) -> dict:
    """
    Load the pre-race model artifact and validate on held-out val_year data.

    Loads the model via joblib.load, fetches features via load_prerace_training,
    filters to val_year rows, computes predict_proba, applies per-race softmax
    via validate_sum_to_one, and computes Brier score + log loss.

    Args:
        model_path: Path to the pre-race model .joblib file.
        val_year: Year to use as the held-out validation set.
        tolerance: Sum-to-1 tolerance (e.g., 0.001).

    Returns:
        dict with keys: model, model_path, val_year, n_val_rows, brier_score,
        log_loss, n_groups, min_sum, max_sum, violations.

    Raises:
        FileNotFoundError: If model_path does not exist.
        AssertionError: If sum-to-1 fails.
    """
    if not model_path.exists():
        raise FileNotFoundError(
            f"Pre-race model artifact not found: {model_path}. "
            "Run `python scripts/train.py --model prerace` first."
        )

    logger.info("Loading pre-race model from %s ...", model_path)
    model = joblib.load(model_path)

    logger.info("Loading pre-race training data (max_year=%d) ...", val_year)
    df = load_prerace_training(get_engine(), max_year=val_year)
    val_df = df[df["year"] == val_year].copy()

    logger.info("Validating pre-race model on %d rows (year=%d) ...", len(val_df), val_year)

    raw_proba = model.predict_proba(val_df[PRERACE_FEATURE_COLS].values)[:, 1]

    sum_to_one_result = validate_sum_to_one(
        raw_proba,
        val_df[["session_key"]],
        ["session_key"],
        tolerance,
    )

    brier = brier_score_loss(val_df["is_winner"].values, raw_proba)
    ll = log_loss(val_df["is_winner"].values, raw_proba)

    logger.info(
        "Pre-race validation: n_val_rows=%d, n_groups=%d, brier=%.4f, log_loss=%.4f",
        len(val_df), sum_to_one_result["n_groups"], brier, ll,
    )

    return {
        "model": "prerace",
        "model_path": str(model_path),
        "val_year": val_year,
        "n_val_rows": int(len(val_df)),
        "brier_score": round(float(brier), 4),
        "log_loss": round(float(ll), 4),
        **sum_to_one_result,
    }


def validate_inrace_model(model_path: Path, val_year: int, tolerance: float) -> dict:
    """
    Load the in-race model artifact and validate on held-out val_year data.

    Loads the model via joblib.load, fetches features via load_inrace_training,
    filters to val_year rows, builds the 14-column feature matrix, applies
    per-(session_key, lap_number) softmax via validate_sum_to_one, and computes
    Brier score + log loss.

    Args:
        model_path: Path to the in-race model .joblib file.
        val_year: Year to use as the held-out validation set.
        tolerance: Sum-to-1 tolerance (e.g., 0.001).

    Returns:
        dict with keys: model, model_path, val_year, n_val_rows, brier_score,
        log_loss, n_val_lap_groups, n_groups, min_sum, max_sum, violations.

    Raises:
        FileNotFoundError: If model_path does not exist.
        AssertionError: If sum-to-1 fails.
    """
    if not model_path.exists():
        raise FileNotFoundError(
            f"In-race model artifact not found: {model_path}. "
            "Run `python scripts/train.py --model inrace` first."
        )

    logger.info("Loading in-race model from %s ...", model_path)
    model = joblib.load(model_path)

    logger.info("Loading in-race training data (max_year=%d) ...", val_year)
    df = load_inrace_training(get_engine(), max_year=val_year)
    val_df = df[df["year"] == val_year].copy()

    logger.info("Validating in-race model on %d rows (year=%d) ...", len(val_df), val_year)

    X_val = build_inrace_feature_matrix(val_df).values
    raw_proba = model.predict_proba(X_val)[:, 1]

    sum_to_one_result = validate_sum_to_one(
        raw_proba,
        val_df[["session_key", "lap_number"]],
        ["session_key", "lap_number"],
        tolerance,
    )

    brier = brier_score_loss(val_df["is_winner"].values, raw_proba)
    ll = log_loss(val_df["is_winner"].values, raw_proba)

    n_val_lap_groups = sum_to_one_result["n_groups"]

    logger.info(
        "In-race validation: n_val_rows=%d, n_val_lap_groups=%d, brier=%.4f, log_loss=%.4f",
        len(val_df), n_val_lap_groups, brier, ll,
    )

    return {
        "model": "inrace",
        "model_path": str(model_path),
        "val_year": val_year,
        "n_val_rows": int(len(val_df)),
        "brier_score": round(float(brier), 4),
        "log_loss": round(float(ll), 4),
        "n_val_lap_groups": n_val_lap_groups,
        **sum_to_one_result,
    }


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entrypoint for the standalone model validator."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate saved F1 win probability model artifacts on held-out data. "
            "Confirms ROADMAP success criteria PRERACE-02 and INRACE-02: "
            "probabilities sum to 1.0 ± tolerance per race / per lap."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--val-year",
        type=int,
        default=2024,
        metavar="YEAR",
        help="Held-out validation year (ROADMAP success criterion is 2024).",
    )
    parser.add_argument(
        "--prerace-model",
        type=Path,
        default=Path("data/models/prerace_model.joblib"),
        metavar="PATH",
        help="Path to the pre-race model .joblib artifact.",
    )
    parser.add_argument(
        "--inrace-model",
        type=Path,
        default=Path("data/models/inrace_model.joblib"),
        metavar="PATH",
        help="Path to the in-race model .joblib artifact.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.001,
        metavar="FLOAT",
        help="Maximum allowed deviation from 1.0 for sum-to-1 check (PRERACE-02/INRACE-02).",
    )
    parser.add_argument(
        "--skip-prerace",
        action="store_true",
        default=False,
        help="Skip pre-race model validation.",
    )
    parser.add_argument(
        "--skip-inrace",
        action="store_true",
        default=False,
        help="Skip in-race model validation.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    try:
        results = []

        if not args.skip_prerace:
            results.append(
                validate_prerace_model(args.prerace_model, args.val_year, args.tolerance)
            )

        if not args.skip_inrace:
            results.append(
                validate_inrace_model(args.inrace_model, args.val_year, args.tolerance)
            )

        print(json.dumps({"validations": results}, indent=2))
        sys.exit(0)

    except Exception as exc:
        logger.error("Validation failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
