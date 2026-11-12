"""
Field-choice win model (conditional logit).

A race is one choice among the field: each driver gets a score x_i . beta
and P(i wins) = exp(score_i) / sum over the field of exp(score_j). The
model is trained directly on the log of the probability given to the
actual winner, so its output is a probability distribution by
construction — no class re-weighting, calibration step or rescaling.

With ~90 races of history a model with a handful of coefficients
generalizes; the gradient-boosted classifier it replaced scored 91% top
pick on races it had seen and 51% on races it had not.

The input builders below are the single source of truth for both training
(scripts/train.py) and inference — a column computed differently on the
two sides would silently corrupt predictions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.model_selection import GroupKFold

PRERACE_INPUTS = ["log_grid", "recent_quali_delta"]

INRACE_INPUTS = [
    "log_pos", "log_gap", "log_pos_x_prog", "log_gap_x_prog",
    "is_leader", "is_leader_x_prog",
    "pit_rel", "pit_rel_x_prog",
    "log_gap_per_lap_left",
    "log_grid", "log_grid_x_prog",
    "tyre_age_rel", "tyre_age_rel_x_prog",
    "recent_quali_delta", "recent_quali_delta_x_prog",
]

_L2_GRID = (1e-3, 1e-2, 1e-1, 1.0)


def prerace_inputs(df: pd.DataFrame) -> pd.DataFrame:
    """Model inputs for one or more pre-race fields. Needs grid_position, recent_quali_delta."""
    out = pd.DataFrame(index=df.index)
    out["log_grid"] = np.log(df["grid_position"].astype(float))
    out["recent_quali_delta"] = df["recent_quali_delta"].astype(float)
    return out[PRERACE_INPUTS]


def inrace_inputs(df: pd.DataFrame) -> pd.DataFrame:
    """
    Model inputs for in-race fields: a driver's state relative to the field
    on that lap, each also interacted with race progress (0 at the start,
    1 at the flag) because the same gap means more with fewer laps left.

    Needs session_key, lap_number, laps_remaining, position, gap_to_leader,
    pit_stop_count, tyre_age_laps, grid_position, recent_quali_delta.
    """
    field = [df["session_key"], df["lap_number"]]
    progress = df["lap_number"] / (df["lap_number"] + df["laps_remaining"])
    gap = df["gap_to_leader"].astype(float).clip(upper=90.0)  # beyond ~a lap down the size no longer matters

    out = pd.DataFrame(index=df.index)
    out["log_pos"] = np.log(df["position"].astype(float))
    out["log_gap"] = np.log1p(gap)
    out["is_leader"] = (df["position"] == 1).astype(float)
    # stops made relative to the typical car: negative = still has a stop to make
    pits = df["pit_stop_count"].astype(float)
    out["pit_rel"] = pits - pits.groupby(field).transform("median")
    out["log_gap_per_lap_left"] = np.log1p((gap / (df["laps_remaining"] + 1)).clip(upper=30.0))
    out["log_grid"] = np.log(df["grid_position"].astype(float))
    tyre_age = df["tyre_age_laps"].astype(float)
    out["tyre_age_rel"] = tyre_age - tyre_age.groupby(field).transform("median")
    out["recent_quali_delta"] = df["recent_quali_delta"].astype(float)
    for col in ["log_pos", "log_gap", "is_leader", "pit_rel", "log_grid", "tyre_age_rel", "recent_quali_delta"]:
        out[f"{col}_x_prog"] = out[col] * progress
    return out[INRACE_INPUTS]


_INPUT_BUILDERS = {"prerace": (prerace_inputs, ["session_key"]), "inrace": (inrace_inputs, ["session_key", "lap_number"])}


def _field_index(field_ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Start offset of each field and each row's field number, for rows sorted by field."""
    new_field = np.r_[True, field_ids[1:] != field_ids[:-1]]
    return np.flatnonzero(new_field), np.cumsum(new_field) - 1


