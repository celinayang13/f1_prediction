"""
Streamlit dashboard entrypoint (DASH-01, DASH-02, DASH-04).

Run with: `streamlit run src/f1_prediction/dashboard.py`

Two-tab layout — "Pre-Race" (this plan) and "In-Race" (placeholder, wired in
a later plan). Read-only against SQLite (D-06): dashboard.py and
dashboard_lib/ never INSERT/UPDATE/DELETE. Binds to localhost only by
default (never pass a --server.address flag that widens the bind beyond
localhost) so DASH-04's no-auth design stays safe (T-03-02).
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from f1_prediction.dashboard_lib.charts import build_win_prob_bar_chart
from f1_prediction.dashboard_lib.queries import (
    get_latest_race_session_key,
    get_race_display_name,
    load_inrace_predictions,
    load_prerace_predictions,
)


def _format_time(iso_str: str) -> str:
    """Format an ISO 8601 timestamp string as HH:MM:SS, tolerating parse failures."""
    try:
        return datetime.fromisoformat(iso_str).strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return "--:--:--"


def render_prerace_tab(session_key: int) -> None:
    """
    Render the Pre-Race tab: ranked, team-colored win-probability bar chart.

    Renders once per page load (no auto-refresh fragment) — pre-race
    probabilities are static once qualifying/practice data lands (D-08).
    """
    st.subheader("Pre-Race Win Probability")

    df = load_prerace_predictions(session_key)

    if df.empty:
        st.info(
            "**No qualifying data yet**\n\n"
            "Pre-race probabilities appear once qualifying session data is "
            "available for this race. Run the backfill or wait for the "
            "poller to ingest qualifying results."
        )
        return

    chart_df = df.rename(columns={"full_name": "label"})
    fig = build_win_prob_bar_chart(chart_df)
    st.plotly_chart(fig, use_container_width=True)

    latest_computed_at = df["computed_at"].max()
    st.caption(f"Pre-race · computed {_format_time(latest_computed_at)}")


@st.fragment(run_every=15)
def render_inrace_tab(session_key: int) -> None:
    """
    Render the In-Race tab: LIVE badge, freshness line, team-colored bar
    chart, and a side table (position/tyre/age/gap) — auto-refreshes every
    15 seconds via st.fragment (D-07). No manual refresh control of any kind
    is rendered (D-08). Dashboard remains strictly read-only (D-06).
    """
    st.subheader("In-Race Win Probability")

    df = load_inrace_predictions(session_key)

    if df.empty:
        st.info(
            "**Race hasn't started**\n\n"
            "In-race probabilities begin updating once the first lap is "
            "recorded. This tab refreshes automatically every 15 seconds — "
            "no action needed."
        )
        return

    lap_number = int(df["lap_number"].iloc[0])
    latest_computed_at = df["computed_at"].max()

    st.markdown(
        '<span style="background-color:#E10600; color:white; font-weight:600; '
        'padding:2px 8px; border-radius:4px;">LIVE</span>',
        unsafe_allow_html=True,
    )
    st.caption(f"LIVE · Lap {lap_number} · updated {_format_time(latest_computed_at)}")

    chart_df = df.rename(columns={"full_name": "label"})
    fig = build_win_prob_bar_chart(chart_df)
    st.plotly_chart(fig, use_container_width=True)

    side_table = df[
        ["full_name", "position", "tyre_compound", "tyre_age_laps", "gap_to_leader"]
    ].rename(
        columns={
            "full_name": "Driver",
            "position": "Position",
            "tyre_compound": "Tyre",
            "tyre_age_laps": "Tyre Age (laps)",
            "gap_to_leader": "Gap to Leader (s)",
        }
    )
    st.dataframe(side_table, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="F1 Win Probability", layout="wide")

    session_key = get_latest_race_session_key()
    if session_key is None:
        st.title("F1 Win Probability")
        st.error(
            "No race session found in the database. Run `scripts/backfill.py` "
            "or start the live poller, then relaunch the pipeline."
        )
        return

    race_display_name = get_race_display_name(session_key) or f"Session {session_key}"
    st.title(f"F1 Win Probability — {race_display_name}")

    tab_prerace, tab_inrace = st.tabs(["Pre-Race", "In-Race"])

    with tab_prerace:
        render_prerace_tab(session_key)

    with tab_inrace:
        render_inrace_tab(session_key)


if __name__ == "__main__":
    main()
