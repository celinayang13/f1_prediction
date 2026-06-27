"""
SQLAlchemy ORM models for F1 prediction raw data tables.

This module defines the declarative Base and all raw-data models:
  - RawSession      (table: raw_sessions)
  - RawLap          (table: raw_laps)
  - RawStint        (table: raw_stints)
  - RawPitStop      (table: raw_pit_stops)
  - RawPosition     (table: raw_positions)
  - RawWeather      (table: raw_weather)
  - FeaturesPrerace (table: features_prerace)    [Plan 03]
  - FeaturesInrace  (table: features_inrace)     [Plan 03]

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
    PrimaryKeyConstraint,
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
        # NOTE: the (year, round_number, session_type) unique constraint was
        # removed because OpenF1 sessions are inserted with round_number=0 (the
        # API does not return a sequential round number).  Multiple sessions of
        # the same type per year would all have round_number=0, causing
        # IntegrityError on the second insert and blocking multi-round backfill.
        # The session_key primary key already enforces row uniqueness.
        # fix_round_numbers() in backfill.py derives correct round_numbers
        # after all sessions for the year are fetched.
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


class FeaturesPrerace(Base):
    """
    Pre-race feature row: one row per (session_key, driver_number).

    session_key references a Race session in raw_sessions. Features are
    computed from the matching Qualifying and FP2/FP3 sessions for the
    same (year, round_number) via the feature builder.

    Composite PK (session_key, driver_number) doubles as the dedupe key —
    the builder DELETEs then re-INSERTs for a given session_key to allow
    clean re-runs (D-13).

    Columns (D-02):
      qualifying_delta_to_pole  — gap in seconds behind P1 qualifier
      practice_pace_fp2         — driver's median FP2 pace vs field (negative = faster)
      practice_pace_fp3         — driver's median FP3 pace vs field
      circuit_win_rate          — driver's win rate at this circuit (pre-computed, D-04)
      circuit_avg_finish        — constructor's avg finish at this circuit last 2 seasons
      constructor_pace_delta    — constructor pace vs field median at this circuit (D-04)
      championship_position     — driver standings position before this race
      grid_position             — starting grid position (approximated from lap-1 raw_positions)
      driver_seasons_in_f1      — distinct seasons driver appears in raw_sessions
      finish_position           — target label (int, 1-based; D-05)
      team_name                 — constructor name (stored to avoid runtime driver→team join)

    Chronological ordering requirement (D-04):
      The builder MUST process sessions in chronological (year, round_number) order
      so circuit_win_rate and circuit_avg_finish can read prior years' finish_position
      rows from this table.
    """

    __tablename__ = "features_prerace"

    session_key = Column(Integer, nullable=False)
    driver_number = Column(Integer, nullable=False)
    qualifying_delta_to_pole = Column(Float)       # NULL if no Q session found
    practice_pace_fp2 = Column(Float)              # NULL if FP2 not available
    practice_pace_fp3 = Column(Float)              # NULL if FP3 not available
    circuit_win_rate = Column(Float)               # NULL for debut at circuit (no prior races)
    circuit_avg_finish = Column(Float)             # NULL if no prior constructor data
    constructor_pace_delta = Column(Float)         # NULL if no prior constructor data
    championship_position = Column(Integer)        # NULL for first race of season
    grid_position = Column(Integer)                # NULL if no lap-1 position data
    driver_seasons_in_f1 = Column(Integer)         # ≥ 1 for any driver in raw_sessions
    finish_position = Column(Integer)              # target label; NULL for non-race sessions
    team_name = Column(String)                     # constructor name

    __table_args__ = (
        PrimaryKeyConstraint("session_key", "driver_number", name="pk_features_prerace"),
    )


class FeaturesInrace(Base):
    """
    In-race feature rows: one row per (session_key, driver_number, lap_number).

    Computed for Race sessions only. Joins raw_laps + raw_stints + raw_pit_stops
    + raw_positions per driver per lap.

    Composite PK (session_key, driver_number, lap_number) is the dedupe key.

    Columns (D-03):
      lap_number        — current lap number (1-based)
      gap_to_leader     — cumulative gap in seconds (derived from cumsum of lap_duration)
      tyre_compound     — compound on current lap (SOFT/MEDIUM/HARD/INTERMEDIATE/WET)
      tyre_age_laps     — laps on current tyre set
      pit_stop_count    — cumulative pit stops as of this lap (default 0)
      position          — race position (1–20)
      interval_ahead    — gap in seconds to car immediately ahead
      interval_behind   — gap in seconds to car immediately behind
      pit_stop_delta    — lane_duration of most recent pit stop (NULL if no pit this lap)
      laps_remaining    — total_laps - lap_number (NULL if total_laps not in raw_sessions)

    Approximations (documented):
      gap_to_leader  — cumulative sum of lap_duration per driver, difference vs leader's cumsum
      interval_ahead — derived from gap_to_leader differences between adjacent positions
      interval_behind— derived from gap_to_leader differences between adjacent positions
      grid_position  — taken from lap-1 raw_positions (formation lap not strictly available)
    """

    __tablename__ = "features_inrace"

    session_key = Column(Integer, nullable=False)
    driver_number = Column(Integer, nullable=False)
    lap_number = Column(Integer, nullable=False)
    gap_to_leader = Column(Float)            # NULL on lap 1 (leader has 0 by definition)
    tyre_compound = Column(String)           # NULL if no stint data available
    tyre_age_laps = Column(Integer)          # NULL if tyre_age_at_start not recorded
    pit_stop_count = Column(Integer, nullable=False, default=0)
    position = Column(Integer)               # NULL if no position data
    interval_ahead = Column(Float)           # NULL for leader (no car ahead)
    interval_behind = Column(Float)          # NULL for last car (no car behind)
    pit_stop_delta = Column(Float)           # NULL if no pit stop this lap
    laps_remaining = Column(Integer)         # NULL if total_laps not known

    __table_args__ = (
        PrimaryKeyConstraint(
            "session_key", "driver_number", "lap_number",
            name="pk_features_inrace",
        ),
    )
