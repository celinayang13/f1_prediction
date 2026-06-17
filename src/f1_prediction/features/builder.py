"""
Feature builder — orchestrates pre-race and in-race feature computation.

Reads from all raw_* tables and writes to features_prerace / features_inrace.

Key design decisions:
  - Sessions are processed in chronological (year, round_number) order so
    circuit_win_rate / circuit_avg_finish / constructor_pace_delta can read
    prior years' features_prerace rows (D-04).
  - Each call DELETEs existing rows for the session then re-INSERTs (clean
    re-run support per D-13).
  - No f-string SQL — all queries use text() with bound :param names (T-03-05).

Exported functions:
  build_features_for_session(session_key, *, engine, kind) -> int
  build_all_features(*, engine=None, only_years=None) -> dict
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from f1_prediction.db.engine import get_engine
from f1_prediction.features.inrace import compute_inrace_features_for_session
from f1_prediction.features.prerace import (
    compute_championship_position,
    compute_circuit_avg_finish,
    compute_circuit_win_rate,
    compute_constructor_pace_delta,
    compute_driver_seasons,
    compute_finish_position,
    compute_grid_position,
    compute_practice_pace,
    compute_qualifying_delta,
)

logger = logging.getLogger(__name__)

__all__ = ["build_features_for_session", "build_all_features"]


def _get_race_sessions(engine: Engine, only_years: Optional[list] = None) -> list[dict]:
    """
    Return all Race sessions ordered chronologically (year, round_number).

    Args:
        engine: SQLAlchemy engine.
        only_years: If provided, restrict to these years.

    Returns:
        List of dicts with keys: session_key, year, round_number,
        circuit_short_name.
    """
    with engine.connect() as conn:
        # Fetch all Race sessions, filter by year in Python to avoid f-string SQL
        q = text(
            "SELECT session_key, year, round_number, circuit_short_name "
            "FROM raw_sessions WHERE session_type = 'Race' "
            "ORDER BY year, round_number"
        )
        rows = conn.execute(q).fetchall()

    sessions = [
        {
            "session_key": int(r[0]),
            "year": int(r[1]),
            "round_number": int(r[2]),
            "circuit_short_name": r[3],
        }
        for r in rows
    ]

    if only_years is not None:
        year_set = set(only_years)
        sessions = [s for s in sessions if s["year"] in year_set]

    return sessions


def _get_drivers_for_session(session_key: int, conn) -> list[dict]:
    """
    Return driver_number and team_name for all drivers in a race session.

    Team name is sourced from raw_laps via tyre_compound_observed first, but
    that field doesn't have team info. Instead, we look up team_name from the
    normalizer-produced data.

    For FastF1 data: team name is not stored in raw_laps or raw_positions.
    We join raw_laps → (driver already in session) and try to find team from
    existing features_prerace rows for prior races, falling back to None.

    A simpler approach: read distinct driver_numbers from raw_laps for this
    session and look up team_name from the most recent prior features_prerace
    row for each driver (which had it stored). For the very first race we
    have no prior data → team_name = None.

    Note: A dedicated raw_race_results table would provide this cleanly.
    For Phase 1, this approximation is documented.
    """
    q = text(
        "SELECT DISTINCT driver_number FROM raw_laps WHERE session_key = :sk"
    )
    rows = conn.execute(q, {"sk": session_key}).fetchall()
    driver_numbers = [int(r[0]) for r in rows]

    # Try to look up team_name from the most recent prior features_prerace
    # row for each driver (any circuit, any year)
    result = []
    for dn in driver_numbers:
        team_q = text(
            """
            SELECT fp.team_name
            FROM features_prerace fp
            JOIN raw_sessions rs ON fp.session_key = rs.session_key
            WHERE fp.driver_number = :dn
              AND rs.session_key != :sk
            ORDER BY rs.year DESC, rs.round_number DESC
            LIMIT 1
            """
        )
        team_row = conn.execute(team_q, {"dn": dn, "sk": session_key}).fetchone()
        team_name = team_row[0] if team_row else None
        result.append({"driver_number": dn, "team_name": team_name})

    return result


def _get_team_name_from_session(session_key: int, driver_number: int, conn) -> Optional[str]:
    """
    Try to get team name from raw data for this session.

    Checks raw_laps first (FastF1 doesn't store team in laps).
    Then falls back to prior features_prerace rows.
    Returns None if not found.
    """
    # Check if raw_sessions has team info (not stored in current schema)
    # Fall back to most recent prior features_prerace row for this driver
    q = text(
        """
        SELECT fp.team_name
        FROM features_prerace fp
        JOIN raw_sessions rs ON fp.session_key = rs.session_key
        WHERE fp.driver_number = :dn AND fp.team_name IS NOT NULL
        ORDER BY rs.year DESC, rs.round_number DESC
        LIMIT 1
        """
    )
    row = conn.execute(q, {"dn": driver_number}).fetchone()
    return row[0] if row else None


def build_features_for_session(
    session_key: int, *, engine: Optional[Engine] = None, kind: str
) -> int:
    """
    Build feature rows for a single race session.

    Validates that the session is a Race session (non-race sessions have no
    prerace or inrace features). DELETEs any existing rows for this
    session_key from the target table, then bulk-inserts the newly computed
    rows.

    Args:
        session_key: Race session_key.
        engine: SQLAlchemy engine (defaults to get_engine()).
        kind: 'prerace' or 'inrace'.

    Returns:
        Number of rows inserted. Returns 0 if the session is not found or
        is not a Race session.

    Raises:
        ValueError: If kind is not 'prerace' or 'inrace'.
    """
    if kind not in ("prerace", "inrace"):
        raise ValueError(f"kind must be 'prerace' or 'inrace', got {kind!r}")

    if engine is None:
        engine = get_engine()

    table_name = "features_prerace" if kind == "prerace" else "features_inrace"

    with engine.connect() as conn:
        # Validate session type
        meta_q = text(
            "SELECT session_type, year, round_number, circuit_short_name "
            "FROM raw_sessions WHERE session_key = :sk"
        )
        meta = conn.execute(meta_q, {"sk": session_key}).fetchone()
        if meta is None:
            logger.warning("build_features_for_session: session %d not found", session_key)
            return 0
        session_type, year, round_number, circuit_short_name = meta[0], meta[1], meta[2], meta[3]
        if session_type != "Race":
            logger.info(
                "build_features_for_session: session %d is %s (not Race) — skipping",
                session_key, session_type,
            )
            return 0

        # DELETE existing rows (clean re-run support D-13)
        # Use two explicit queries to avoid f-string SQL (T-03-05).
        # table_name is hardcoded from kind parameter (not user input), but
        # we still avoid f-string SQL for consistent policy compliance.
        if kind == "prerace":
            conn.execute(
                text("DELETE FROM features_prerace WHERE session_key = :sk"),
                {"sk": session_key},
            )
        else:
            conn.execute(
                text("DELETE FROM features_inrace WHERE session_key = :sk"),
                {"sk": session_key},
            )
        conn.commit()

        if kind == "inrace":
            df = compute_inrace_features_for_session(session_key, conn)
            if df.empty:
                logger.info(
                    "build_features_for_session: no inrace data for session %d", session_key
                )
                return 0
            # Convert nullable Int64 to Python int/None for SQLite insert
            rows = df.to_dict("records")
            for row in rows:
                for col in ["tyre_age_laps", "position", "laps_remaining"]:
                    v = row.get(col)
                    if pd.isna(v) if v is not None else False:
                        row[col] = None
                    elif v is not None:
                        row[col] = int(v)

        else:
            # kind == "prerace"
            # Get all drivers for this race session
            driver_rows = conn.execute(
                text("SELECT DISTINCT driver_number FROM raw_laps WHERE session_key = :sk"),
                {"sk": session_key},
            ).fetchall()
            driver_numbers = [int(r[0]) for r in driver_rows]

            if not driver_numbers:
                logger.info(
                    "build_features_for_session: no drivers found for session %d", session_key
                )
                return 0

            # Compute qualifying deltas for all drivers (vectorized)
            q_delta_df = compute_qualifying_delta(session_key, conn)
            q_delta_map = (
                dict(zip(q_delta_df["driver_number"], q_delta_df["qualifying_delta_to_pole"]))
                if not q_delta_df.empty
                else {}
            )

            # Compute practice paces (vectorized)
            fp2_df = compute_practice_pace(session_key, conn, fp_session_type="Practice 2")
            fp2_map = (
                dict(zip(fp2_df["driver_number"], fp2_df["practice_pace"]))
                if not fp2_df.empty
                else {}
            )
            fp3_df = compute_practice_pace(session_key, conn, fp_session_type="Practice 3")
            fp3_map = (
                dict(zip(fp3_df["driver_number"], fp3_df["practice_pace"]))
                if not fp3_df.empty
                else {}
            )

            rows = []
            for dn in driver_numbers:
                # Team name: look up from prior features_prerace or try raw data
                team_name = _get_team_name_from_session(session_key, dn, conn)

                row = {
                    "session_key": session_key,
                    "driver_number": dn,
                    "qualifying_delta_to_pole": _maybe_float(q_delta_map.get(dn)),
                    "practice_pace_fp2": _maybe_float(fp2_map.get(dn)),
                    "practice_pace_fp3": _maybe_float(fp3_map.get(dn)),
                    "circuit_win_rate": compute_circuit_win_rate(
                        dn, circuit_short_name, int(year), conn
                    ),
                    "circuit_avg_finish": (
                        compute_circuit_avg_finish(team_name, circuit_short_name, int(year), conn)
                        if team_name
                        else None
                    ),
                    "constructor_pace_delta": (
                        compute_constructor_pace_delta(team_name, circuit_short_name, int(year), conn)
                        if team_name
                        else None
                    ),
                    "championship_position": compute_championship_position(
                        dn, int(year), int(round_number), conn
                    ),
                    "grid_position": compute_grid_position(session_key, dn, conn),
                    "driver_seasons_in_f1": compute_driver_seasons(dn, int(year), conn),
                    "finish_position": compute_finish_position(session_key, dn, conn),
                    "team_name": team_name,
                }
                rows.append(row)

        if not rows:
            return 0

        # Bulk insert
        if rows:
            df_insert = pd.DataFrame(rows)
            with engine.connect() as conn2:
                df_insert.to_sql(
                    table_name,
                    conn2,
                    if_exists="append",
                    index=False,
                    method="multi",
                    chunksize=500,
                )
                conn2.commit()

        inserted = len(rows)
        logger.info(
            "build_features_for_session: inserted %d %s rows for session %d",
            inserted, kind, session_key,
        )
        return inserted


def _maybe_float(value) -> Optional[float]:
    """Convert a value to float, returning None for missing/nan values."""
    if value is None:
        return None
    try:
        f = float(value)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def build_all_features(
    *, engine: Optional[Engine] = None, only_years: Optional[list] = None
) -> dict:
    """
    Build all feature rows across all backfilled race sessions.

    Sessions are processed in chronological (year, round_number) order
    so cross-session aggregates (circuit_win_rate, circuit_avg_finish,
    constructor_pace_delta) can read prior years' completed rows.

    Args:
        engine: SQLAlchemy engine (defaults to get_engine()).
        only_years: If provided, restrict to these years.

    Returns:
        Dict with keys:
          prerace_inserted: int — total features_prerace rows inserted
          inrace_inserted:  int — total features_inrace rows inserted
          sessions_processed: int — number of Race sessions processed
    """
    if engine is None:
        engine = get_engine()

    sessions = _get_race_sessions(engine, only_years=only_years)
    logger.info("build_all_features: found %d race sessions to process", len(sessions))

    total_prerace = 0
    total_inrace = 0
    sessions_processed = 0

    for sess in sessions:
        sk = sess["session_key"]
        logger.info(
            "build_all_features: processing session %d (year=%d round=%d)",
            sk, sess["year"], sess["round_number"],
        )
        try:
            pre = build_features_for_session(sk, engine=engine, kind="prerace")
            inrace = build_features_for_session(sk, engine=engine, kind="inrace")
            total_prerace += pre
            total_inrace += inrace
            sessions_processed += 1
        except Exception as exc:
            logger.error(
                "build_all_features: error processing session %d: %s", sk, exc, exc_info=True
            )

    result = {
        "prerace_inserted": total_prerace,
        "inrace_inserted": total_inrace,
        "sessions_processed": sessions_processed,
    }
    logger.info("build_all_features: complete — %s", result)
    return result
