"""
Feature loaders for the ML training pipeline (Plans 02-01 and 02-02).

Loads features_prerace and features_inrace tables joined with raw_sessions to
provide the year column needed for the temporal train/test split.

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

# PRERACE_TARGET: source column for the training label (finish_position, integer 1-20).
# The pipeline binarizes it as: label = (finish_position == 1).astype(int).
# Do NOT use this column directly as a binary label.
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


# ---------------------------------------------------------------------------
# In-race feature column constants — inference contract for Phase 3 (Plan 02-02)
# ---------------------------------------------------------------------------

# INRACE_FEATURE_COLS: ordered list of feature columns fed to the in-race model.
# Order is the Phase 3 inference contract — Phase 3 must transform in this exact order.
# Do NOT include finish_position (target), session_key, driver_number, or year
# (RESEARCH Pitfall 6 — target leakage). finish_position is joined from features_prerace.
INRACE_FEATURE_COLS: list[str] = [
    "lap_number",
    "gap_to_leader",
    "tyre_compound",
    "tyre_age_laps",
    "pit_stop_count",
    "position",
    "interval_ahead",
    "interval_behind",
    "pit_stop_delta",
    "laps_remaining",
]

# INRACE_CAT_COLS: string categorical columns passed through OrdinalEncoder.
# Only tyre_compound for in-race model (D-06; tyre_compound is the only string
# categorical in features_inrace).
INRACE_CAT_COLS: list[str] = ["tyre_compound"]

# INRACE_TARGET: source column for the training label (finish_position, integer 1-20).
# The pipeline binarizes it as: label = (finish_position == 1).astype(int).
# Do NOT use this column directly as a binary label.
# NOTE: finish_position is NOT a column on features_inrace — it must be joined
# from features_prerace on (session_key, driver_number) per RESEARCH Open Question Q1.
INRACE_TARGET: str = "finish_position"


def load_inrace_df(conn) -> pd.DataFrame:
    """
    Load all in-race feature rows joined with finish_position (label) and year.

    Performs an INNER JOIN with features_prerace to retrieve finish_position as
    the training target. Drivers without a finish_position in features_prerace
    are excluded by the INNER JOIN — this handles retirements and debut drivers
    cleanly (RESEARCH Open Question Q1 resolution, T-02.2-04).

    Also joins raw_sessions to provide the year column for temporal split.

    Returns a DataFrame with INRACE_FEATURE_COLS + [session_key, driver_number,
    finish_position, year]. The caller performs the temporal split
    (year <= 2024 for train, year == 2025 for test).

    Only Race sessions are returned (WHERE rs.session_type = 'Race').

    Security: query uses text() with no f-string interpolation (T-03-05, T-02.2-02).

    Args:
        conn: An active SQLAlchemy connection from engine.connect().

    Returns:
        pd.DataFrame with rows from features_inrace joined to features_prerace
        and raw_sessions.year.
    """
    query = text(
        """
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
            fp.finish_position,
            rs.year
        FROM features_inrace fi
        JOIN features_prerace fp
            ON fi.session_key = fp.session_key
           AND fi.driver_number = fp.driver_number
        JOIN raw_sessions rs
            ON fi.session_key = rs.session_key
        WHERE rs.session_type = 'Race'
          AND fp.finish_position IS NOT NULL
        """
    )
    df = pd.read_sql(query, conn)
    logger.debug("Loaded %d in-race feature rows from features_inrace (with prerace label join)", len(df))
    return df
