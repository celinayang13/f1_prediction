"""
FastF1 session loader — ingest a single 2022 F1 session into raw SQLite tables.

This module is the boundary between FastF1 and the rest of the system:
- It enables the FastF1 cache (once, at import time).
- It loads a session via FastF1's high-level API.
- It normalizes the session and lap data to the OpenF1-compatible schema.
- It writes to raw_sessions and raw_laps via the upsert helpers.

Per data source decision (D-10 and Pitfall 4), this loader only handles
year <= 2022. OpenF1 data (year >= 2023) is handled by the openf1_client
module (Plan 02).

FastF1 cache is stored at project-root/data/cache/ (see SKELETON.md).
"""
from __future__ import annotations

import logging
from pathlib import Path

import fastf1

from f1_prediction.db.engine import get_engine, init_db
from f1_prediction.db.upsert import bulk_insert_rows, insert_or_skip_session
from f1_prediction.ingest.normalizer import (
    normalize_fastf1_laps,
    normalize_fastf1_session,
)

logger = logging.getLogger(__name__)

# Module-level flag to ensure cache is only enabled once per process
_cache_enabled: bool = False


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
    Load a FastF1 session and insert it into the raw_sessions and raw_laps tables.

    This function implements the full FastF1 → normalizer → ORM → SQLite
    pipeline for a single session. It is the primary entry point for the
    2022 historical backfill.

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

        # Normalize session metadata
        session_row = normalize_fastf1_session(sess, year=year, round_number=round_number)

        # D-11 resumability: skip if session already exists
        inserted = insert_or_skip_session(engine, session_row)
        if not inserted:
            logger.info(
                "Session year=%d round=%d %s already exists — skipping laps insert",
                year, round_number, identifier,
            )
            return {
                "session_key": session_row["session_key"],
                "laps_inserted": 0,
                "skipped": True,
            }

        # Normalize and insert laps
        lap_rows = normalize_fastf1_laps(
            sess.laps, session_key=session_row["session_key"]
        )
        laps_inserted = bulk_insert_rows(engine, "raw_laps", lap_rows)

        logger.info(
            "Session year=%d round=%d %s loaded: %d laps inserted",
            year, round_number, identifier, laps_inserted,
        )
        return {
            "session_key": session_row["session_key"],
            "laps_inserted": laps_inserted,
            "skipped": False,
        }

    except Exception:
        logger.exception(
            "Failed to load FastF1 session year=%d round=%d %s",
            year, round_number, identifier,
        )
        raise
