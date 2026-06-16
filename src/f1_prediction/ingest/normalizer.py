"""
FastF1 → OpenF1-schema normalizer.

Converts FastF1 Session objects and Laps DataFrames to the canonical
OpenF1-compatible dict format expected by the raw_sessions and raw_laps
ORM models.

## session_key Synthesis Scheme (FastF1 / 2022 data)

OpenF1 keys for 2023+ are large server-assigned integers (in the hundreds
of thousands by 2024). For FastF1 2022 data, we synthesise a key using:

    session_key = year * 100_000 + round_number * 100 + session_type_code

Where session_type_code is:
    1 = Practice 1    (not ingested per D-06 but included for completeness)
    2 = Practice 2
    3 = Practice 3
    4 = Qualifying
    5 = Sprint Shootout
    6 = Sprint
    7 = Race

Examples:
    2022 Round 1 Race    → 2022 * 100_000 + 1 * 100 + 7 = 202_200_107
    2022 Round 22 Race   → 2022 * 100_000 + 22 * 100 + 7 = 202_202_207

This scheme guarantees:
- Uniqueness across all 2022 sessions (max 22 rounds × 7 session types = 154 keys)
- No collision with OpenF1 keys (OpenF1 2023+ keys are > 1_000_000 server-assigned)
- Stable across re-runs (deterministic from year + round + session type)

Downstream consumers (Plans 02, 03, feature builder) can use this scheme
to join FastF1 and OpenF1 data without key conflicts.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    import fastf1

logger = logging.getLogger(__name__)

# Mapping from FastF1 session identifiers to canonical session_type strings
_SESSION_TYPE_MAP: dict[str, str] = {
    "R": "Race",
    "Q": "Qualifying",
    "FP1": "Practice 1",
    "FP2": "Practice 2",
    "FP3": "Practice 3",
    "S": "Sprint",
    "SS": "Sprint Shootout",
    # FastF1 may also use full names in some cases
    "Race": "Race",
    "Qualifying": "Qualifying",
    "Practice 1": "Practice 1",
    "Practice 2": "Practice 2",
    "Practice 3": "Practice 3",
    "Sprint": "Sprint",
    "Sprint Shootout": "Sprint Shootout",
}

# Mapping from session identifier to numeric code for session_key synthesis
_SESSION_TYPE_CODE: dict[str, int] = {
    "FP1": 1, "Practice 1": 1,
    "FP2": 2, "Practice 2": 2,
    "FP3": 3, "Practice 3": 3,
    "Q": 4, "Qualifying": 4,
    "SS": 5, "Sprint Shootout": 5,
    "S": 6, "Sprint": 6,
    "R": 7, "Race": 7,
}

# FastF1-specific columns to DROP (never stored in raw_laps)
# Compound/TyreLife sourced from raw_stints in Plan 02 (D-04)
_FASTF1_DROP_COLUMNS = frozenset({
    "IsAccurate",
    "IsPersonalBest",
    "FastF1Generated",
    "Deleted",
    "DeletedReason",
    "Compound",
    "TyreLife",
    "FreshTyre",
    "Team",
    "Driver",
    "LapStartTime",
    "Stint",
    "Sector1SessionTime",
    "Sector2SessionTime",
    "Sector3SessionTime",
    "Time",
    "PitInTime",
    "PitOutTime",
    "Position",
    "SpeedFL",
    "TrackStatus",
    "WindDirection",
    "WindSpeed",
    "AirTemp",
    "Rainfall",
    "Pressure",
    "Humidity",
    "TrackTemp",
})


def _synthesize_session_key(year: int, round_number: int, session_name: str) -> int:
    """
    Compute the synthetic session_key for FastF1 (2022) sessions.

    See module docstring for the full scheme description.

    Args:
        year: Season year (e.g. 2022).
        round_number: Round number in the season (1-based).
        session_name: FastF1 session identifier (e.g. 'R', 'Q', 'FP2').

    Returns:
        Integer session key unique within the year.
    """
    code = _SESSION_TYPE_CODE.get(session_name, 0)
    return year * 100_000 + round_number * 100 + code


def normalize_fastf1_session(
    session: "fastf1.core.Session",
    *,
    year: int,
    round_number: int,
) -> dict:
    """
    Convert a loaded FastF1 Session object to a raw_sessions row dict.

    Output keys match RawSession column names exactly.

    Args:
        session: A loaded FastF1 Session object (session.load() must have
                 been called before passing here).
        year: Season year (used for session_key synthesis and storage).
        round_number: Round number in the season (1-based).

    Returns:
        Dict with keys: session_key, year, round_number, country_name,
        circuit_short_name, session_type, session_name, date_start,
        date_end, total_laps, source.
    """
    raw_name = session.name  # e.g. 'R', 'Q', 'FP2'
    session_type = _SESSION_TYPE_MAP.get(raw_name, raw_name)
    session_key = _synthesize_session_key(year, round_number, raw_name)

    # date_start: ISO 8601 string from session.date (may be NaT)
    date_start: str | None = None
    try:
        if session.date is not None and not pd.isnull(session.date):
            date_start = session.date.isoformat()
    except (TypeError, ValueError):
        pass

    # country_name and circuit_short_name from session event metadata
    try:
        country_name: str | None = str(session.event.get("Country", None))
        if country_name == "None":
            country_name = None
    except (AttributeError, TypeError):
        country_name = None

    try:
        circuit_short_name: str | None = str(session.event.get("Location", None))
        if circuit_short_name == "None":
            circuit_short_name = None
    except (AttributeError, TypeError):
        circuit_short_name = None

    return {
        "session_key": session_key,
        "year": year,
        "round_number": round_number,
        "country_name": country_name,
        "circuit_short_name": circuit_short_name,
        "session_type": session_type,
        "session_name": raw_name,
        "date_start": date_start,
        "date_end": None,       # populated in Plan 02 from session results
        "total_laps": None,     # populated in Plan 02 from MAX(lap_number) or event metadata
        "source": "fastf1",
    }


def normalize_fastf1_laps(
    laps_df: pd.DataFrame,
    *,
    session_key: int,
) -> list[dict]:
    """
    Convert a FastF1 session.laps DataFrame to a list of raw_laps row dicts.

    Output keys match RawLap column names exactly. FastF1-specific columns
    are dropped. All timedeltas are converted to float seconds. NaN values
    are converted to Python None so SQLite accepts them.

    Security (T-01-01): columns are explicitly enumerated — unexpected FastF1
    columns are ignored, not blindly serialized. Type casts (int/float) raise
    on malformed values rather than silently poisoning the database.

    Args:
        laps_df: FastF1 session.laps DataFrame.
        session_key: The synthesized (or OpenF1) session key to attach to
                     each lap row.

    Returns:
        List of dicts, each representing one raw_laps row.
    """
    if laps_df is None or len(laps_df) == 0:
        logger.warning("normalize_fastf1_laps received empty/None DataFrame")
        return []

    df = laps_df.copy()

    # --- Convert timedelta columns to float seconds (or None if NaT) ---
    def _timedelta_to_seconds(series: pd.Series) -> pd.Series:
        """Convert pd.Timedelta series to float seconds; NaT → None."""
        return series.apply(
            lambda v: float(v.total_seconds()) if pd.notna(v) and hasattr(v, "total_seconds") else None
        )

    lap_duration = _timedelta_to_seconds(df.get("LapTime", pd.Series([None] * len(df), dtype=object)))
    sector_1 = _timedelta_to_seconds(df.get("Sector1Time", pd.Series([None] * len(df), dtype=object)))
    sector_2 = _timedelta_to_seconds(df.get("Sector2Time", pd.Series([None] * len(df), dtype=object)))
    sector_3 = _timedelta_to_seconds(df.get("Sector3Time", pd.Series([None] * len(df), dtype=object)))

    # --- is_pit_out_lap: True if PitOutTime is non-null ---
    if "PitOutTime" in df.columns:
        is_pit_out = df["PitOutTime"].notna()
    else:
        is_pit_out = pd.Series([None] * len(df), dtype=object)

    # --- driver_number: FastF1 returns as string, we cast to int ---
    def _to_int_or_none(val) -> int | None:
        try:
            if pd.notna(val):
                return int(val)  # converts numpy.int64 → Python int
            return None
        except (ValueError, TypeError):
            return None

    driver_number = df.get("DriverNumber", pd.Series([None] * len(df), dtype=object)).apply(_to_int_or_none)
    lap_number = df.get("LapNumber", pd.Series([None] * len(df), dtype=object)).apply(_to_int_or_none)

    # --- Speed columns: int or None ---
    def _to_int_speed(series: pd.Series) -> pd.Series:
        return series.apply(lambda v: int(v) if pd.notna(v) else None)  # int() converts numpy.int64 → Python int

    i1_speed = _to_int_speed(df.get("SpeedI1", pd.Series([None] * len(df), dtype=object)))
    i2_speed = _to_int_speed(df.get("SpeedI2", pd.Series([None] * len(df), dtype=object)))
    st_speed = _to_int_speed(df.get("SpeedST", pd.Series([None] * len(df), dtype=object)))

    # --- date_start: ISO string from LapStartDate ---
    def _to_iso_str(val) -> str | None:
        if pd.notna(val):
            try:
                return pd.Timestamp(val).isoformat()
            except (ValueError, TypeError):
                return None
        return None

    date_start_col = df.get("LapStartDate", pd.Series([None] * len(df), dtype=object))
    date_start = date_start_col.apply(_to_iso_str)

    # --- Helper to coerce numpy scalars to native Python types ---
    def _pyint(val) -> int | None:
        """Convert numpy.int64 / numpy.int32 → Python int, None stays None."""
        return int(val) if val is not None and pd.notna(val) else None

    def _pyfloat(val) -> float | None:
        """Convert numpy.float64 → Python float, None stays None."""
        return float(val) if val is not None and pd.notna(val) else None

    # --- Assemble output list of dicts ---
    rows = []
    for i in range(len(df)):
        row = {
            "session_key": session_key,
            "driver_number": _pyint(driver_number.iloc[i]),
            "lap_number": _pyint(lap_number.iloc[i]),
            "lap_duration": _pyfloat(lap_duration.iloc[i]),
            "duration_sector_1": _pyfloat(sector_1.iloc[i]),
            "duration_sector_2": _pyfloat(sector_2.iloc[i]),
            "duration_sector_3": _pyfloat(sector_3.iloc[i]),
            "is_pit_out_lap": bool(is_pit_out.iloc[i]) if pd.notna(is_pit_out.iloc[i]) else None,
            "i1_speed": _pyint(i1_speed.iloc[i]),
            "i2_speed": _pyint(i2_speed.iloc[i]),
            "st_speed": _pyint(st_speed.iloc[i]),
            "date_start": date_start.iloc[i],
        }
        rows.append(row)

    logger.info(
        "Normalized %d laps for session_key=%d", len(rows), session_key
    )
    return rows
