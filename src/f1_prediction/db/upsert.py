"""
Upsert helpers for raw data tables.

Provides two helpers:
  - insert_or_skip_session: insert a session row only if it doesn't exist (D-11 resumability)
  - bulk_insert_rows: chunked bulk insert of lap/stint/position rows via pandas

Security: All SQL uses SQLAlchemy parameterized queries (`text()` with bound
`:param` names). No f-string SQL is used anywhere in this module (T-01-02).
"""
from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from f1_prediction.db.models import RawSession

logger = logging.getLogger(__name__)


def insert_or_skip_session(engine: Engine, session_row: dict) -> bool:
    """
    Insert a session row into raw_sessions if it does not already exist.

    Implements D-11 resumability: if raw_sessions already has a row with
    the same session_key, the insert is skipped and False is returned.
    This allows backfill.py to be re-run after failure without creating
    duplicates.

    Args:
        engine: SQLAlchemy Engine.
        session_row: Dict matching RawSession column names.

    Returns:
        True if the row was inserted; False if it already existed.
    """
    sk = session_row["session_key"]
    # WR-01: atomic insert — catch IntegrityError instead of a separate existence
    # check (which had a TOCTOU race between two separate connection opens).
    with Session(engine) as session:
        try:
            session.add(RawSession(**session_row))
            session.commit()
            logger.info("Inserted session %d into raw_sessions", sk)
            return True
        except IntegrityError:
            session.rollback()
            logger.debug("Session %d already exists (concurrent insert) — skipping", sk)
            return False


def session_exists(engine: Engine, session_key: int) -> bool:
    """
    Return True if a row with the given session_key exists in raw_sessions.

    Extracted from insert_or_skip_session so loaders can early-exit before
    fetching expensive upstream data (D-11 resumability).

    Security: uses parameterized query — no f-string SQL (T-02-05).

    Args:
        engine: SQLAlchemy Engine.
        session_key: The session key to check.

    Returns:
        True if the session exists; False otherwise.
    """
    check_sql = text("SELECT 1 FROM raw_sessions WHERE session_key = :sk")
    with engine.connect() as conn:
        row = conn.execute(check_sql, {"sk": session_key}).fetchone()
    return row is not None


def bulk_insert_rows(engine: Engine, table_name: str, rows: list[dict]) -> int:
    """
    Insert a list of row dicts into the given table using pandas bulk insert.

    Uses pandas `to_sql` with `method="multi"` and `chunksize=500` to stay
    well within SQLite's 999-parameter-per-statement limit (Pitfall 6:
    15 columns × 500 rows = 7,500 parameters — safely below the limit).

    Args:
        engine: SQLAlchemy Engine.
        table_name: Target table name (must already exist).
        rows: List of dicts, each mapping column name → value.

    Returns:
        Number of rows inserted (len(rows)). Returns 0 for empty input
        without opening a database connection.
    """
    if not rows:
        return 0

    df = pd.DataFrame(rows)
    with engine.connect() as conn:
        df.to_sql(
            table_name,
            conn,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=500,
        )
        conn.commit()

    logger.info("Bulk-inserted %d rows into %s", len(rows), table_name)
    return len(rows)
