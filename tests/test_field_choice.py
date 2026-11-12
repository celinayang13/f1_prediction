"""Tests for the field-choice (conditional logit) win model."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from f1_prediction.inference.features import predict_normalized
from f1_prediction.inference.field_choice import FieldChoiceModel


def _races(n_races: int, seed: int = 0) -> pd.DataFrame:
    """Synthetic fields of 10 where a better grid slot makes winning more likely."""
    rng = np.random.default_rng(seed)
    rows = []
    for race in range(n_races):
        grid = np.arange(1, 11)
        strength = np.exp(-1.5 * np.log(grid))
        winner = rng.choice(10, p=strength / strength.sum())
        for i in range(10):
            rows.append({
                "session_key": race, "driver_number": i, "grid_position": grid[i],
                "recent_quali_delta": rng.normal(1.0, 0.3), "is_winner": int(i == winner),
            })
    return pd.DataFrame(rows)


def test_field_choice_learns_grid_effect_and_sums_to_one():
    df = _races(200)
    model = FieldChoiceModel("prerace").fit(df)

    assert model.coefficients()["log_grid"] < 0          # worse grid slot, lower chance
    assert abs(model.coefficients()["recent_quali_delta"]) < 0.3  # pure noise here

    result = predict_normalized(model, df, [])
    assert result.groupby("session_key")["win_prob"].sum().to_numpy() == pytest.approx(1.0)
    pole = result[result["grid_position"] == 1]["win_prob"].mean()
    assert 0.3 < pole < 0.6   # true pole win rate under the generator is ~0.45


def test_field_choice_tolerates_missing_inputs_and_bad_fields():
    df = _races(30)
    df.loc[df.index[::7], "recent_quali_delta"] = np.nan    # missing input -> training mean
    df.loc[df["session_key"] == 0, "is_winner"] = 0          # a race with no winner is ignored
    model = FieldChoiceModel("prerace").fit(df, cv=2)
    assert model.n_fields_ == 29
    assert np.isfinite(model.scores(df)).all()


def test_field_choice_rejects_unknown_kind():
    with pytest.raises(ValueError):
        FieldChoiceModel("podium")
