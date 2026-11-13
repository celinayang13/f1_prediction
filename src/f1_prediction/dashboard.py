"""
Streamlit dashboard entrypoint (DASH-01, DASH-02, DASH-04).

Run with: `streamlit run src/f1_prediction/dashboard.py`

Two-tab layout — "Pre-Race" and "In-Race" — for the race picked in the
sidebar (most recent by default). Read-only against SQLite (D-06): dashboard.py and
dashboard_lib/ never INSERT/UPDATE/DELETE. Binds to localhost only by
default (never pass a --server.address flag that widens the bind beyond
localhost) so DASH-04's no-auth design stays safe (T-03-02).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from f1_prediction.dashboard_lib.charts import build_win_prob_bar_chart
from f1_prediction.dashboard_lib.queries import (
    get_official_winner,
    get_race_display_name,
    get_session_window,
    list_races,
    load_inrace_predictions,
    load_prerace_predictions,
)
from f1_prediction.dashboard_lib.status import (
    FINISHED,
    PRE_RACE,
    STATUS_COLOURS,
    parse_utc,
    session_status,
)

_MODELS_DIR = Path(__file__).resolve().parents[2] / "data" / "models"


def _format_time(iso_str: str) -> str:
    """Format an ISO 8601 timestamp as a UTC date and time, tolerating parse failures."""
    parsed = parse_utc(iso_str)
    if parsed is None:
        return "unknown time"
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _status_badge(status: str) -> None:
    st.markdown(
        f'<span style="background-color:{STATUS_COLOURS[status]}; color:white; font-weight:600; '
        f'padding:2px 8px; border-radius:4px;">{status}</span>',
        unsafe_allow_html=True,
    )


def _model_caption(kind: str) -> None:
    """Show which model produced the numbers; they are experimental until validated on new races."""
    try:
        meta = json.loads((_MODELS_DIR / f"{kind}_model.meta.json").read_text())
    except (OSError, ValueError):
        st.caption("Experimental model · version unknown (no model metadata found)")
        return
    years = meta.get("train_years") or []
    span = f"{years[0]}–{years[-1]}" if years else "unknown seasons"
    st.caption(
        f"Experimental model · {meta.get('algorithm', 'unknown')} · trained on {span} "
        f"({str(meta.get('trained_at', ''))[:10]}) · revision {meta.get('code_revision', 'unknown')}"
    )


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
    st.plotly_chart(fig, width="stretch")

    latest_computed_at = df["computed_at"].max()
    st.caption(f"Pre-race · computed {_format_time(latest_computed_at)}")
    _model_caption("prerace")


@st.fragment(run_every=15)
def render_inrace_tab(session_key: int) -> None:
    """
    Render the In-Race tab: status badge (PRE-RACE / LIVE / STALE /
    FINISHED), freshness line, team-colored bar chart, and a side table
    (position/tyre/age/gap) — auto-refreshes every 15 seconds via
    st.fragment (D-07), which also re-evaluates the status. No manual
    refresh control of any kind is rendered (D-08). Dashboard remains
    strictly read-only (D-06).
    """
    st.subheader("In-Race Win Probability")

    df = load_inrace_predictions(session_key)
    date_start, date_end = get_session_window(session_key)
    latest_computed_at = df["computed_at"].max() if not df.empty else None
    status = session_status(date_start, date_end, latest_computed_at)
    _status_badge(status)

    if df.empty:
        st.info(
            "**No in-race predictions yet**\n\n"
            + (
                f"The race is scheduled to start at {_format_time(date_start)}. "
                if status == PRE_RACE else ""
            )
            + "In-race probabilities appear once the first lap is recorded. "
            "This tab refreshes automatically every 15 seconds."
        )
        return

    lap_number = int(df["lap_number"].iloc[0])
    if status == FINISHED:
        winner = get_official_winner(session_key)
        st.caption(
            (f"Official winner: {winner} · " if winner else "Official result not stored · ")
            + f"showing the model's last estimate, after lap {lap_number}"
        )
    else:
        st.caption(f"{status} · Lap {lap_number} · computed {_format_time(latest_computed_at)}")

    chart_df = df.rename(columns={"full_name": "label"})
    fig = build_win_prob_bar_chart(chart_df)
    st.plotly_chart(fig, width="stretch")

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
    st.dataframe(side_table, width="stretch", hide_index=True)
    _model_caption("inrace")


def main() -> None:
    st.set_page_config(page_title="F1 Win Probability", layout="wide")

    races = list_races()
    if races.empty:
        st.title("F1 Win Probability")
        st.error(
            "No race session found in the database. Run `scripts/backfill.py` "
            "or start the live poller, then relaunch the pipeline."
        )
        return

    # Race selector: most recent race first, which is the default
    labels = {
        int(r.session_key): f"{r.year} {r.country_name or 'Unknown'} ({str(r.date_start)[:10]})"
        for r in races.itertuples()
    }
    session_key = st.sidebar.selectbox("Race", list(labels), format_func=labels.get)

    race_display_name = get_race_display_name(session_key) or f"Session {session_key}"
    st.title(f"F1 Win Probability — {race_display_name}")

    tab_prerace, tab_inrace = st.tabs(["Pre-Race", "In-Race"])

    with tab_prerace:
        render_prerace_tab(session_key)

    with tab_inrace:
        render_inrace_tab(session_key)


if __name__ == "__main__":
    main()
