"""
Pre-race win probability inference (D-09, PRERACE-02 contract reused).

Loads the trained prerace_model.joblib artifact, computes win probabilities
from the features_prerace rows for a given Race session, and writes them to
the predictions table with prediction_type='prerace' (lap_number NULL).

Reuses inference/features.py's load_model/predict_with_softmax verbatim —
softmax is applied per session_key so probabilities sum to ~1.0 across the
field (PRERACE-02). Do not reimplement softmax here (Pitfall 4/5).

Security: all SQL uses text() with bound :param names — no f-string SQL (T-03-05).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from f1_prediction.db.engine import get_engine
from f1_prediction.db.upsert import bulk_insert_rows
from f1_prediction.inference.features import (
    PRERACE_FEATURE_COLS,
    load_model,
    predict_with_softmax,
)

logger = logging.getLogger(__name__)

__all__ = ["compute_and_store_prerace"]

DEFAULT_MODEL_PATH = "data/models/prerace_model.joblib"


def compute_and_store_prerace(
    session_key: int,
    model_path: "str | Path" = DEFAULT_MODEL_PATH,
    *,
    engine: Optional[Engine] = None,
) -> int:
    """
    Compute pre-race win probabilities for a session and insert into predictions.

    Args:
        session_key: Race session_key to compute predictions for.
        model_path: Path to the trained prerace_model.joblib artifact.
        engine: SQLAlchemy Engine (defaults to get_engine()).

    Returns:
        Number of prediction rows inserted. Returns 0 (and logs a warning)
        if no features_prerace rows exist yet for the session.
    """
    if engine is None:
        engine = get_engine()

    model = load_model(Path(model_path))

    # WR-02: read features in one connection, close it before writing.
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT session_key, driver_number, qualifying_delta_to_pole, "
                "practice_pace_fp2, practice_pace_fp3, circuit_win_rate, "
                "championship_position, grid_position, driver_seasons_in_f1 "
                "FROM features_prerace WHERE session_key = :sk"
            ),
            conn,
            params={"sk": session_key},
        )

    if df.empty:
        logger.warning(
            "compute_and_store_prerace: no features_prerace rows for session %d — skipping",
            session_key,
        )
        return 0

    # Reindex to the exact training-time column set/order (Pitfall 5) before
    # handing off to predict_with_softmax, which itself selects feature_cols
    # by name — this reindex is a defensive, explicit contract check.
    df = df[["session_key", "driver_number", *PRERACE_FEATURE_COLS]]

    result = predict_with_softmax(model, df, PRERACE_FEATURE_COLS)

    computed_at = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "session_key": session_key,
            "driver_number": int(row["driver_number"]),
            "win_probability": float(row["win_prob"]),
            "prediction_type": "prerace",
            "lap_number": None,
            "computed_at": computed_at,
        }
        for _, row in result.iterrows()
    ]

    # WR-02: fresh connection for the bulk insert (outer read conn is closed).
    inserted = bulk_insert_rows(engine, "predictions", rows)
    logger.info(
        "compute_and_store_prerace: inserted %d prerace rows for session %d",
        inserted, session_key,
    )
    return inserted
