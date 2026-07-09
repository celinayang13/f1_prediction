"""
Shared read-only DB queries with no scheduler/inference dependencies (WR-03).

Extracted from pipeline.py so that dashboard_lib/queries.py (and any other
read-only consumer) can resolve the latest race session_key without pulling
in pipeline.py's transitive dependency graph (APScheduler, live/poller.py,
inference/inrace.py, inference/prerace.py) — dashboard.py's docstring states
the pipeline (writer) and dashboard (reader) processes "never talk directly
... they communicate only through SQLite in WAL mode"; importing pipeline.py
from the dashboard violated that.

Security: all SQL uses text() with bound :param names — no f-string SQL (T-03-05).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

__all__ = ["get_latest_race_session_key"]


def get_latest_race_session_key(engine: Engine) -> "int | None":
    """
    Resolve the most recent Race-type session_key (D-01).

    Orders by date_start DESC rather than session_key DESC. FastF1-synthesized
    2022 session_keys (year*100_000 + round*100 + session_type_code) are
    numerically larger than real OpenF1 2023+ session_keys (small sequential
    server-assigned integers), so a raw session_key DESC ordering always
    resolves to a stale 2022 session instead of the true latest race —
    verified against the project DB (session_key DESC picks 202202207 / 2022
    Abu Dhabi over any 2023-2025 race). date_start is populated as an ISO 8601
    string by both FastF1 and OpenF1 ingestion paths and sorts correctly
    across both session_key schemes.

    Args:
        engine: SQLAlchemy Engine.

    Returns:
        The latest Race session_key, or None if no Race session exists.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT session_key FROM raw_sessions "
                "WHERE session_type = 'Race' "
                "ORDER BY date_start DESC LIMIT 1"
            )
        ).fetchone()
    return int(row[0]) if row else None
