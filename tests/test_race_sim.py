"""Tests for the experimental Monte Carlo race simulator."""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from f1_prediction.simulation.race_sim import SimParams, add_pace_columns, simulate_win_probs

# no randomness from safety cars or retirements unless a test asks for it
QUIET = SimParams(safety_car_per_lap=0.0, dnf_per_lap=0.0, tyre_deg=0.0, pit_hazard=np.zeros((8, 6, 3)).tolist())


def _state(gaps, stops, laps_remaining=20, pace=None):
    n = len(gaps)
    return pd.DataFrame({
        "driver_number": range(1, n + 1), "gap": gaps, "tyre_age": 5.0, "stops": stops,
        "laps_remaining": laps_remaining, "pace_mean": pace or [0.0] * n, "pace_sd": 0.1,
        "standing_start": False,
    })


def test_probabilities_sum_to_one_and_are_repeatable():
    state = _state([0.0, 2.0, 5.0, 9.0], [1, 1, 1, 1])
    a = simulate_win_probs(state, QUIET, n_sims=500, seed=1)
    b = simulate_win_probs(state, QUIET, n_sims=500, seed=1)
    assert a.sum() == pytest.approx(1.0)
    assert a.tolist() == b.tolist()
    assert a.index.tolist() == [1, 2, 3, 4]


def test_big_lead_with_equal_pace_wins():
    probs = simulate_win_probs(_state([0.0, 30.0, 35.0], [1, 1, 1]), QUIET, n_sims=500)
    assert probs[1] > 0.95


def test_leader_who_still_has_to_stop_loses_to_a_close_follower():
    # car 1 leads by 5s but has not stopped; a stop costs far more than 5s
    probs = simulate_win_probs(_state([0.0, 5.0], [0, 1]), QUIET, n_sims=500)
    assert probs[2] > 0.9


def test_faster_car_catches_up_over_a_long_stint():
    slow_leader = _state([0.0, 8.0], [1, 1], laps_remaining=40, pace=[0.0, -1.0])
    assert simulate_win_probs(slow_leader, QUIET, n_sims=500)[2] > 0.5


def test_retirements_spread_the_outcome():
    risky = dataclasses.replace(QUIET, dnf_per_lap=0.05)
    probs = simulate_win_probs(_state([0.0, 30.0, 35.0], [1, 1, 1]), risky, n_sims=1000)
    assert 0.4 < probs[1] < 0.95 and probs[2] > 0.05


def test_add_pace_columns_ignores_pit_and_first_laps():
    laps = pd.DataFrame({
        "session_key": 1,
        "driver_number": [1] * 5 + [2] * 5,
        "lap_number": list(range(1, 6)) * 2,
        "lap_duration": [95.0, 90.0, 90.0, 112.0, 91.0, 96.0, 91.0, 91.0, 91.0, 91.0],
        "pit_stop_count": [0, 0, 0, 1, 1, 0, 0, 0, 0, 0],
    })
    out = add_pace_columns(laps).set_index(["driver_number", "lap_number"])
    assert bool(out.loc[(1, 4), "pitted"])
    assert out.loc[(1, [1, 4, 5]), "rel_pace"].isna().all()       # lap 1, in-lap, out-lap
    assert out.loc[(1, 3), "obs_pace"] == pytest.approx(-0.5)     # half a second quicker than the lap median
    assert out.loc[(1, 5), "obs_laps"] == 2                       # carried forward through the stop


def test_older_tyres_cost_time_when_wear_is_on():
    worn = dataclasses.replace(QUIET, tyre_deg=0.3)
    state = _state([0.0, 3.0], [1, 1], laps_remaining=25)
    state["tyre_age"] = [30.0, 2.0]          # leader on much older tyres
    assert simulate_win_probs(state, worn, n_sims=500)[2] > 0.8
    assert simulate_win_probs(state, QUIET, n_sims=500)[2] < 0.5
