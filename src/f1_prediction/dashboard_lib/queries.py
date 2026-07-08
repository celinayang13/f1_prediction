"""
Read-only dashboard queries against predictions/raw_drivers/raw_sessions.

Dashboard is read-only against SQLite — never writes to the DB (D-06, T-03-03).
A fresh connection is opened per call (Pattern 4) so SQLite WAL readers never
serve a stale snapshot across Streamlit fragment reruns; only the Engine
object itself (a connection pool, not query results) is cached via
st.cache_resource.

Security: all SQL uses text() with bound :param names — no f-string SQL (T-03-05).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Engine

from f1_prediction.db.engine import get_engine
from f1_prediction.pipeline import get_latest_race_session_key as _get_latest_race_session_key

__all__ = [
    "get_latest_race_session_key",
    "load_prerace_predictions",
    "get_race_display_name",
]

# Neutral gray fallback when a driver has no raw_drivers match (no OpenF1
# drivers data for this session, e.g. a 2022 FastF1-sourced session).
_FALLBACK_TEAM_COLOUR = "888888"


@st.cache_resource
def _engine() -> Engine:
    """Cache the Engine (connection pool) — never cache query results here."""
    return get_engine()


def get_latest_race_session_key() -> "int | None":
    """Resolve the latest Race-type session_key (reuses pipeline.py's D-01 query)."""
    return _get_latest_race_session_key(_engine())


def load_prerace_predictions(session_key: int) -> pd.DataFrame:
    """
    Load 'prerace' predictions for a session, joined to raw_drivers for
    display metadata, ordered by win_probability DESC.

    Uses a LEFT JOIN so predictions still render even if raw_drivers has no
    matching rows (e.g. a 2022 session with no OpenF1 drivers data) — falls
    back to driver_number as the label and a neutral gray bar color.

    Args:
        session_key: Race session_key to load prerace predictions for.

    Returns:
        DataFrame with columns: driver_number, win_probability, full_name,
        name_acronym, team_colour, computed_at. Empty DataFrame if no
        prerace predictions exist yet.
    """
    with _engine().connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT p.driver_number, p.win_probability, p.computed_at, "
                "d.full_name, d.name_acronym, d.team_colour "
                "FROM predictions p "
                "LEFT JOIN raw_drivers d "
                "  ON p.session_key = d.session_key "
                " AND p.driver_number = d.driver_number "
                "WHERE p.session_key = :sk AND p.prediction_type = 'prerace' "
                "ORDER BY p.win_probability DESC"
            ),
            conn,
            params={"sk": session_key},
        )

    if df.empty:
        return df

    df["full_name"] = df["full_name"].fillna(df["driver_number"].astype(str))
    df["name_acronym"] = df["name_acronym"].fillna(df["driver_number"].astype(str))
    df["team_colour"] = df["team_colour"].fillna(_FALLBACK_TEAM_COLOUR)
    return df


def get_race_display_name(session_key: int) -> Optional[str]:
    """
    Build a human-readable race name from raw_sessions metadata.

    Args:
        session_key: Race session_key to look up.

    Returns:
        A display string like "Country Name — Circuit Short Name", or None
        if the session_key does not exist.
    """
    with _engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT country_name, circuit_short_name, session_name "
                "FROM raw_sessions WHERE session_key = :sk"
            ),
            {"sk": session_key},
        ).fetchone()

    if row is None:
        return None

    country_name, circuit_short_name, _session_name = row
    parts = [p for p in (country_name, circuit_short_name) if p]
    return " — ".join(parts) if parts else f"Session {session_key}"
