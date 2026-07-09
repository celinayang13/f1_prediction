"""
In-race win probability inference (D-09, INRACE-02 contract reused).

Rebuild-then-slice (Pattern 1): calls build_features_for_session(kind="inrace")
to fully rebuild features_inrace for the session from raw_* tables, then
slices to the latest lap (MAX(lap_number)) and computes softmax-normalized
win probabilities for that lap only.

Do NOT hand-roll a separate incremental per-lap feature path — the
gap_to_leader/interval join in features/inrace.py had a real prior bug
(01-REVIEW-FIX CR-03); a second incremental implementation would risk
re-introducing similar bugs and drift from the training-time feature
computation.

Reuses inference/features.py's load_model/predict_inrace_with_softmax
verbatim (Pitfall 4/5) — softmax is applied per (session_key, lap_number)
so probabilities sum to ~1.0 within each lap (INRACE-02).

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
from f1_prediction.features.builder import build_features_for_session
from f1_prediction.inference.features import (
    INRACE_NUMERIC_FEATURE_COLS,
    load_model,
    predict_inrace_with_softmax,
)

logger = logging.getLogger(__name__)

__all__ = ["compute_and_store_inrace_latest_lap"]

DEFAULT_MODEL_PATH = "data/models/inrace_model.joblib"


def compute_and_store_inrace_latest_lap(
    session_key: int,
    model_path: "str | Path" = DEFAULT_MODEL_PATH,
    *,
    engine: Optional[Engine] = None,
) -> int:
    """
    Rebuild in-race features for a session, slice to the latest lap, and
    insert softmax-normalized win probabilities into predictions.

    Args:
        session_key: Race session_key to compute in-race predictions for.
        model_path: Path to the trained inrace_model.joblib artifact.
        engine: SQLAlchemy Engine (defaults to get_engine()).

    Returns:
        Number of prediction rows inserted. Returns 0 (and logs) if no
        features_inrace rows exist yet for the session after rebuild.
    """
    if engine is None:
        engine = get_engine()

    # Pattern 1: full rebuild from raw_* tables — never hand-roll an
    # incremental per-lap join (01-REVIEW-FIX CR-03).
    build_features_for_session(session_key, engine=engine, kind="inrace")

    model = load_model(Path(model_path))

    # WR-02: read features in one connection, close it before writing.
    with engine.connect() as conn:
        max_lap_row = conn.execute(
            text(
                "SELECT MAX(lap_number) FROM features_inrace WHERE session_key = :sk"
            ),
            {"sk": session_key},
        ).fetchone()
        max_lap = max_lap_row[0] if max_lap_row else None

        if max_lap is None:
            logger.warning(
                "compute_and_store_inrace_latest_lap: no features_inrace rows for "
                "session %d after rebuild — skipping",
                session_key,
            )
            return 0

        df = pd.read_sql(
            text(
                "SELECT session_key, driver_number, lap_number, gap_to_leader, "
                "tyre_age_laps, pit_stop_count, position, interval_ahead, "
                "interval_behind, pit_stop_delta, laps_remaining, tyre_compound "
                "FROM features_inrace "
                "WHERE session_key = :sk AND lap_number = :lap"
            ),
            conn,
            params={"sk": session_key, "lap": int(max_lap)},
        )

    if df.empty:
        logger.warning(
            "compute_and_store_inrace_latest_lap: no features_inrace rows for "
            "session %d at lap %s — skipping",
            session_key, max_lap,
        )
        return 0

    # Reindex to the exact training-time column set/order (Pitfall 5) before
    # handing off to predict_inrace_with_softmax, which builds the feature
    # matrix via build_inrace_feature_matrix (9 numeric + 5 tyre one-hot).
    # NOTE: lap_number is one of the 9 INRACE_NUMERIC_FEATURE_COLS already —
    # it must not be listed a second time here, or selecting it by name
    # yields a duplicate column and a 15-wide (not 14-wide) feature matrix.
    df = df[["session_key", "driver_number", *INRACE_NUMERIC_FEATURE_COLS, "tyre_compound"]]

    result = predict_inrace_with_softmax(model, df)

    computed_at = datetime.now(timezone.utc).isoformat()
    lap_number = int(max_lap)
    rows = [
        {
            "session_key": session_key,
            "driver_number": int(row["driver_number"]),
            "win_probability": float(row["win_prob"]),
            "prediction_type": "inrace",
            "lap_number": lap_number,
            "computed_at": computed_at,
        }
        for _, row in result.iterrows()
    ]

    # Idempotent per-lap re-runs: delete any existing 'inrace' rows for this
    # (session_key, lap_number) before insert (T-03-07).
    #
    # WR-02: delete + insert run in the SAME transaction/connection so the
    # swap is atomic — dashboard readers (which select MAX(lap_number) over
    # predictions) never observe a window with the previous lap's stale rows
    # deleted but the new lap's rows not yet inserted.
    rows_df = pd.DataFrame(rows)
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM predictions "
                "WHERE session_key = :sk AND prediction_type = 'inrace' "
                "AND lap_number = :lap"
            ),
            {"sk": session_key, "lap": lap_number},
        )
        rows_df.to_sql(
            "predictions",
            conn,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=500,
        )

    inserted = len(rows)
    logger.info(
        "compute_and_store_inrace_latest_lap: inserted %d inrace rows for "
        "session %d lap %d",
        inserted, session_key, lap_number,
    )
    return inserted
