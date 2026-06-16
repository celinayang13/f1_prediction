"""
Shared pytest fixtures for the F1 prediction test suite.

Fixtures:
    tmp_engine: Provides an isolated SQLAlchemy Engine backed by a
                temporary SQLite file (scoped per test via tmp_path).
                Tests use this fixture to avoid touching the dev database
                at data/f1.db (T-01-05: test DB isolation).
"""
import pytest

from f1_prediction.db.engine import get_engine, init_db


@pytest.fixture
def tmp_engine(tmp_path):
    """
    Yield a per-test SQLAlchemy Engine backed by a temporary SQLite file.

    The database file is created inside pytest's tmp_path, which is unique
    per test invocation. WAL mode is enabled via the engine's connection
    event listener (Pattern 2). All tables are created via init_db().

    The engine is closed automatically at the end of each test.
    """
    db_path = tmp_path / "test.db"
    engine = get_engine(db_path)
    init_db(engine)
    yield engine
    engine.dispose()
