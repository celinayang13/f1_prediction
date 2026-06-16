"""
SQLAlchemy ORM models for F1 prediction raw data tables.

This module defines the declarative Base and two raw-data models:
  - RawSession  (table: raw_sessions)
  - RawLap      (table: raw_laps)

Plans 02 and 03 will extend this module with additional tables
(raw_stints, raw_pit_stops, raw_positions, raw_weather,
features_prerace, features_inrace) without changing these two models.

Schema follows the OpenF1 column naming convention so that FastF1
(2022) and OpenF1 (2023+) data share a single schema. FastF1-specific
columns are dropped at ingest time by the normalizer.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, Float, Index, Integer, String
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
    date_end = Column(String)  # ISO 8601 string; NULL until Plan 02
    total_laps = Column(Integer)  # NULL until populated for completed races (Plan 02)
    source = Column(String, nullable=False)  # 'fastf1' or 'openf1'


class RawLap(Base):
    """
    One row per lap per driver per session.

    Columns mirror the OpenF1 /v1/laps endpoint field names.
    FastF1-specific fields (IsAccurate, Compound, TyreLife, etc.) are
    dropped at normalisation time. Compound/TyreLife will be sourced from
    the raw_stints table (Plan 02 — decision D-04).

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

    __table_args__ = (
        Index(
            "ix_raw_laps_session_driver_lap",
            "session_key",
            "driver_number",
            "lap_number",
        ),
    )
