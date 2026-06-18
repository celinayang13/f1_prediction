"""
Pre-race feature loader for the ML training pipeline (Plan 02-01).

Loads the features_prerace table joined with raw_sessions to provide
the year column needed for the temporal train/test split.

Security: All SQL uses text() with bound :param names — no f-string SQL (T-03-05).
"""
from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature column constants — inference contract for Phase 3
# ---------------------------------------------------------------------------

# PRERACE_FEATURE_COLS: ordered list of feature columns fed to the model.
# Order is the inference contract — Phase 3 must transform in this exact order.
# Do NOT include finish_position (target), session_key, driver_number, or year
# (RESEARCH Pitfall 6 — target leakage).
PRERACE_FEATURE_COLS: list[str] = [
    "qualifying_delta_to_pole",
    "practice_pace_fp2",
    "practice_pace_fp3",
    "circuit_win_rate",
    "circuit_avg_finish",
    "constructor_pace_delta",
    "championship_position",
    "grid_position",
    "driver_seasons_in_f1",
    "team_name",
]

# PRERACE_CAT_COLS: string categorical columns passed through OrdinalEncoder.
# Only team_name for pre-race model (D-06).
PRERACE_CAT_COLS: list[str] = ["team_name"]

# PRERACE_TARGET: binary label column — 1 if driver won the race, 0 otherwise.
PRERACE_TARGET: str = "finish_position"


def load_prerace_df(conn) -> pd.DataFrame:
    """
    Load all pre-race feature rows joined with session year from raw_sessions.

    Returns a DataFrame with PRERACE_FEATURE_COLS + [session_key, driver_number,
    finish_position, year]. The caller performs the temporal split
    (year <= 2024 for train, year == 2025 for test).

    Only Race sessions with non-NULL finish_position are returned — qualifying
    or practice sessions are excluded by the WHERE clause.

    Security: query uses text() with bound :param names (T-03-05).

    Args:
        conn: An active SQLAlchemy connection from engine.connect().

    Returns:
        pd.DataFrame with rows from features_prerace joined to raw_sessions.year.
    """
    query = text(
        """
        SELECT fp.*, rs.year
        FROM features_prerace fp
        JOIN raw_sessions rs ON fp.session_key = rs.session_key
        WHERE rs.session_type = 'Race'
          AND fp.finish_position IS NOT NULL
        """
    )
    df = pd.read_sql(query, conn)
    logger.debug("Loaded %d pre-race feature rows from features_prerace", len(df))
    return df
