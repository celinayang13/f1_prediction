"""
SQLAlchemy ORM models for F1 prediction raw data tables.

This module defines the declarative Base and all raw-data models:
  - RawSession   (table: raw_sessions)
  - RawLap       (table: raw_laps)
  - RawStint     (table: raw_stints)
  - RawPitStop   (table: raw_pit_stops)
  - RawPosition  (table: raw_positions)
  - RawWeather   (table: raw_weather)

Schema is created via `Base.metadata.create_all(engine)`. For Phase 1,
schema changes are handled by deleting `data/f1.db` and re-backfilling
(no Alembic migrations until v2). Tests use `tmp_engine` and are unaffected.

Schema follows the OpenF1 column naming convention so that FastF1
(2022) and OpenF1 (2023+) data share a single schema. FastF1-specific
columns are dropped at ingest time by the normalizer.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models in this project."""


class RawSession(Base):
    """
    One row per F1 session (Race, Qualifying, Practice 2, Practice 3,
    Sprint, or Sprint Shootout) fetched from FastF1 or OpenF1.

    session_key is a synthetic integer for FastF1 data (see normalizer.py
    for the synthesis scheme). For OpenF1 data, session_key matches the
    OpenF1 server's native key.

    The UniqueConstraint on (year, round_number, session_type) provides
    DB-layer defence against duplicate inserts from re-runs (D-11).
    meeting_key and circuit_key are OpenF1-specific identifiers (NULL for
    FastF1 source rows).
    """

    __tablename__ = "raw_sessions"

    session_key = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False, index=True)
    round_number = Column(Integer, nullable=False)
    country_name = Column(String)
    circuit_short_name = Column(String)
    session_type = Column(
        String, nullable=False
    )  # e.g. 'Race', 'Qualifying', 'Practice 2'
    session_name = Column(String)
    date_start = Column(String)  # ISO 8601 string
    date_end = Column(String)  # ISO 8601 string
    total_laps = Column(Integer)  # NULL until populated for completed races
    source = Column(String, nullable=False)  # 'fastf1' or 'openf1'
    # OpenF1-specific fields (NULL for FastF1 source rows)
    location = Column(String)
    meeting_key = Column(Integer)
    circuit_key = Column(Integer)
    gmt_offset = Column(String)

    __table_args__ = (
        UniqueConstraint(
            "year",
            "round_number",
            "session_type",
            name="uq_raw_sessions_year_round_type",
        ),
    )


class RawLap(Base):
    """
    One row per lap per driver per session.

    Columns mirror the OpenF1 /v1/laps endpoint field names (D-09: full mirror).
    FastF1-specific fields (IsAccurate, Compound, TyreLife, etc.) are
    dropped at normalisation time (D-10). Compound/TyreLife are sourced from
    the raw_stints table (D-04).

    segments_sector_1/2/3 are OpenF1 mini-sector arrays, stored as JSON
    text strings (OpenF1 returns them as JSON arrays; SQLite stores as TEXT).

    A composite index on (session_key, driver_number, lap_number) supports
    the most common lookup pattern in the feature builder.
    """

    __tablename__ = "raw_laps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_key = Column(Integer, nullable=False, index=True)
    driver_number = Column(Integer, nullable=False)
    lap_number = Column(Integer, nullable=False)
    lap_duration = Column(Float)
    duration_sector_1 = Column(Float)
    duration_sector_2 = Column(Float)
    duration_sector_3 = Column(Float)
    is_pit_out_lap = Column(Boolean)
    i1_speed = Column(Integer)
    i2_speed = Column(Integer)
    st_speed = Column(Integer)
    date_start = Column(String)  # ISO 8601 string of lap start datetime
    # Extended OpenF1 fields (D-09)
    is_pit_in_lap = Column(Boolean)  # True if driver pitted at end of this lap
    tyre_compound_observed = Column(String)  # compound from laps endpoint (D-09 full mirror)
    # Mini-sector arrays stored as serialized JSON text (OpenF1 returns as arrays)
    segments_sector_1 = Column(String)  # JSON text, e.g. '[[2049, 2049, 2051], ...]'
    segments_sector_2 = Column(String)  # JSON text
    segments_sector_3 = Column(String)  # JSON text

    __table_args__ = (
        Index(
            "ix_raw_laps_session_driver_lap",
            "session_key",
            "driver_number",
            "lap_number",
        ),
    )


