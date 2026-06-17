"""
OpenF1 session loader — ingest a single 2023+ F1 session into raw SQLite tables.

This module is the OpenF1 counterpart to fastf1_loader.py:
  - Calls the OpenF1 REST API via OpenF1Client (httpx + tenacity retry)
  - Normalizes field names (most are already snake_case in OpenF1)
  - Writes to all 6 raw tables via upsert helpers

Per data source decision (Pitfall 4), this loader only handles year >= 2023.
FastF1 data (year <= 2022) is handled by fastf1_loader.py.

Security:
  - All SQL uses SQLAlchemy parameterized queries (T-02-05: no f-string SQL)
  - Per-field extraction from OpenF1 responses; unknown keys ignored (T-02-01)
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

from f1_prediction.db.engine import get_engine, init_db
from f1_prediction.db.upsert import bulk_insert_rows, insert_or_skip_session, session_exists
from f1_prediction.ingest.normalizer import classify_weather
from f1_prediction.ingest.openf1_client import OpenF1Client

logger = logging.getLogger(__name__)

# Mapping from identifier shorthand → OpenF1 session_type strings
_IDENTIFIER_TO_SESSION_TYPE: dict[str, str] = {
    "R": "Race",
    "Q": "Qualifying",
    "FP2": "Practice 2",
    "FP3": "Practice 3",
    "S": "Sprint",
    "SS": "Sprint Shootout",
    # Full names map to themselves
    "Race": "Race",
    "Qualifying": "Qualifying",
    "Practice 2": "Practice 2",
    "Practice 3": "Practice 3",
    "Sprint": "Sprint",
    "Sprint Shootout": "Sprint Shootout",
}

# Default identifiers for OpenF1 year loads (D-06: no FP1; D-07: include Sprint/SS)
_DEFAULT_IDENTIFIERS = ["FP2", "FP3", "Q", "R", "S", "SS"]


def _normalize_openf1_session(raw: dict) -> dict:
    """
    Convert an OpenF1 /v1/sessions row to a raw_sessions row dict.

    Extracts only documented fields (T-02-01 per-field whitelist).
    """
    # Map OpenF1 session_type to canonical values
    raw_type = raw.get("session_type") or raw.get("session_name") or ""
    session_type = _IDENTIFIER_TO_SESSION_TYPE.get(raw_type, raw_type)

    return {
        "session_key": int(raw["session_key"]),
        "year": int(raw.get("year", 0)),
        # CR-02: use round_number field directly; meeting_key is a large server-assigned
        # integer (e.g. 1219), not a sequential round number (1, 2, 3, …).
        "round_number": int(raw.get("round_number") or raw.get("meeting_round_number") or 0),
        "country_name": raw.get("country_name"),
        "circuit_short_name": raw.get("location"),
        "session_type": session_type,
        "session_name": raw.get("session_name"),
        "date_start": raw.get("date_start"),
        "date_end": raw.get("date_end"),
        "total_laps": None,  # populated below after laps are inserted
        "source": "openf1",
        "location": raw.get("location"),
        "meeting_key": int(raw["meeting_key"]) if raw.get("meeting_key") is not None else None,
        "circuit_key": int(raw["circuit_key"]) if raw.get("circuit_key") is not None else None,
        "gmt_offset": raw.get("gmt_offset"),
    }


def _normalize_openf1_laps(laps: list[dict], session_key: int) -> list[dict]:
    """
    Normalize OpenF1 /v1/laps rows to raw_laps schema.

    Extracts only the fields in the RawLap column set (T-02-01).
    """
    rows = []
    for lap in laps:
        rows.append({
            "session_key": session_key,
            "driver_number": _safe_int(lap.get("driver_number")),
            "lap_number": _safe_int(lap.get("lap_number")),
            "lap_duration": _safe_float(lap.get("lap_duration")),
            "duration_sector_1": _safe_float(lap.get("duration_sector_1")),
            "duration_sector_2": _safe_float(lap.get("duration_sector_2")),
            "duration_sector_3": _safe_float(lap.get("duration_sector_3")),
            "is_pit_out_lap": _safe_bool(lap.get("is_pit_out_lap")),
            "is_pit_in_lap": _safe_bool(lap.get("is_pit_in_lap")),
            "i1_speed": _safe_int(lap.get("i1_speed")),
            "i2_speed": _safe_int(lap.get("i2_speed")),
            "st_speed": _safe_int(lap.get("st_speed")),
            "date_start": lap.get("date_start"),
            "tyre_compound_observed": lap.get("tyre_compound_observed"),
            "segments_sector_1": _safe_json_str(lap.get("segments_sector_1")),
            "segments_sector_2": _safe_json_str(lap.get("segments_sector_2")),
            "segments_sector_3": _safe_json_str(lap.get("segments_sector_3")),
        })
    return rows


def _normalize_openf1_stints(stints: list[dict], session_key: int) -> list[dict]:
    """Normalize OpenF1 /v1/stints rows to raw_stints schema."""
    rows = []
    for s in stints:
        # WR-04: discard stints with null lap_start or lap_end rather than defaulting
        # to 1, which would create zero-length or invalid stints.
        lap_start = _safe_int(s.get("lap_start"))
        lap_end = _safe_int(s.get("lap_end"))
        if lap_start is None or lap_end is None:
            logger.debug(
                "Skipping stint record with null lap_start/lap_end for session %d", session_key
            )
            continue
        compound = s.get("compound")
        if compound is None:
            compound = "UNKNOWN"
        rows.append({
            "session_key": session_key,
            "driver_number": _safe_int(s.get("driver_number")),
            "stint_number": _safe_int(s.get("stint_number")) or 1,
            "lap_start": lap_start,
            "lap_end": lap_end,
            "compound": str(compound),
            "tyre_age_at_start": _safe_int(s.get("tyre_age_at_start")),
        })
    return rows


def _normalize_openf1_pits(pits: list[dict], session_key: int) -> list[dict]:
    """
    Normalize OpenF1 /v1/pit rows to raw_pit_stops schema.

    Uses lane_duration + stop_duration only (pit_duration deprecated per RESEARCH).
    """
    rows = []
    for p in pits:
        # WR-04: discard pit stops with null lap_number rather than defaulting to 0.
        # Lap 0 doesn't exist; a phantom pit on lap 0 inflates pit_stop_count for
        # every subsequent lap (WHERE pit_lap <= lap_number catches 0 <= 1, etc.).
        lap_num = _safe_int(p.get("lap_number"))
        if lap_num is None:
            logger.debug("Skipping pit stop record with null lap_number for session %d", session_key)
            continue
        rows.append({
            "session_key": session_key,
            "driver_number": _safe_int(p.get("driver_number")),
            "lap_number": lap_num,
            "lane_duration": _safe_float(p.get("lane_duration")),
            "stop_duration": _safe_float(p.get("stop_duration")),
            "date_recorded": p.get("date"),
        })
    return rows


def _normalize_openf1_positions(positions: list[dict], session_key: int) -> list[dict]:
    """Normalize OpenF1 /v1/position rows to raw_positions schema."""
    rows = []
    for p in positions:
        pos = _safe_int(p.get("position"))
        if pos is None:
            continue
        rows.append({
            "session_key": session_key,
            "driver_number": _safe_int(p.get("driver_number")),
            "lap_number": _safe_int(p.get("lap_number")) or 0,
            "position": pos,
            "date_recorded": p.get("date"),
        })
    return rows


# ---------------------------------------------------------------------------
# Type coercion helpers (T-02-01: per-field extraction, raises on bad types)
# ---------------------------------------------------------------------------

def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_bool(val) -> bool | None:
    if val is None:
        return None
    return bool(val)


def _safe_json_str(val) -> str | None:
    """Convert an OpenF1 array field to its JSON string representation."""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    try:
        import json
        return json.dumps(val)
    except (TypeError, ValueError):
        return str(val)


# ---------------------------------------------------------------------------
# Main loader functions
# ---------------------------------------------------------------------------

def load_openf1_session(
    year: int,
    session_key: int,
    *,
    engine: Engine | None = None,
    _transport=None,  # for testing: httpx transport override
) -> dict:
    """
    Fetch one OpenF1 session and insert it into all 6 raw tables.

    This is the primary entry point for 2023–2025 historical backfill.

    Args:
        year: Season year. Must be >= 2023 (OpenF1 only covers 2023+; Pitfall 4).
        session_key: OpenF1 session key.
        engine: Optional SQLAlchemy Engine. If None, uses the default engine.
        _transport: Optional httpx transport for testing (MockTransport).

    Returns:
        Dict with keys:
            session_key (int): The OpenF1 session key.
            laps_inserted (int): Number of lap rows inserted (0 if skipped).
            stints_inserted (int): Number of stint rows inserted.
            pits_inserted (int): Number of pit stop rows inserted.
            positions_inserted (int): Number of position rows inserted.
            weather_inserted (int): 1 if weather row inserted, else 0.
            skipped (bool): True if the session was already in raw_sessions.

    Raises:
        ValueError: If year <= 2022 (use FastF1 for 2022 data — Pitfall 4).
    """
    if year <= 2022:
        raise ValueError(
            f"OpenF1 loader is for year >= 2023 only; use FastF1 for 2022. "
            f"Got year={year}."
        )

    if engine is None:
        engine = get_engine()

    init_db(engine)

    # D-11 resumability: early-exit if session already exists
    if session_exists(engine, session_key):
        logger.info("Session %d already exists — skipping OpenF1 fetch", session_key)
        return {
            "session_key": session_key,
            "laps_inserted": 0,
            "stints_inserted": 0,
            "pits_inserted": 0,
            "positions_inserted": 0,
            "weather_inserted": 0,
            "skipped": True,
        }

    async def _fetch_all():
        """Fetch all endpoints for this session in a single event loop."""
        async with OpenF1Client(_transport=_transport) as client:
            sessions_raw = await client.sessions(session_key=session_key)
            laps_raw = await client.laps(session_key=session_key)
            stints_raw = await client.stints(session_key=session_key)
            pits_raw = await client.pit(session_key=session_key)
            positions_raw = await client.position(session_key=session_key)
            weather_raw = await client.weather(session_key=session_key)
        return sessions_raw, laps_raw, stints_raw, pits_raw, positions_raw, weather_raw

    logger.info("Fetching OpenF1 session %d (year=%d)", session_key, year)
    sessions_raw, laps_raw, stints_raw, pits_raw, positions_raw, weather_raw = asyncio.run(_fetch_all())

    # Normalize session row
    if sessions_raw:
        session_row = _normalize_openf1_session(sessions_raw[0])
    else:
        # Fallback: create minimal session row from what we know
        session_row = {
            "session_key": session_key,
            "year": year,
            "round_number": 0,
            "country_name": None,
            "circuit_short_name": None,
            "session_type": "Race",
            "session_name": None,
            "date_start": None,
            "date_end": None,
            "total_laps": None,
            "source": "openf1",
            "location": None,
            "meeting_key": None,
            "circuit_key": None,
            "gmt_offset": None,
        }

    # Insert session (D-11: insert_or_skip_session is idempotent)
    inserted = insert_or_skip_session(engine, session_row)
    if not inserted:
        # Another thread beat us — return skipped
        return {
            "session_key": session_key,
            "laps_inserted": 0,
            "stints_inserted": 0,
            "pits_inserted": 0,
            "positions_inserted": 0,
            "weather_inserted": 0,
            "skipped": True,
        }

    # Normalize and insert all data tables
    lap_rows = _normalize_openf1_laps(laps_raw, session_key)
    stint_rows = _normalize_openf1_stints(stints_raw, session_key)
    pit_rows = _normalize_openf1_pits(pits_raw, session_key)
    position_rows = _normalize_openf1_positions(positions_raw, session_key)

    # Weather classification
    weather_classification = classify_weather(weather_raw)
    weather_classification["session_key"] = session_key
    weather_rows = [weather_classification]

    laps_inserted = bulk_insert_rows(engine, "raw_laps", lap_rows)
    stints_inserted = bulk_insert_rows(engine, "raw_stints", stint_rows)
    pits_inserted = bulk_insert_rows(engine, "raw_pit_stops", pit_rows)
    positions_inserted = bulk_insert_rows(engine, "raw_positions", position_rows)
    weather_inserted = bulk_insert_rows(engine, "raw_weather", weather_rows)

    # Update total_laps on session row for race/sprint sessions
    if laps_raw:
        lap_numbers = [
            r.get("lap_number") for r in laps_raw
            if r.get("lap_number") is not None
        ]
        if lap_numbers:
            total_laps = max(lap_numbers)
            session_type = session_row.get("session_type", "")
            if session_type in ("Race", "Sprint"):
                with engine.connect() as conn:
                    conn.execute(
                        text(
                            "UPDATE raw_sessions SET total_laps = :tl "
                            "WHERE session_key = :sk"
                        ),
                        {"tl": int(total_laps), "sk": session_key},
                    )
                    conn.commit()
                logger.info(
                    "Set total_laps=%d for session_key=%d", total_laps, session_key
                )

    logger.info(
        "OpenF1 session %d loaded: laps=%d stints=%d pits=%d positions=%d weather=%d",
        session_key, laps_inserted, stints_inserted, pits_inserted,
        positions_inserted, weather_inserted,
    )

    return {
        "session_key": session_key,
        "laps_inserted": laps_inserted,
        "stints_inserted": stints_inserted,
        "pits_inserted": pits_inserted,
        "positions_inserted": positions_inserted,
        "weather_inserted": weather_inserted,
        "skipped": False,
    }


def load_openf1_year(
    year: int,
    *,
    engine: Engine | None = None,
    rounds: list[int] | None = None,
    identifiers: list[str] | None = None,
) -> list[dict]:
    """
    Load all sessions for a given year using OpenF1.

    Fetches the session list from /v1/sessions?year=year, filters by
    session_type to the configured identifiers (D-06: no FP1; D-07: Sprint/SS).

    Args:
        year: Season year (must be >= 2023).
        engine: Optional SQLAlchemy Engine.
        rounds: Optional list of meeting_key values to restrict the load.
                If None, all sessions for the year are loaded.
        identifiers: Optional list of identifier strings (e.g. ['R', 'Q']).
                     Defaults to ['FP2', 'FP3', 'Q', 'R', 'S', 'SS'].

    Returns:
        List of result dicts from load_openf1_session per session loaded.
    """
    if year <= 2022:
        raise ValueError(
            f"OpenF1 loader is for year >= 2023 only. Got year={year}."
        )

    if identifiers is None:
        identifiers = _DEFAULT_IDENTIFIERS

    # Build the set of target session_types from the identifier list
    target_types = {
        _IDENTIFIER_TO_SESSION_TYPE.get(ident, ident)
        for ident in identifiers
    }

    # Fetch session list for the year
    async def _fetch_sessions():
        async with OpenF1Client() as client:
            return await client.sessions(year=year)

    logger.info("Fetching OpenF1 session list for year=%d", year)
    all_sessions = asyncio.run(_fetch_sessions())

    results: list[dict] = []
    for sess in all_sessions:
        sess_type = sess.get("session_type") or sess.get("session_name") or ""
        canonical_type = _IDENTIFIER_TO_SESSION_TYPE.get(sess_type, sess_type)

        if canonical_type not in target_types:
            logger.debug("Skipping session_type=%s (not in target identifiers)", sess_type)
            continue

        if rounds is not None:
            # CR-02: filter on round_number (sequential 1, 2, 3…), not meeting_key
            # (which is a large server-assigned int like 1219 that doesn't match
            # user-supplied round numbers from the CLI).
            sess_round = sess.get("round_number") or sess.get("meeting_round_number")
            if sess_round is not None and int(sess_round) not in rounds:
                logger.debug("Skipping round_number=%s (not in rounds filter)", sess_round)
                continue

        sk = int(sess.get("session_key", 0))
        if sk == 0:
            logger.warning("Session with meeting_key=%s has no session_key — skipping", sess.get("meeting_key"))
            continue

        year_val = int(sess.get("year", year))
        try:
            result = load_openf1_session(year_val, sk, engine=engine)
            results.append(result)
        except Exception:
            logger.exception("Failed to load OpenF1 session %d", sk)
            results.append({
                "session_key": sk,
                "skipped": False,
                "error": True,
                "laps_inserted": 0,
                "stints_inserted": 0,
                "pits_inserted": 0,
                "positions_inserted": 0,
                "weather_inserted": 0,
            })

    return results
