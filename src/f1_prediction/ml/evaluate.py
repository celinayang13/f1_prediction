"""
Evaluation metrics for the F1 win probability models (Plan 02-01).

Provides three evaluation utilities:
  - compute_log_loss: log loss on held-out test set
  - per_session_softmax: per-session normalized win probability distribution
  - compute_top3_hit_rate: fraction of sessions where winner is in top-3 predicted

Security: No SQL queries in this module. Pure pandas/scipy/sklearn computation.
"""
from __future__ import annotations

import logging

import pandas as pd
from scipy.special import softmax
from sklearn.metrics import log_loss as _sklearn_log_loss

logger = logging.getLogger(__name__)


def compute_log_loss(y_true, win_proba) -> float:
    """
    Compute log loss between true binary labels and predicted win probabilities.

    Thin wrapper over sklearn.metrics.log_loss that returns a float.

    Args:
        y_true: Array-like of binary labels (1 = winner, 0 = non-winner).
        win_proba: Array-like of predicted win probabilities (column 1 of predict_proba).

    Returns:
        Log loss as a float.
    """
    return float(_sklearn_log_loss(y_true, win_proba, labels=[0, 1]))


def per_session_softmax(
    df: pd.DataFrame, score_col: str, session_col: str
) -> pd.Series:
    """
    Apply softmax normalization per session group.

    For each unique value in session_col, applies scipy.special.softmax
    to the scores in score_col, producing a valid probability distribution
    summing to 1.0 within each session.

    This is the per-session normalization step described in CONTEXT D-10 and
    RESEARCH Pattern 3. Softmax must be applied per session, NOT globally —
    applying globally mixes drivers from different races (RESEARCH Pitfall 4).

    Args:
        df: DataFrame containing score_col and session_col.
        score_col: Column name of raw win scores (pre-softmax).
        session_col: Column name to group by (e.g. 'session_key').

    Returns:
        pd.Series aligned with df.index, containing per-session softmax probabilities.
    """
    return df.groupby(session_col)[score_col].transform(
        lambda g: softmax(g.values)
    )


def compute_top3_hit_rate(
    df: pd.DataFrame,
    prob_col: str,
    label_col: str,
    session_col: str,
) -> tuple[float, int, int]:
    """
    Compute the top-3 hit rate across all sessions.

    For each session, a "hit" is recorded if the winner (label_col == 1) appears
    in the top-3 drivers by predicted probability (prob_col).

    Args:
        df: DataFrame containing prob_col, label_col, and session_col.
        prob_col: Column name of win probabilities (or raw win scores for ranking).
        label_col: Column name of binary win labels (1 = winner, 0 = non-winner).
        session_col: Column name identifying each session.

    Returns:
        Tuple of (rate, hits, total_sessions) where:
          - rate: fraction of sessions where winner appeared in top-3 (float 0.0–1.0)
          - hits: number of sessions with a top-3 hit (int)
          - total_sessions: total number of unique sessions evaluated (int)
    """
    sessions = df[session_col].unique()
    total_sessions = len(sessions)

    if total_sessions == 0:
        logger.warning("compute_top3_hit_rate called with 0 sessions — returning (0.0, 0, 0)")
        return 0.0, 0, 0

    hits = 0
    for sk in sessions:
        session_df = df[df[session_col] == sk]
        top3 = session_df.nlargest(3, prob_col)
        if (top3[label_col] == 1).any():
            hits += 1

    rate = hits / total_sessions
    return rate, hits, total_sessions
