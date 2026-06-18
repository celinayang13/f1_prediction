"""
Model training pipeline for pre-race and in-race models (Plans 02-01 and 02-02).

Implements:
  - build_encoder: OrdinalEncoder for string categoricals (team_name / tyre_compound)
  - train_xgb_classifier: XGBClassifier with locked D-03 hyperparameters
  - calibrate_model: Platt scaling via CalibratedClassifierCV(FrozenEstimator(...))
  - save_artifact: joblib serialization of the model dict artifact
  - train_prerace_model: orchestration entry point for pre-race model
  - train_inrace_model: orchestration entry point for in-race model (Plan 02-02)

Security:
  - SQL queries delegated to loader.py (text() with bound params, T-03-05)
  - Artifact paths are hardcoded Path constants (T-02-01, T-02.2-01: no user-input derivation)
  - joblib artifacts loaded only from local filesystem (trust boundary: disk → joblib.load)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.preprocessing import OrdinalEncoder
import xgboost as xgb

from f1_prediction.ml.loader import (
    PRERACE_CAT_COLS,
    PRERACE_FEATURE_COLS,
    PRERACE_TARGET,
    load_prerace_df,
    INRACE_CAT_COLS,
    INRACE_FEATURE_COLS,
    INRACE_TARGET,
    load_inrace_df,
)
from f1_prediction.ml.evaluate import (
    compute_log_loss,
    compute_top3_hit_rate,
    per_session_softmax,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Artifact path constants (T-02-01, T-02.2-01: hardcoded, no user-input derivation)
# ---------------------------------------------------------------------------

PRERACE_MODEL_PATH: Path = Path("data/models/prerace_model.joblib")
INRACE_MODEL_PATH: Path = Path("data/models/inrace_model.joblib")


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def build_encoder(cat_cols: list[str]) -> OrdinalEncoder:
    """
    Build an OrdinalEncoder for string categorical columns.

    Uses unknown_value=np.nan so that unseen categories at inference time
    (e.g., a new team in 2026) are encoded as NaN rather than -1.
    This aligns with XGBoost's native missing-value sentinel (NaN),
    ensuring unknown categories are routed through the missing-value branch
    during tree splits (RESEARCH Pitfall 7 override: np.nan, NOT -1).

    The CONTEXT.md D-06 decision specified unknown_value=-1; RESEARCH §Pitfall 7
    overrides this because XGBoost's missing sentinel is NaN by default.
    Using -1 without setting XGBClassifier(missing=-1) would silently produce
    wrong predictions for unknown categories.

    Args:
        cat_cols: List of categorical column names to encode.

    Returns:
        Unfitted OrdinalEncoder configured for safe unknown-category handling.
    """
    return OrdinalEncoder(
        handle_unknown="use_encoded_value",
        unknown_value=np.nan,           # NOT -1 — aligns with XGBoost NaN sentinel
        encoded_missing_value=np.nan,   # NULL inputs → NaN output for XGBoost NaN branch
    )


def train_xgb_classifier(
    X_train: pd.DataFrame, y_train: pd.Series
) -> xgb.XGBClassifier:
    """
    Train an XGBClassifier with the locked D-03 hyperparameters.

    Hyperparameters are fixed per D-03 (no GridSearch or early stopping):
      - n_estimators=300, max_depth=4, learning_rate=0.05
      - scale_pos_weight=19 for 1:19 winner-to-non-winner class imbalance (D-02)
      - enable_categorical=True to use ordinal-encoded categoricals as native
        categorical features (D-07)
      - tree_method="hist" required for enable_categorical (RESEARCH anti-pattern note)
      - random_state=42 for reproducibility

    Args:
        X_train: Training feature DataFrame (categoricals already OrdinalEncoded).
        y_train: Binary training labels (1 = winner, 0 = non-winner).

    Returns:
        Fitted XGBClassifier.
    """
    model = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=19,        # 1:19 class imbalance (D-02)
        enable_categorical=True,    # treat ordinal-encoded cols as categoricals (D-07)
        tree_method="hist",         # required for enable_categorical
        random_state=42,
        eval_metric="logloss",
    )
    model.fit(X_train.values, y_train.values)
    return model


def calibrate_model(
    fitted_model: xgb.XGBClassifier,
    X_test: pd.DataFrame,
    y_test: pd.Series,
) -> CalibratedClassifierCV:
    """
    Calibrate a pre-fitted XGBoost model using Platt scaling (sigmoid method).

    Uses FrozenEstimator to wrap the pre-fitted model before passing to
    CalibratedClassifierCV. FrozenEstimator prevents sklearn from re-fitting
    the base estimator during calibration (RESEARCH Pitfall 1: the old cv=prefit
    parameter was removed in sklearn 1.8 — FrozenEstimator is the correct API
    for sklearn 1.6+).

    The calibration layer is fitted on the 2025 test set (D-09), converting
    raw XGBoost scores to better-calibrated probabilities. Platt scaling
    (sigmoid method) is preferred over isotonic regression at this data scale
    (~480 test rows) because isotonic overfits on small calibration sets.

    The cv parameter is clamped to min(5, n_samples) so that small test sets
    (e.g., in unit tests with few seeded rows) do not raise ValueError from
    CalibratedClassifierCV's n_splits > n_samples check.

    Args:
        fitted_model: A pre-fitted XGBClassifier from train_xgb_classifier.
        X_test: Test feature DataFrame (categoricals already OrdinalEncoded).
        y_test: Binary test labels (1 = winner, 0 = non-winner).

    Returns:
        Fitted CalibratedClassifierCV wrapping the original model.
    """
    n_samples = len(X_test)
    # Clamp cv to min(5, min_class_count) — CalibratedClassifierCV requires at
    # least cv examples per class. With real data (~480 rows, ~24 winners) this
    # is always 5. With small test fixtures it gracefully reduces to 2.
    min_class_count = int(y_test.value_counts().min()) if hasattr(y_test, "value_counts") else n_samples
    cv = min(5, max(2, min_class_count))
    if cv < 5:
        logger.debug(
            "calibrate_model: clamping cv to %d (min_class_count=%d, n_samples=%d)",
            cv,
            min_class_count,
            n_samples,
        )

    # CRITICAL: use FrozenEstimator + cv=N, NOT the removed cv=prefit parameter
    calibrated = CalibratedClassifierCV(
        FrozenEstimator(fitted_model),
        method="sigmoid",   # Platt scaling
        cv=cv,
    )
    calibrated.fit(X_test.values, y_test.values)
    return calibrated


def save_artifact(
    path: Path,
    *,
    model: Any,
    feature_cols: list[str],
    cat_cols: list[str],
    encoder: OrdinalEncoder,
    trained_on: str,
    tested_on: str,
) -> dict:
    """
    Serialize the model artifact dict to a joblib file.

    The artifact dict is the Phase 3 inference contract. Phase 3 loads
    this dict and uses feature_cols + encoder to prepare inference inputs
    in the correct order without re-fitting.

    Artifact keys (D-11 contract):
      model        — CalibratedClassifierCV wrapping the XGBClassifier
      feature_cols — ordered list of feature column names (inference order)
      cat_cols     — which columns were ordinally encoded
      encoder      — fitted OrdinalEncoder; Phase 3 calls encoder.transform()
      trained_on   — string description of training years ("2022-2024")
      tested_on    — string description of test year ("2025")

    Args:
        path: Target file path for the joblib artifact.
        model: The calibrated classifier.
        feature_cols: Ordered feature column names.
        cat_cols: Categorical column names.
        encoder: Fitted OrdinalEncoder.
        trained_on: Training period description string.
        tested_on: Test period description string.

    Returns:
        The artifact dict that was serialized (for programmatic callers).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "model": model,
        "feature_cols": feature_cols,
        "cat_cols": cat_cols,
        "encoder": encoder,
        "trained_on": trained_on,
        "tested_on": tested_on,
    }
    joblib.dump(artifact, path)
    logger.info("Saved pre-race artifact to %s", path)
    return artifact


