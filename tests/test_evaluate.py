"""Tests for scripts/evaluate.py scoring helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from evaluate import frequency_baseline, score_fields  # noqa: E402


def _field(session_key: int, winner_idx: int, n: int = 4) -> pd.DataFrame:
    return pd.DataFrame({
        "session_key": session_key,
        "is_winner": [int(i == winner_idx) for i in range(n)],
    })


def test_score_fields_known_values():
    df = pd.concat([_field(1, 0), _field(2, 3)], ignore_index=True)

    # Uniform over 4 drivers: brier = 3*(1/4)^2 + (3/4)^2 = 0.75, nll = ln 4
    uniform = score_fields(df, pd.Series(1.0, index=df.index), ["session_key"])
    assert uniform["brier"].tolist() == pytest.approx([0.75, 0.75])
    assert uniform["nll"].tolist() == pytest.approx([np.log(4)] * 2)
    assert uniform["top1"].tolist() == pytest.approx([0.25, 0.25])  # ties share the credit

    # Unnormalized scores are normalized per field; race 1 right, race 2 wrong and 0% on the winner
    scores = pd.Series([5.0, 0, 0, 0, 2.0, 0, 0, 0], index=df.index)
    out = score_fields(df, scores, ["session_key"])
    assert out.loc[1, ["brier", "nll", "top1", "zero_winner"]].tolist() == pytest.approx([0, 0, 1, 0])
    assert out.loc[2, ["brier", "top1", "zero_winner"]].tolist() == pytest.approx([2, 0, 1])
    assert np.isfinite(out.loc[2, "nll"])


def test_score_fields_drops_fields_without_single_winner():
    df = pd.concat([_field(1, 0), _field(2, 99)], ignore_index=True)  # race 2 has no winner
    out = score_fields(df, pd.Series(1.0, index=df.index), ["session_key"])
    assert out.index.tolist() == [1]


def test_frequency_baseline_learns_from_train_only_and_never_zero():
    train = pd.DataFrame({"grid_position": [1, 1, 2, 2], "is_winner": [1, 1, 0, 0]})
    test = pd.DataFrame({"grid_position": [1, 2, 7], "is_winner": [0, 1, 0]}, index=[10, 11, 12])
    p = frequency_baseline(train, test, ["grid_position"])
    assert p.index.tolist() == [10, 11, 12]
    assert p[10] > p[11] > 0
    assert p[12] == p[11]  # unseen grid slot gets the lowest observed rate
