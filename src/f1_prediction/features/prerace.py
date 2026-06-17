"""
Pre-race feature computation functions (D-02).

Each function is a pure function that reads from a SQLAlchemy Connection and
returns a pandas DataFrame or scalar value. Functions are called by the
feature builder (builder.py) in chronological order so cross-session
aggregates (circuit_win_rate, circuit_avg_finish, constructor_pace_delta)
can read prior years' completed features_prerace rows.

Security: All SQL uses text() with bound :param names — no f-string SQL (T-03-05).
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

# FIA 2022+ points scoring: 1st–10th
_FIA_POINTS = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}


def compute_qualifying_delta(session_key: int, conn) -> pd.DataFrame:
    """
    Compute qualifying_delta_to_pole for each driver in the qualifying
    session matching the given race session_key.

    Looks up the Qualifying (or Sprint Shootout fallback) session for the
    same (year, round_number) as the race session, then uses MIN(lap_duration)
    per driver (excluding pit-out laps and NULL lap times) as the driver's
    best qualifying lap.

    qualifying_delta_to_pole = driver_best_lap - pole_time
    Pole sitter has delta = 0.0; all others are positive.

    Sprint Shootout fallback: if no 'Qualifying' session is found for the
    same (year, round_number), fall back to any session of type 'Sprint
    Shootout'. This covers sprint weekends where the grid for the main race
    is set by the Sprint Shootout rather than a conventional qualifying.

    Args:
        session_key: session_key of the Race session.
        conn: SQLAlchemy connection.

    Returns:
        DataFrame with columns [driver_number, qualifying_delta_to_pole].
        Empty DataFrame if no qualifying session exists for this race.

    Security: parameterized queries only — no f-string SQL (T-03-05).
    """
    # Get year and round_number for this race session
    race_meta_q = text(
        "SELECT year, round_number FROM raw_sessions WHERE session_key = :sk"
    )
    meta = conn.execute(race_meta_q, {"sk": session_key}).fetchone()
    if meta is None:
        logger.warning("compute_qualifying_delta: no session found for session_key=%d", session_key)
        return pd.DataFrame(columns=["driver_number", "qualifying_delta_to_pole"])

    year, round_number = meta[0], meta[1]

    # Try 'Qualifying' first, then 'Sprint Shootout' fallback
    q_key_q = text(
        """
        SELECT session_key FROM raw_sessions
        WHERE year = :year AND round_number = :rn AND session_type = :stype
        LIMIT 1
        """
    )
    q_row = conn.execute(q_key_q, {"year": year, "rn": round_number, "stype": "Qualifying"}).fetchone()
    if q_row is None:
        # Sprint Shootout fallback
        q_row = conn.execute(q_key_q, {"year": year, "rn": round_number, "stype": "Sprint Shootout"}).fetchone()

    if q_row is None:
        logger.info(
            "compute_qualifying_delta: no Qualifying/Sprint Shootout session for year=%d round=%d",
            year, round_number,
        )
        return pd.DataFrame(columns=["driver_number", "qualifying_delta_to_pole"])

    q_session_key = q_row[0]

    # Best lap per driver in the Q session (per RESEARCH Pattern 7)
    best_laps_q = text(
        """
        SELECT driver_number, MIN(lap_duration) AS best_lap
        FROM raw_laps
        WHERE session_key = :qsk
          AND (is_pit_out_lap = 0 OR is_pit_out_lap IS NULL)
          AND lap_duration IS NOT NULL
        GROUP BY driver_number
        """
    )
    df = pd.read_sql(best_laps_q, conn, params={"qsk": q_session_key})
    if df.empty:
        return pd.DataFrame(columns=["driver_number", "qualifying_delta_to_pole"])

    pole_time = df["best_lap"].min()
    df["qualifying_delta_to_pole"] = df["best_lap"] - pole_time
    return df[["driver_number", "qualifying_delta_to_pole"]]


def compute_practice_pace(session_key: int, conn, *, fp_session_type: str) -> pd.DataFrame:
    """
    Compute practice_pace for each driver in a FP session.

    fp_session_type is 'Practice 2' or 'Practice 3'. Looks up the
    matching FP session for the same (year, round_number) as the race.

    Per D-08: filtering happens here, not at ingest. Clean-lap filter:
    - Exclude pit-out laps (is_pit_out_lap = 1)
    - Apply 107% rule: exclude laps > 107% of session median (per RESEARCH Code Examples)

    practice_pace = driver_median - field_median
    Negative = faster than field median; positive = slower.

    Args:
        session_key: session_key of the Race session.
        conn: SQLAlchemy connection.
        fp_session_type: 'Practice 2' or 'Practice 3'.

    Returns:
        DataFrame with columns [driver_number, practice_pace].
        Empty DataFrame if the FP session is not found.
    """
    race_meta_q = text(
        "SELECT year, round_number FROM raw_sessions WHERE session_key = :sk"
    )
    meta = conn.execute(race_meta_q, {"sk": session_key}).fetchone()
    if meta is None:
        return pd.DataFrame(columns=["driver_number", "practice_pace"])

    year, round_number = meta[0], meta[1]

    fp_key_q = text(
        """
        SELECT session_key FROM raw_sessions
        WHERE year = :year AND round_number = :rn AND session_type = :stype
        LIMIT 1
        """
    )
    fp_row = conn.execute(fp_key_q, {"year": year, "rn": round_number, "stype": fp_session_type}).fetchone()
    if fp_row is None:
        logger.info(
            "compute_practice_pace: no %s session for year=%d round=%d",
            fp_session_type, year, round_number,
        )
        return pd.DataFrame(columns=["driver_number", "practice_pace"])

    fp_session_key = fp_row[0]

    laps_q = text(
        """
        SELECT driver_number, lap_duration
        FROM raw_laps
        WHERE session_key = :fpsk
          AND (is_pit_out_lap = 0 OR is_pit_out_lap IS NULL)
          AND lap_duration IS NOT NULL
        """
    )
    df = pd.read_sql(laps_q, conn, params={"fpsk": fp_session_key})
    if df.empty:
        return pd.DataFrame(columns=["driver_number", "practice_pace"])

    # 107% rule (D-08, RESEARCH Code Examples — Practice Pace)
    field_median = df["lap_duration"].median()
    df = df[df["lap_duration"] <= field_median * 1.07]
    if df.empty:
        return pd.DataFrame(columns=["driver_number", "practice_pace"])

    # Recalculate field median after filtering
    field_median = df["lap_duration"].median()
    driver_medians = df.groupby("driver_number")["lap_duration"].median()
    result = (driver_medians - field_median).rename("practice_pace").reset_index()
    return result[["driver_number", "practice_pace"]]


def compute_circuit_win_rate(
    driver_number: int, circuit_short_name: str, year: int, conn
) -> Optional[float]:
    """
    Compute the driver's historical win rate at this circuit.

    Reads from features_prerace (finish_position == 1 for wins). Only
    considers races before the current year to avoid data leakage.

    Chronological ordering requirement: build_all_features processes
    sessions in (year, round_number) order so prior years' finish_position
    rows are already populated when this function is called.

    Args:
        driver_number: Driver number.
        circuit_short_name: Circuit short name (e.g., 'Bahrain').
        year: Current race year (only data from years < year is used).
        conn: SQLAlchemy connection.

    Returns:
        wins / starts ratio, or None if the driver has no prior starts at
        this circuit (e.g., circuit debut).
    """
    q = text(
        """
        SELECT
            COUNT(*) AS starts,
            SUM(CASE WHEN fp.finish_position = 1 THEN 1 ELSE 0 END) AS wins
        FROM features_prerace fp
        JOIN raw_sessions rs ON fp.session_key = rs.session_key
        WHERE fp.driver_number = :dn
          AND rs.circuit_short_name = :csn
          AND rs.year < :year
          AND fp.finish_position IS NOT NULL
        """
    )
    row = conn.execute(q, {"dn": driver_number, "csn": circuit_short_name, "year": year}).fetchone()
    if row is None or row[0] == 0:
        return None
    starts, wins = row[0], row[1]
    return float(wins) / float(starts) if starts > 0 else None


def compute_circuit_avg_finish(
    team_name: str, circuit_short_name: str, year: int, conn
) -> Optional[float]:
    """
    Compute the constructor's average finishing position at this circuit
    over the last 2 seasons (years year-2 and year-1).

    Args:
        team_name: Constructor name as stored in features_prerace.team_name.
        circuit_short_name: Circuit short name.
        year: Current race year.
        conn: SQLAlchemy connection.

    Returns:
        Average finish_position over the last 2 seasons, or None if no data.
    """
    q = text(
        """
        SELECT AVG(fp.finish_position) AS avg_finish
        FROM features_prerace fp
        JOIN raw_sessions rs ON fp.session_key = rs.session_key
        WHERE fp.team_name = :team
          AND rs.circuit_short_name = :csn
          AND rs.year >= :year_start
          AND rs.year < :year
          AND fp.finish_position IS NOT NULL
        """
    )
    row = conn.execute(
        q,
        {
            "team": team_name,
            "csn": circuit_short_name,
            "year_start": year - 2,
            "year": year,
        },
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def compute_constructor_pace_delta(
    team_name: str, circuit_short_name: str, year: int, conn
) -> Optional[float]:
    """
    Compute the constructor's practice pace delta at this circuit over the
    last 2 seasons (average of practice_pace_fp2 and practice_pace_fp3).

    Stored as delta vs field median (negative = faster). Reads from
    features_prerace of prior years' races at this circuit.

    Args:
        team_name: Constructor name.
        circuit_short_name: Circuit short name.
        year: Current race year.
        conn: SQLAlchemy connection.

    Returns:
        Average pace delta over last 2 seasons, or None if no data.
    """
    q = text(
        """
        SELECT AVG(sub.combined_pace) AS avg_delta
        FROM (
            -- WR-03: use conditional average to avoid COALESCE(fp2, 0) substituting 0
        -- for a missing session, which would bias the average toward zero.
        SELECT CASE
            WHEN fp.practice_pace_fp2 IS NOT NULL AND fp.practice_pace_fp3 IS NOT NULL
                 THEN (fp.practice_pace_fp2 + fp.practice_pace_fp3) / 2.0
            WHEN fp.practice_pace_fp2 IS NOT NULL THEN fp.practice_pace_fp2
            ELSE fp.practice_pace_fp3
        END AS combined_pace
            FROM features_prerace fp
            JOIN raw_sessions rs ON fp.session_key = rs.session_key
            WHERE fp.team_name = :team
              AND rs.circuit_short_name = :csn
              AND rs.year >= :year_start
              AND rs.year < :year
              AND (fp.practice_pace_fp2 IS NOT NULL OR fp.practice_pace_fp3 IS NOT NULL)
        ) sub
        WHERE sub.combined_pace IS NOT NULL
        """
    )
    row = conn.execute(
        q,
        {
            "team": team_name,
            "csn": circuit_short_name,
            "year_start": year - 2,
            "year": year,
        },
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def compute_championship_position(
    driver_number: int, year: int, round_number: int, conn
) -> Optional[int]:
    """
    Compute the driver's championship standings position before this race.

    Uses cumulative FIA 2022+ points (25-18-15-12-10-8-6-4-2-1 for top 10)
    derived from finish_position in features_prerace for all races in this year
    up to round_number - 1. Ranks drivers by total points descending.

    Open Question 1 resolution: championship_position is computed from
    finish_position-to-points mapping in features_prerace rather than storing
    a separate points column in raw_sessions. This keeps the raw tables thin.

    Args:
        driver_number: Driver number.
        year: Season year.
        round_number: Current round number.
        conn: SQLAlchemy connection.

    Returns:
        Standings rank (1 = championship leader), or None if no prior races.
    """
    # Get all race sessions in this year up to round_number - 1
    q = text(
        """
        SELECT fp.driver_number, fp.finish_position
        FROM features_prerace fp
        JOIN raw_sessions rs ON fp.session_key = rs.session_key
        WHERE rs.year = :year
          AND rs.round_number < :rn
          AND rs.session_type = 'Race'
          AND fp.finish_position IS NOT NULL
        """
    )
    rows = conn.execute(q, {"year": year, "rn": round_number}).fetchall()
    if not rows:
        return None

    # Accumulate points per driver
    points: dict[int, float] = {}
    for dn, pos in rows:
        pts = _FIA_POINTS.get(int(pos), 0)
        points[int(dn)] = points.get(int(dn), 0.0) + pts

    if driver_number not in points:
        # Driver has no prior races in this season yet
        points[driver_number] = 0.0

    # Rank drivers by points descending (ties → same rank)
    sorted_drivers = sorted(points.keys(), key=lambda d: points[d], reverse=True)
    for rank, dn in enumerate(sorted_drivers, start=1):
        if dn == driver_number:
            return rank

    return None


def compute_grid_position(session_key: int, driver_number: int, conn) -> Optional[int]:
    """
    Approximate the driver's starting grid position for the race.

    Grid position is derived from raw_positions WHERE lap_number = 1,
    taking the first observed position record. This is an approximation —
    the lap-1 position reflects the order at the start of the first racing
    lap, which closely mirrors the starting grid (subject to formation lap
    incidents or immediate overtakes).

    FastF1's session.results.GridPosition would be more accurate but was
    not stored during Plan 02 ingest to avoid schema complexity. This
    approximation is documented here; Phase 4 may add a raw_race_results
    table if grid position accuracy becomes critical.

    Args:
        session_key: Race session_key.
        driver_number: Driver number.
        conn: SQLAlchemy connection.

    Returns:
        Grid position (1-based), or None if no position data.
    """
    q = text(
        """
        SELECT position
        FROM raw_positions
        WHERE session_key = :sk AND driver_number = :dn AND lap_number = 1
        ORDER BY date_recorded ASC
        LIMIT 1
        """
    )
    row = conn.execute(q, {"sk": session_key, "dn": driver_number}).fetchone()
    return int(row[0]) if row is not None else None


def compute_driver_seasons(driver_number: int, year: int, conn) -> int:
    """
    Count distinct seasons in which the driver appears in raw_laps, up to
    and including the given year.

    Per Assumptions Log A2: this approximates F1 career years. If a driver
    has a gap year (sabbatical), the count will be lower than their actual
    career year total.

    Args:
        driver_number: Driver number.
        year: Current season year (inclusive upper bound).
        conn: SQLAlchemy connection.

    Returns:
        Count of distinct seasons (≥ 1 for any driver in the raw data).
    """
    q = text(
        """
        SELECT COUNT(DISTINCT rs.year)
        FROM raw_laps rl
        JOIN raw_sessions rs ON rl.session_key = rs.session_key
        WHERE rl.driver_number = :dn
          AND rs.year <= :year
        """
    )
    row = conn.execute(q, {"dn": driver_number, "year": year}).fetchone()
    return int(row[0]) if (row is not None and row[0] is not None) else 0


def compute_finish_position(session_key: int, driver_number: int, conn) -> Optional[int]:
    """
    Compute the driver's finishing position from the final lap of raw_positions.

    For race sessions: reads the position at MAX(lap_number) for the driver
    in raw_positions. For DNFs (where the driver disappears from later laps),
    the last recorded position is used.

    Args:
        session_key: Race session_key.
        driver_number: Driver number.
        conn: SQLAlchemy connection.

    Returns:
        Finishing position (1-based int), or None if no position data.
    """
    q = text(
        """
        SELECT position
        FROM raw_positions
        WHERE session_key = :sk AND driver_number = :dn
        ORDER BY lap_number DESC
        LIMIT 1
        """
    )
    row = conn.execute(q, {"sk": session_key, "dn": driver_number}).fetchone()
    return int(row[0]) if row is not None else None