# ---------------------------------------------------------------------------
# Orchestration entry point
# ---------------------------------------------------------------------------


def train_prerace_model(
    engine,
    *,
    model_path: Path | None = None,
) -> dict:
    """
    Orchestrate the full pre-race model training pipeline.

    Steps:
      1. Load features_prerace joined with raw_sessions.year via load_prerace_df.
      2. Guard against empty DataFrame (RESEARCH Pitfall 5).
      3. Temporal split: train on year <= 2024, test on year == 2025 (D-04, D-05).
      4. Derive binary labels: finish_position == 1 → label = 1.
      5. Build OrdinalEncoder, fit on X_train[cat_cols] only (RESEARCH Pitfall 3:
         never fit on full dataset before split).
      6. Transform both X_train and X_test using the fitted encoder.
      7. Train XGBClassifier via train_xgb_classifier.
      8. Calibrate via calibrate_model (FrozenEstimator + Platt scaling).
      9. Evaluate: compute_log_loss + compute_top3_hit_rate; print to stdout (D-16).
     10. Save artifact via save_artifact.
     11. Return the artifact dict (for programmatic callers / tests).

    Args:
        engine: SQLAlchemy Engine from get_engine().
        model_path: Override for the artifact path. Defaults to PRERACE_MODEL_PATH.
                    Used by tests to write under tmp_path instead of data/models/.

    Returns:
        The serialized artifact dict with keys:
        {model, feature_cols, cat_cols, encoder, trained_on, tested_on}.

    Raises:
        RuntimeError: If features_prerace is empty (no rows returned from the DB).
                      Run 'python scripts/backfill.py' first to populate the table.
    """
    artifact_path: Path = model_path if model_path is not None else PRERACE_MODEL_PATH

    # Step 1: Load features
    with engine.connect() as conn:
        df = load_prerace_df(conn)

    # Step 2: Guard against empty DataFrame (RESEARCH Pitfall 5)
    if len(df) == 0:
        raise RuntimeError(
            "features_prerace is empty — run scripts/backfill.py first"
        )

    # Step 3: Temporal split (D-04, D-05)
    train_df = df[df["year"] <= 2024].copy()
    test_df = df[df["year"] == 2025].copy()

    logger.info(
        "Temporal split: train=%d rows (2022-2024), test=%d rows (2025)",
        len(train_df),
        len(test_df),
    )

    if len(train_df) == 0:
        raise RuntimeError(
            "features_prerace is empty — run scripts/backfill.py first"
        )

    if len(test_df) == 0:
        raise RuntimeError(
            "No 2025 test rows in features_prerace — run scripts/backfill.py "
            "with --years 2025 to populate 2025 race data"
        )

    # Step 4: Binary labels — 1 if winner (finish_position == 1), else 0
    train_df = train_df.copy()
    test_df = test_df.copy()
    train_df["label"] = (train_df[PRERACE_TARGET] == 1).astype(int)
    test_df["label"] = (test_df[PRERACE_TARGET] == 1).astype(int)

    # Step 5: Build encoder and fit on X_train ONLY (RESEARCH Pitfall 3)
    encoder = build_encoder(PRERACE_CAT_COLS)
    X_train = train_df[PRERACE_FEATURE_COLS].copy()
    X_test = test_df[PRERACE_FEATURE_COLS].copy()

    # fit_transform on train, transform only on test — prevents data leakage
    X_train[PRERACE_CAT_COLS] = encoder.fit_transform(X_train[PRERACE_CAT_COLS])
    X_test[PRERACE_CAT_COLS] = encoder.transform(X_test[PRERACE_CAT_COLS])

    # Step 6: Train
    logger.info("Training XGBClassifier on %d rows...", len(X_train))
    model = train_xgb_classifier(X_train, train_df["label"])

    # Step 7: Calibrate (Platt scaling on test set — D-09)
    logger.info("Calibrating model on %d test rows...", len(X_test))
    calibrated = calibrate_model(model, X_test, test_df["label"])

    # Step 8: Evaluate — compute metrics on the calibrated model
    proba = calibrated.predict_proba(X_test.values)   # shape: (n, 2)
    win_scores = proba[:, 1]                           # win-class probabilities

    ll = compute_log_loss(test_df["label"].values, win_scores)

    # Add win scores to test_df for top-3 hit rate computation
    test_df = test_df.copy()
    test_df["win_score"] = win_scores
    # Apply per-session softmax for the top-3 hit rate (D-10)
    test_df["win_prob"] = per_session_softmax(test_df, "win_score", "session_key")

    rate, hits, total = compute_top3_hit_rate(
        test_df, prob_col="win_prob", label_col="label", session_col="session_key"
    )

    # D-16: print metrics to stdout (not logger — kept in pipeline.py for testability)
    print(
        f"prerace  log_loss={ll:.4f}  top3_hit_rate={rate:.3f}  ({hits}/{total} races)"
    )

    # Step 9: Save artifact (D-11 dict contract)
    artifact = save_artifact(
        artifact_path,
        model=calibrated,
        feature_cols=PRERACE_FEATURE_COLS,
        cat_cols=PRERACE_CAT_COLS,
        encoder=encoder,
        trained_on="2022-2024",
        tested_on="2025",
    )

    return artifact