def _field_softmax(scores: np.ndarray, starts: np.ndarray, field_of_row: np.ndarray) -> np.ndarray:
    e = np.exp(scores - np.maximum.reduceat(scores, starts)[field_of_row])
    return e / np.add.reduceat(e, starts)[field_of_row]


def _fit(X: np.ndarray, y: np.ndarray, field_ids: np.ndarray, l2: float) -> np.ndarray:
    """Maximize the winners' log probability (L2-penalized). Rows must be sorted by field."""
    starts, field_of_row = _field_index(field_ids)

    def loss_and_grad(beta):
        p = _field_softmax(X @ beta, starts, field_of_row)
        loss = -np.log(np.clip(p[y == 1], 1e-300, None)).sum() / len(starts) + l2 * beta @ beta
        return loss, X.T @ (p - y) / len(starts) + 2 * l2 * beta

    return minimize(loss_and_grad, np.zeros(X.shape[1]), jac=True, method="L-BFGS-B").x


class FieldChoiceModel:
    """Fitted conditional-logit win model for one prediction kind ('prerace' or 'inrace')."""

    def __init__(self, kind: str):
        if kind not in _INPUT_BUILDERS:
            raise ValueError(f"kind must be 'prerace' or 'inrace', got {kind!r}")
        self.kind = kind

    def _matrix(self, df: pd.DataFrame) -> np.ndarray:
        builder, _ = _INPUT_BUILDERS[self.kind]
        # a missing input is set to the training mean, i.e. contributes nothing
        return ((builder(df) - self.mean_) / self.scale_).fillna(0.0).to_numpy(float)

    def fit(self, df: pd.DataFrame, cv: int = 4) -> "FieldChoiceModel":
        """
        Fit on rows with an 'is_winner' column. Fields without exactly one
        winner are ignored. The L2 penalty is chosen by cross-validation
        that keeps whole races together.
        """
        builder, keys = _INPUT_BUILDERS[self.kind]
        df = df.reset_index(drop=True)
        # inputs are computed on the complete fields (pit_rel etc. are field-relative) before filtering
        inputs = builder(df)
        keep = df.groupby(keys)["is_winner"].transform("sum") == 1
        if not keep.any():
            raise ValueError("No field with exactly one winner in the training data.")
        order = df[keep].sort_values(keys).index
        inputs, df = inputs.loc[order], df.loc[order]

        self.columns_ = list(inputs.columns)
        self.mean_ = inputs.mean()
        self.scale_ = inputs.std().replace(0, 1.0).fillna(1.0)
        X = ((inputs - self.mean_) / self.scale_).fillna(0.0).to_numpy(float)
        y = df["is_winner"].to_numpy(float)
        field_ids = df.groupby(keys, sort=False).ngroup().to_numpy()
        races = df["session_key"].to_numpy()

        self.l2_ = _L2_GRID[1]
        if len(np.unique(races)) >= max(cv, 2):
            cv_loss = {}
            for l2 in _L2_GRID:
                total = 0.0
                for train_idx, val_idx in GroupKFold(cv).split(X, groups=races):
                    beta = _fit(X[train_idx], y[train_idx], field_ids[train_idx], l2)
                    starts, field_of_row = _field_index(field_ids[val_idx])
                    p = _field_softmax(X[val_idx] @ beta, starts, field_of_row)
                    total += -np.log(np.clip(p[y[val_idx] == 1], 1e-12, None)).mean()
                cv_loss[l2] = total
            self.l2_ = min(cv_loss, key=cv_loss.get)
        self.coef_ = _fit(X, y, field_ids, self.l2_)
        self.n_fields_ = int(len(np.unique(field_ids)))
        return self

    def scores(self, df: pd.DataFrame) -> np.ndarray:
        """
        Positive strength per row. Dividing by the sum over a field gives the
        win probabilities (inference/features.py::normalize_probs does this).
        """
        return np.exp(np.clip(self._matrix(df) @ self.coef_, -50, 50))

    def coefficients(self) -> dict[str, float]:
        """Coefficient per standardized input — positive raises the win probability."""
        return {c: round(float(b), 4) for c, b in zip(self.columns_, self.coef_)}
