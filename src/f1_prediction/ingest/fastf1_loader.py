"""
FastF1 session loader — ingest a single 2022 F1 session into raw SQLite tables.

This module is the boundary between FastF1 and the rest of the system:
- It enables the FastF1 cache (once, at import time).
- It loads a session via FastF1's high-level API.
- It normalizes the session and lap data to the OpenF1-compatible schema.
- It writes to raw_sessions, raw_laps, raw_stints, raw_pit_stops,
  raw_positions, and raw_weather via the upsert helpers.

Per data source decision (D-10 and Pitfall 4), this loader only handles
year <= 2022. OpenF1 data (year >= 2023) is handled by openf1_loader.py.

FastF1 cache is stored at project-root/data/cache/ (see SKELETON.md).

Session type coverage for stints/pits/positions:
- Race (R), Sprint (S): full set of stints, pits, positions, weather
- Qualifying (Q), Practice 2 (FP2), Practice 3 (FP3): laps + weather only
  (Stints/pits/positions are less meaningful for non-race sessions and
  FastF1 data quality is lower for these; they are intentionally skipped.)
"""
from __future__ import annotations

import logging
from pathlib import Path

import fastf1

from f1_prediction.db.engine import get_engine, init_db
from f1_prediction.db.upsert import bulk_insert_rows, insert_or_skip_session
from f1_prediction.ingest.normalizer import (
    classify_weather,
    normalize_fastf1_laps,
    normalize_fastf1_pits,
    normalize_fastf1_positions,
    normalize_fastf1_session,
    normalize_fastf1_stints,
    normalize_fastf1_weather,
)

logger = logging.getLogger(__name__)

# Module-level flag to ensure cache is only enabled once per process
_cache_enabled: bool = False

# Identifiers for which stints/pits/positions are ingested (race-pace sessions)
_RACE_PACE_IDENTIFIERS = frozenset({"R", "Race", "S", "Sprint"})

# Default identifiers for load_year (D-06: no FP1; D-07: Sprint/SS included)
_DEFAULT_IDENTIFIERS = ["FP2", "FP3", "Q", "R", "S", "SS"]


def _ensure_cache_enabled() -> None:
    """Enable FastF1 file cache (idempotent — only runs once per process)."""
    global _cache_enabled
    if _cache_enabled:
        return

    cache_dir = Path(__file__).resolve().parents[3] / "data" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(cache_dir))
    _cache_enabled = True
    logger.info("FastF1 cache enabled at %s", cache_dir)


def load_session(
    year: int,
    round_number: int,
    identifier: str = "R",
    *,
    engine=None,
) -> dict:
    """
    Load a FastF1 session and insert it into all 6 raw tables.

    Extends Plan 01's lap-only behavior: now also ingests stints, pit stops,
    positions, and weather data (Plan 02 requirement).

    For qualifying and practice sessions (Q, FP2, FP3), only laps and weather
    are ingested. Stints, pits, and positions are only ingested for race-pace
    sessions (R, S) where FastF1 data quality is higher.

    Args:
        year: Season year. Must be <= 2022 (FastF1 only; OpenF1 handles 2023+).
        round_number: Round number in the season (1-based, e.g. 1 for Bahrain 2022).
        identifier: Session identifier string:
            'R' = Race (default)
            'Q' = Qualifying
            'FP2' = Practice 2
            'FP3' = Practice 3
            'S' = Sprint
            'SS' = Sprint Shootout
        engine: Optional SQLAlchemy Engine. If None, uses the default engine
                pointing at data/f1.db.

    Returns:
        Dict with keys:
            session_key (int): The synthesized session key.
            laps_inserted (int): Number of lap rows inserted (0 if skipped).
            stints_inserted (int): Number of stint rows inserted.
            pits_inserted (int): Number of pit stop rows inserted.
            positions_inserted (int): Number of position rows inserted.
            weather_inserted (int): 1 if weather row inserted, 0 otherwise.
            skipped (bool): True if the session was already in raw_sessions.

    Raises:
        ValueError: If year > 2022 (route to OpenF1 instead — Pitfall 4).
        Exception: Any FastF1 or SQLAlchemy error is logged then re-raised.
    """
    # Guard: this loader is 2022-only
    if year > 2022:
        raise ValueError(
            f"FastF1 loader is for year <= 2022 only; use OpenF1 for 2023+. "
            f"Got year={year}."
        )

    _ensure_cache_enabled()

    if engine is None:
        engine = get_engine()

    # Ensure tables exist (idempotent)
    init_db(engine)

    try:
        logger.info(
            "Loading FastF1 session year=%d round=%d identifier=%s",
            year, round_number, identifier,
        )

        # Load session — telemetry=False prevents 50-100 MB downloads per session
        # (Pitfall 2 / T-01-04)
        sess = fastf1.get_session(year, round_number, identifier)
        sess.load(laps=True, telemetry=False, weather=True, messages=False)

        # Compute total_laps for race sessions (D-11 note: order matters)
        total_laps = None
        if identifier in _RACE_PACE_IDENTIFIERS:
            try:
                if sess.laps is not None and len(sess.laps) > 0:
                    total_laps = int(sess.laps["LapNumber"].max())
            except (KeyError, ValueError, TypeError):
                total_laps = None

        # Normalize session metadata (includes total_laps)
        session_row = normalize_fastf1_session(sess, year=year, round_number=round_number)
        if total_laps is not None:
            session_row["total_laps"] = total_laps

        # D-11 resumability: skip if session already exists
        inserted = insert_or_skip_session(engine, session_row)
        if not inserted:
            logger.info(
                "Session year=%d round=%d %s already exists — skipping all inserts",
                year, round_number, identifier,
            )
            return {
                "session_key": session_row["session_key"],
                "laps_inserted": 0,
                "stints_inserted": 0,
                "pits_inserted": 0,
                "positions_inserted": 0,
                "weather_inserted": 0,
                "skipped": True,
            }

        sk = session_row["session_key"]
        is_race_pace = identifier in _RACE_PACE_IDENTIFIERS

        # Normalize and insert laps (all sessions)
        lap_rows = normalize_fastf1_laps(
            sess.laps, session_key=sk
        )
        laps_inserted = bulk_insert_rows(engine, "raw_laps", lap_rows)

        # Normalize and insert stints, pits, positions (race-pace sessions only)
        stints_inserted = 0
        pits_inserted = 0
        positions_inserted = 0

        if is_race_pace:
            stint_rows = normalize_fastf1_stints(sess, session_key=sk)
            stints_inserted = bulk_insert_rows(engine, "raw_stints", stint_rows)

            pit_rows = normalize_fastf1_pits(sess, session_key=sk)
            pits_inserted = bulk_insert_rows(engine, "raw_pit_stops", pit_rows)

            position_rows = normalize_fastf1_positions(sess, session_key=sk)
            positions_inserted = bulk_insert_rows(engine, "raw_positions", position_rows)

        # Weather (all sessions — DATA-02)
        weather_row = normalize_fastf1_weather(sess, session_key=sk)
        weather_inserted = bulk_insert_rows(engine, "raw_weather", [weather_row])

        logger.info(
            "FastF1 session year=%d round=%d %s loaded: "
            "laps=%d stints=%d pits=%d positions=%d weather=%d",
            year, round_number, identifier,
            laps_inserted, stints_inserted, pits_inserted,
            positions_inserted, weather_inserted,
        )
        return {
            "session_key": sk,
            "laps_inserted": laps_inserted,
            "stints_inserted": stints_inserted,
            "pits_inserted": pits_inserted,
            "positions_inserted": positions_inserted,
            "weather_inserted": weather_inserted,
            "skipped": False,
        }

    except Exception:
        logger.exception(
            "Failed to load FastF1 session year=%d round=%d %s",
            year, round_number, identifier,
        )
        raise


