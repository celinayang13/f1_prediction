"""
Plotly chart builders for the F1 dashboard (DASH-01, D-05).

No existing charting code elsewhere in this codebase — this is the first
Plotly usage in the project. Team colors come from OpenF1 team_colour
(bare hex, no '#' prefix); Plotly's marker_color requires the '#' prefix
(Pitfall 3), which is applied here at render time.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

__all__ = ["build_win_prob_bar_chart"]


def build_win_prob_bar_chart(df: pd.DataFrame) -> go.Figure:
    """
    Build a horizontal, team-colored win-probability bar chart.

    Args:
        df: DataFrame with columns 'label' (driver display name),
            'win_probability' (float, 0-1), and 'team_colour' (bare hex
            string, no '#' prefix).

    Returns:
        A Plotly Figure with a single horizontal go.Bar trace, sorted so
        the highest win probability renders at the top.
    """
    # Plotly renders horizontal bar categories bottom-to-top, so sort
    # ascending by win_probability to put the highest probability on top.
    sorted_df = df.sort_values("win_probability", ascending=True)

    marker_color = [f"#{c}" for c in sorted_df["team_colour"]]

    fig = go.Figure(
        data=[
            go.Bar(
                orientation="h",
                x=sorted_df["win_probability"],
                y=sorted_df["label"],
                marker_color=marker_color,
                text=[f"{p:.1%}" for p in sorted_df["win_probability"]],
                textposition="outside",
                hovertemplate="%{y}: %{x:.1%}<extra></extra>",
            )
        ]
    )
    fig.update_layout(
        xaxis_title="Win Probability",
        yaxis_title=None,
        xaxis=dict(tickformat=".0%", range=[0, max(1.0, sorted_df["win_probability"].max() * 1.15)]),
        template="plotly_dark",
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False,
    )
    return fig