def train_inrace_model(
    engine,
    *,
    model_path: Path | None = None,
) -> dict:
    """
    Orchestrate the full in-race model training pipeline (Plan 02-02).

    Trains an XGBoost model on per-lap race state (position, gap_to_leader,
    tyre compound, pit history) and calibrates it on 2025 test data.

    The training label (finish_position) is joined from features_prerace
    because FeaturesInrace does not store finish_position. An INNER JOIN
    is used so drivers without a known finish_position are excluded from
    training (RESEARCH Open Question Q1 resolution, T-02.2-04).

    Steps:
      1. Load features_inrace joined with features_prerace.finish_position and
         raw_sessions.year via load_inrace_df (INNER JOIN on session_key, driver_number).
      2. Guard against empty DataFrame (RESEARCH Pitfall 5).
      3. Temporal split: train on year <= 2024, test on year == 2025 (D-04, D-05).
      4. Derive binary labels: finish_position == 1 → label = 1.
      5. Build a SEPARATE OrdinalEncoder for INRACE_CAT_COLS (["tyre_compound"]).
         Fit only on X_train[INRACE_CAT_COLS] (RESEARCH Pitfall 3).
      6. Transform both X_train and X_test using the fitted encoder.
      7. Train XGBClassifier via train_xgb_classifier (same D-03 hyperparameters).
      8. Calibrate via calibrate_model (FrozenEstimator + Platt scaling, D-09).
      9. Evaluate: log_loss + per-(session_key, lap_number) top-3 hit rate at final lap.
         Print to stdout in D-16 format.
     10. Save artifact via save_artifact (D-11 dict contract: same 6 keys as pre-race).
     11. Return the artifact dict.

    Args:
        engine: SQLAlchemy Engine from get_engine().
        model_path: Override for the artifact path. Defaults to INRACE_MODEL_PATH.
                    Used by tests to write under tmp_path instead of data/models/.

    Returns:
        The serialized artifact dict with keys:
        {model, feature_cols, cat_cols, encoder, trained_on, tested_on}.
        cat_cols == ["tyre_compound"] (different from pre-race cat_cols ["team_name"]).

    Raises:
        RuntimeError: If features_inrace is empty (no rows after the INNER JOIN).
                      Run 'python scripts/backfill.py' first to populate the table.
    """
    artifact_path: Path = model_path if model_path is not None else INRACE_MODEL_PATH

    # Step 1: Load features (INNER JOIN to features_prerace for finish_position label)
    with engine.connect() as conn:
        df = load_inrace_df(conn)

    # Step 2: Guard against empty DataFrame (RESEARCH Pitfall 5)
    if len(df) == 0:
        raise RuntimeError(
            "features_inrace is empty — run scripts/backfill.py first"
        )

    # Step 3: Temporal split (D-04, D-05)
    train_df = df[df["year"] <= 2024].copy()
    test_df = df[df["year"] == 2025].copy()

    logger.info(
        "In-race temporal split: train=%d rows (2022-2024), test=%d rows (2025)",
        len(train_df),
        len(test_df),
    )

    if len(train_df) == 0:
        raise RuntimeError(
            "features_inrace is empty — run scripts/backfill.py first"
        )

    if len(test_df) == 0:
        raise RuntimeError(
            "No 2025 test rows in features_inrace — run scripts/backfill.py "
            "with --years 2025 to populate 2025 race data"
        )

    # Step 4: Binary labels — 1 if winner (finish_position == 1), else 0
    train_df = train_df.copy()
    test_df = test_df.copy()
    train_df["label"] = (train_df[INRACE_TARGET] == 1).astype(int)
    test_df["label"] = (test_df[INRACE_TARGET] == 1).astype(int)

    # Step 5: Build a SEPARATE encoder for in-race cat cols and fit on X_train ONLY.
    # This encoder is distinct from the pre-race encoder — different cat_cols sets
    # (["tyre_compound"] vs ["team_name"]) and must NOT be shared (RESEARCH Pitfall 3).
    encoder = build_encoder(INRACE_CAT_COLS)
    X_train = train_df[INRACE_FEATURE_COLS].copy()
    X_test = test_df[INRACE_FEATURE_COLS].copy()

    # fit_transform on train, transform only on test — prevents data leakage (Pitfall 3)
    X_train[INRACE_CAT_COLS] = encoder.fit_transform(X_train[INRACE_CAT_COLS])
    X_test[INRACE_CAT_COLS] = encoder.transform(X_test[INRACE_CAT_COLS])

    # Step 6: Train (same D-03 hyperparameters as pre-race model)
    logger.info("Training in-race XGBClassifier on %d rows...", len(X_train))
    model = train_xgb_classifier(X_train, train_df["label"])

    # Step 7: Calibrate (Platt scaling on test set — D-09)
    logger.info("Calibrating in-race model on %d test rows...", len(X_test))
    calibrated = calibrate_model(model, X_test, test_df["label"])

    # Step 8: Evaluate — compute metrics on the calibrated model
    proba = calibrated.predict_proba(X_test.values)   # shape: (n, 2)
    win_scores = proba[:, 1]                           # win-class probabilities

    ll = compute_log_loss(test_df["label"].values, win_scores)

    # Add win scores to test_df for top-3 hit rate computation.
    # For in-race, per-lap softmax uses a composite (session_key, lap_number) key
    # since each lap is an independent inference moment (INRACE-02, Pitfall 4).
    test_df = test_df.copy()
    test_df["win_score"] = win_scores
    test_df["session_lap_key"] = (
        test_df["session_key"].astype(str) + "_" + test_df["lap_number"].astype(str)
    )
    # Apply per-(session, lap) softmax so each lap group sums to 1.0 (D-10, INRACE-02)
    test_df["win_prob"] = per_session_softmax(test_df, "win_score", "session_lap_key")

    # D-16: top-3 hit rate at final lap only — filter to max lap_number per session
    test_df["max_lap"] = test_df.groupby("session_key")["lap_number"].transform("max")
    final_lap_df = test_df[test_df["lap_number"] == test_df["max_lap"]].copy()

    rate, hits, total = compute_top3_hit_rate(
        final_lap_df,
        prob_col="win_prob",
        label_col="label",
        session_col="session_key",
    )

    # D-16: print metrics to stdout in the specified format
    print(
        f"inrace  log_loss={ll:.4f}  top3_hit_rate_final_lap={rate:.3f}  ({hits}/{total} races)"
    )

    # Step 9: Save artifact (D-11 dict contract — same 6-key shape as pre-race)
    artifact = save_artifact(
        artifact_path,
        model=calibrated,
        feature_cols=INRACE_FEATURE_COLS,
        cat_cols=INRACE_CAT_COLS,
        encoder=encoder,
        trained_on="2022-2024",
        tested_on="2025",
    )

    return artifact