class RawStint(Base):
    """
    One row per driver stint per session (one stint = continuous run on one tyre set).

    Sourced from OpenF1 /v1/stints (2023+) or derived from FastF1 session.laps
    Compound column (2022). compound uses OpenF1 spelling (SOFT/MEDIUM/HARD/
    INTERMEDIATE/WET). tyre_age_at_start is NULL when not available from source.

    Index on (session_key, driver_number) supports per-driver tyre lookups
    during feature engineering.
    """

    __tablename__ = "raw_stints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_key = Column(Integer, nullable=False)
    driver_number = Column(Integer, nullable=False)
    stint_number = Column(Integer, nullable=False)
    lap_start = Column(Integer, nullable=False)
    lap_end = Column(Integer, nullable=False)
    compound = Column(String, nullable=False)  # SOFT/MEDIUM/HARD/INTERMEDIATE/WET
    tyre_age_at_start = Column(Integer)  # NULL when source does not provide

    __table_args__ = (
        Index(
            "ix_raw_stints_session_driver",
            "session_key",
            "driver_number",
        ),
    )


class RawPitStop(Base):
    """
    One row per pit stop per driver per session.

    Uses lane_duration (full pit lane transit time) and stop_duration
    (stationary time). Per RESEARCH State of the Art: pit_duration is
    deprecated in OpenF1; stop_duration is only available from the 2024
    US GP onward — NULL for earlier races is expected and correct.

    Index on (session_key, driver_number) supports per-driver pit lookups.
    """

    __tablename__ = "raw_pit_stops"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_key = Column(Integer, nullable=False)
    driver_number = Column(Integer, nullable=False)
    lap_number = Column(Integer, nullable=False)
    lane_duration = Column(Float)   # full pit lane transit time (seconds)
    stop_duration = Column(Float)   # stationary time; NULL for pre-2024-US-GP
    date_recorded = Column(String)  # ISO 8601 string

    __table_args__ = (
        Index(
            "ix_raw_pit_stops_session_driver",
            "session_key",
            "driver_number",
        ),
    )


class RawPosition(Base):
    """
    One row per driver per lap showing track position.

    Sourced from OpenF1 /v1/position (2023+) or FastF1 session.laps
    Position column (2022). position is the race position (1–20).

    Index on (session_key, lap_number) supports feature builder lookups
    for interval_behind computation (requires knowing who is at position+1).
    """

    __tablename__ = "raw_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_key = Column(Integer, nullable=False)
    driver_number = Column(Integer, nullable=False)
    lap_number = Column(Integer, nullable=False)
    position = Column(Integer, nullable=False)  # 1–20
    date_recorded = Column(String)  # ISO 8601 string

    __table_args__ = (
        Index(
            "ix_raw_positions_session_lap",
            "session_key",
            "lap_number",
        ),
    )


class RawWeather(Base):
    """
    One row per session with weather classification (DATA-02).

    wet_dry_mixed is a session-level rollup from the rainfall flag:
      - 'dry':   0% of observations have rainfall=1
      - 'mixed': >0% and <50% have rainfall=1
      - 'wet':   >=50% have rainfall=1

    The CHECK constraint enforces the enum at the DB layer (T-02-02).
    rainfall_flag_count and total_observation_count document how the
    classification was derived (audit trail).

    air_temp_c, track_temp_c, humidity_pct are medians across the session.
    """

    __tablename__ = "raw_weather"

    session_key = Column(Integer, primary_key=True)  # one row per session
    wet_dry_mixed = Column(String, nullable=False)   # 'wet', 'dry', or 'mixed'
    air_temp_c = Column(Float)     # median air temperature (°C)
    track_temp_c = Column(Float)   # median track temperature (°C)
    humidity_pct = Column(Float)   # median humidity (%)
    rainfall_flag_count = Column(Integer, nullable=False, default=0)
    total_observation_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "wet_dry_mixed IN ('wet', 'dry', 'mixed')",
            name="ck_raw_weather_wet_dry_mixed",
        ),
    )
