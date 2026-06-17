"""
In-race feature computation (D-03).

Produces one row per (driver_number, lap_number) for a given Race session
with all features_inrace columns computed from raw_laps, raw_positions,
raw_stints, and raw_pit_stops.

Approximations (documented per plan):
  gap_to_leader:  cumulative sum of lap_duration per driver; gap vs leader's cumsum.
                  This is the standard F1 timing-screen interpretation for historical data.
                  Live race: OpenF1 /v1/intervals provides the actual value.
  interval_ahead: gap_to_leader[this driver] - gap_to_leader[driver at position-1] (same lap).
  interval_behind:gap_to_leader[driver at position+1] - gap_to_leader[this driver] (same lap).
  grid_position:  taken from lap-1 raw_positions (see prerace.compute_grid_position).

Security: All SQL uses text() with bound :param names — no f-string SQL (T-03-05).
"""
from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)


def compute_inrace_features_for_session(session_key: int, conn) -> pd.DataFrame:
    """
    Compute all features_inrace rows for a race session.

    Joins raw_laps + raw_positions + raw_stints + raw_pit_stops per
    (driver_number, lap_number). Returns a DataFrame with columns matching
    FeaturesInrace exactly, ready for DELETE-then-bulk-INSERT.

    Args:
        session_key: Race session_key.
        conn: SQLAlchemy connection.

    Returns:
        DataFrame with columns:
          session_key, driver_number, lap_number, gap_to_leader,
          tyre_compound, tyre_age_laps, pit_stop_count, position,
          interval_ahead, interval_behind, pit_stop_delta, laps_remaining
        Empty DataFrame if no lap data exists for this session.
    """
    # ------------------------------------------------------------------
    # 1. Load raw_laps
    # ------------------------------------------------------------------
    laps_q = text(
        """
        SELECT driver_number, lap_number, lap_duration
        FROM raw_laps
        WHERE session_key = :sk
          AND lap_duration IS NOT NULL
        ORDER BY driver_number, lap_number
        """
    )
    laps_df = pd.read_sql(laps_q, conn, params={"sk": session_key})
    if laps_df.empty:
        return pd.DataFrame(
            columns=[
                "session_key", "driver_number", "lap_number", "gap_to_leader",
                "tyre_compound", "tyre_age_laps", "pit_stop_count", "position",
                "interval_ahead", "interval_behind", "pit_stop_delta", "laps_remaining",
            ]
        )

    # ------------------------------------------------------------------
    # 2. Load raw_positions
    # ------------------------------------------------------------------
    pos_q = text(
        """
        SELECT driver_number, lap_number, position
        FROM raw_positions
        WHERE session_key = :sk
        ORDER BY driver_number, lap_number
        """
    )
    pos_df = pd.read_sql(pos_q, conn, params={"sk": session_key})

    # ------------------------------------------------------------------
    # 3. Load raw_stints
    # ------------------------------------------------------------------
    stints_q = text(
        """
        SELECT driver_number, lap_start, lap_end, compound, tyre_age_at_start
        FROM raw_stints
        WHERE session_key = :sk
        ORDER BY driver_number, lap_start
        """
    )
    stints_df = pd.read_sql(stints_q, conn, params={"sk": session_key})

    # ------------------------------------------------------------------
    # 4. Load raw_pit_stops
    # ------------------------------------------------------------------
    pits_q = text(
        """
        SELECT driver_number, lap_number AS pit_lap, lane_duration
        FROM raw_pit_stops
        WHERE session_key = :sk
        ORDER BY driver_number, lap_number
        """
    )
    pits_df = pd.read_sql(pits_q, conn, params={"sk": session_key})

    # ------------------------------------------------------------------
    # 5. Load total_laps from raw_sessions
    # ------------------------------------------------------------------
    total_laps_q = text(
        "SELECT total_laps FROM raw_sessions WHERE session_key = :sk"
    )
    total_laps_row = conn.execute(total_laps_q, {"sk": session_key}).fetchone()
    total_laps = int(total_laps_row[0]) if (total_laps_row and total_laps_row[0] is not None) else None
    # Fallback: use MAX(lap_number) from this session's laps if total_laps not stored
    if total_laps is None and not laps_df.empty:
        total_laps = int(laps_df["lap_number"].max())

    # ------------------------------------------------------------------
    # 6. Compute gap_to_leader via cumulative lap times
    # ------------------------------------------------------------------
    laps_df = laps_df.sort_values(["driver_number", "lap_number"]).copy()
    laps_df["cumsum_lap"] = laps_df.groupby("driver_number")["lap_duration"].cumsum()

    # For each lap_number, the leader's cumsum (minimum cumsum across all drivers)
    leader_cumsum = (
        laps_df.groupby("lap_number")["cumsum_lap"].min().rename("leader_cumsum")
    )
    laps_df = laps_df.merge(leader_cumsum, on="lap_number", how="left")
    laps_df["gap_to_leader"] = laps_df["cumsum_lap"] - laps_df["leader_cumsum"]

    # ------------------------------------------------------------------
    # 7. Merge position data
    # ------------------------------------------------------------------
    if not pos_df.empty:
        # Use the last recorded position per (driver, lap) in case of duplicates
        pos_deduped = pos_df.drop_duplicates(subset=["driver_number", "lap_number"], keep="last")
        laps_df = laps_df.merge(
            pos_deduped[["driver_number", "lap_number", "position"]],
            on=["driver_number", "lap_number"],
            how="left",
        )
    else:
        laps_df["position"] = None

    # ------------------------------------------------------------------
    # 8. Compute interval_ahead and interval_behind from gap_to_leader
    # ------------------------------------------------------------------
    # Create a wide pivot: gap_to_leader per (lap_number, driver_number)
    if not laps_df["position"].isna().all():
        gap_by_pos = laps_df[["lap_number", "driver_number", "gap_to_leader", "position"]].copy()
        gap_by_pos = gap_by_pos.dropna(subset=["position"])
        gap_by_pos["position"] = gap_by_pos["position"].astype(int)

        # For interval_ahead: gap_to_leader of car at position-1 (closer to leader)
        gap_ahead = gap_by_pos[["lap_number", "position", "gap_to_leader"]].copy()
        gap_ahead["position_plus1"] = gap_ahead["position"] + 1
        gap_ahead = gap_ahead.rename(columns={"gap_to_leader": "gap_ahead_leader"})

        # Merge: for each (driver, lap) with position P, join car at position P-1
        laps_df = laps_df.merge(
            gap_by_pos[["lap_number", "position", "gap_to_leader"]].rename(
                columns={"position": "pos_ahead", "gap_to_leader": "gap_ahead_val"}
            ),
            left_on=["lap_number", "position"],
            right_on=["lap_number", "pos_ahead"],
            how="left",
        )
        # interval_ahead = leader's gap minus this car's gap (leader at position 1 has 0)
        # Actually: interval_ahead = this car's gap - car_ahead's gap (car ahead has smaller gap)
        laps_df["pos_ahead_adj"] = laps_df["position"].astype("Float64") - 1
        laps_df = laps_df.merge(
            gap_by_pos[["lap_number", "position", "gap_to_leader"]].rename(
                columns={"position": "pos_m1", "gap_to_leader": "gap_m1"}
            ),
            left_on=["lap_number", "pos_ahead_adj"],
            right_on=["lap_number", "pos_m1"],
            how="left",
        )
        laps_df["interval_ahead"] = laps_df["gap_to_leader"] - laps_df["gap_m1"]

        # interval_behind = gap of car at position+1 minus this car's gap
        laps_df["pos_p1"] = laps_df["position"].astype("Float64") + 1
        laps_df = laps_df.merge(
            gap_by_pos[["lap_number", "position", "gap_to_leader"]].rename(
                columns={"position": "pos_p1_key", "gap_to_leader": "gap_p1"}
            ),
            left_on=["lap_number", "pos_p1"],
            right_on=["lap_number", "pos_p1_key"],
            how="left",
        )
        laps_df["interval_behind"] = laps_df["gap_p1"] - laps_df["gap_to_leader"]
    else:
        laps_df["interval_ahead"] = None
        laps_df["interval_behind"] = None

    # ------------------------------------------------------------------
    # 9. Merge tyre data from raw_stints
    # ------------------------------------------------------------------
    if not stints_df.empty:
        # For each (driver, lap), find the matching stint
        def _get_tyre_info(driver_number: int, lap_number: int):
            """Return (compound, tyre_age_laps) for this driver at this lap."""
            driver_stints = stints_df[stints_df["driver_number"] == driver_number]
            matching = driver_stints[
                (driver_stints["lap_start"] <= lap_number)
                & (driver_stints["lap_end"] >= lap_number)
            ]
            if matching.empty:
                return None, None
            stint = matching.iloc[0]
            compound = stint["compound"]
            age_at_start = stint["tyre_age_at_start"]
            if age_at_start is not None and not pd.isna(age_at_start):
                tyre_age = int(lap_number - stint["lap_start"]) + int(age_at_start)
            else:
                tyre_age = int(lap_number - stint["lap_start"])
            return compound, tyre_age

        tyre_rows = laps_df.apply(
            lambda row: pd.Series(
                _get_tyre_info(int(row["driver_number"]), int(row["lap_number"])),
                index=["tyre_compound", "tyre_age_laps"],
            ),
            axis=1,
        )
        laps_df["tyre_compound"] = tyre_rows["tyre_compound"]
        laps_df["tyre_age_laps"] = tyre_rows["tyre_age_laps"]
    else:
        laps_df["tyre_compound"] = None
        laps_df["tyre_age_laps"] = None

    # ------------------------------------------------------------------
    # 10. Compute pit_stop_count and pit_stop_delta from raw_pit_stops
    # ------------------------------------------------------------------
    if not pits_df.empty:
        def _pit_count(driver_number: int, lap_number: int) -> int:
            driver_pits = pits_df[pits_df["driver_number"] == driver_number]
            return int((driver_pits["pit_lap"] <= lap_number).sum())

        def _pit_delta(driver_number: int, lap_number: int):
            """Return lane_duration if this driver pitted on this exact lap."""
            driver_pits = pits_df[pits_df["driver_number"] == driver_number]
            this_pit = driver_pits[driver_pits["pit_lap"] == lap_number]
            if this_pit.empty:
                return None
            val = this_pit.iloc[0]["lane_duration"]
            return float(val) if val is not None and not pd.isna(val) else None

        laps_df["pit_stop_count"] = laps_df.apply(
            lambda row: _pit_count(int(row["driver_number"]), int(row["lap_number"])),
            axis=1,
        )
        laps_df["pit_stop_delta"] = laps_df.apply(
            lambda row: _pit_delta(int(row["driver_number"]), int(row["lap_number"])),
            axis=1,
        )
    else:
        laps_df["pit_stop_count"] = 0
        laps_df["pit_stop_delta"] = None

    # ------------------------------------------------------------------
    # 11. Compute laps_remaining (Pitfall 8)
    # ------------------------------------------------------------------
    if total_laps is not None:
        laps_df["laps_remaining"] = total_laps - laps_df["lap_number"]
    else:
        laps_df["laps_remaining"] = None

    # ------------------------------------------------------------------
    # 12. Assemble output DataFrame with exact column set
    # ------------------------------------------------------------------
    laps_df["session_key"] = session_key

    output_cols = [
        "session_key", "driver_number", "lap_number", "gap_to_leader",
        "tyre_compound", "tyre_age_laps", "pit_stop_count", "position",
        "interval_ahead", "interval_behind", "pit_stop_delta", "laps_remaining",
    ]

    result = laps_df[output_cols].copy()

    # Type coercion for integer columns — allow NaN (use Float64 / Int64 nullable)
    for col in ["driver_number", "lap_number", "pit_stop_count"]:
        result[col] = result[col].astype(int)
    for col in ["tyre_age_laps", "position", "laps_remaining"]:
        result[col] = pd.array(result[col], dtype=pd.Int64Dtype())

    return result
