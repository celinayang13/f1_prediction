"""
SQLAlchemy engine factory with WAL mode for F1 prediction database.

Provides `get_engine()` which returns a SQLAlchemy Engine configured
with SQLite WAL journal mode, allowing concurrent reads from the
dashboard while the live poller writes.

Default database path: project-root/data/f1.db
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

# Default database path — project root is three levels above this file:
# src/f1_prediction/db/engine.py → src/f1_prediction/db → src/f1_prediction → src → root
DB_PATH: Path = Path(__file__).resolve().parents[3] / "data" / "f1.db"


def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    """Event listener: enable WAL mode and NORMAL sync on every new connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def get_engine(db_path: "str | Path | None" = None) -> Engine:
    """
    Create and return a SQLAlchemy Engine for the F1 SQLite database.

    WAL mode is enabled via a connection event listener so that the
    live poller (writer) and future dashboard (reader) can operate
    concurrently without blocking each other.

    Args:
        db_path: Path to the SQLite file. Defaults to DB_PATH
                 (project-root/data/f1.db). The parent directory is
                 created automatically if it does not exist.

    Returns:
        Configured SQLAlchemy Engine (SQLite + WAL mode).
    """
    resolved: Path = Path(db_path) if db_path is not None else DB_PATH
    resolved.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite:///{resolved}", echo=False, future=True)
    event.listen(engine, "connect", _set_sqlite_pragma)
    return engine


def init_db(engine: Engine) -> None:
    """
    Create all ORM-declared tables in the database.

    This is idempotent — calling it multiple times is safe because
    SQLAlchemy uses CREATE TABLE IF NOT EXISTS internally.

    Args:
        engine: A SQLAlchemy Engine (from `get_engine()`).
    """
    # Import here to avoid circular imports; models must be imported
    # after the Base is defined in models.py.
    from f1_prediction.db.models import Base  # noqa: PLC0415

    Base.metadata.create_all(engine)