def load_year(
    year: int,
    *,
    engine=None,
    rounds: list[int] | None = None,
    identifiers: list[str] | None = None,
) -> list[dict]:
    """
    Load all sessions for a FastF1 year (year <= 2022).

    Iterates the event schedule via fastf1.get_event_schedule(). Applies
    sprint-detection logic (RESEARCH Pattern 6) to pick which session types
    to load per round.

    Args:
        year: Season year (must be <= 2022 — FastF1 path only).
        engine: Optional SQLAlchemy Engine.
        rounds: Optional list of round numbers to restrict the load.
                If None, all rounds in the season are loaded.
        identifiers: Optional list of identifier strings (e.g. ['R', 'Q']).
                     Defaults to ['FP2', 'FP3', 'Q', 'R', 'S', 'SS'].

    Returns:
        List of result dicts from load_session per session loaded.
    """
    if year > 2022:
        raise ValueError(
            f"FastF1 loader is for year <= 2022 only. Got year={year}."
        )

    _ensure_cache_enabled()

    if identifiers is None:
        identifiers = _DEFAULT_IDENTIFIERS

    if engine is None:
        engine = get_engine()

    logger.info("Loading FastF1 year=%d schedule", year)
    schedule = fastf1.get_event_schedule(year, include_testing=False)

    results: list[dict] = []

    for _, event in schedule.iterrows():
        round_num = int(event.get("RoundNumber", 0))
        if round_num == 0:
            continue

        if rounds is not None and round_num not in rounds:
            logger.debug("Skipping round=%d (not in rounds filter)", round_num)
            continue

        # Sprint detection (RESEARCH Pattern 6)
        fmt = event.get("EventFormat", "conventional")
        if fmt == "conventional":
            # Standard weekend: FP2, FP3, Q, R available
            round_identifiers = [i for i in identifiers if i not in ("S", "SS")]
        elif fmt in ("sprint", "sprint_shootout", "sprint_qualifying"):
            # Sprint weekend: S available instead of FP2/FP3
            round_identifiers = [i for i in identifiers if i not in ("FP2", "FP3")]
        else:
            # Unknown format: conservative subset
            round_identifiers = [i for i in identifiers if i in ("Q", "R")]

        for ident in round_identifiers:
            try:
                result = load_session(year, round_num, ident, engine=engine)
                result["year"] = year
                result["round_number"] = round_num
                result["identifier"] = ident
                results.append(result)
            except Exception:
                logger.exception(
                    "Failed to load FastF1 session year=%d round=%d %s",
                    year, round_num, ident,
                )
                results.append({
                    "year": year,
                    "round_number": round_num,
                    "identifier": ident,
                    "session_key": None,
                    "error": True,
                    "laps_inserted": 0,
                    "stints_inserted": 0,
                    "pits_inserted": 0,
                    "positions_inserted": 0,
                    "weather_inserted": 0,
                    "skipped": False,
                })

    return results
