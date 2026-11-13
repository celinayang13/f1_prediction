"""
Monte Carlo race simulation (experimental).

Plays out the remaining laps of a race many times and reports how often
each driver wins. One engine serves both cases: from the grid (pre-race)
and from any lap of a race in progress.

What a simulated lap does, in order:
  1. Safety car: with a small per-lap probability the field bunches up.
  2. Pit stops: each car may stop, with a probability that depends on tyre
     age, laps remaining and stops already made; a stop costs `pit_loss`.
  3. Lap time: the car's pace relative to the field (uncertain, drawn once
     per simulated race), a penalty for tyres older than the field's, and
     per-lap noise.
  4. Overtaking: a car that would pass the one directly ahead is sometimes
     held up behind it instead.
  5. Retirements: each car has a small per-lap chance of dropping out.

Everything is in seconds relative to the field, so absolute lap time, fuel
load and track length cancel out.

Known gaps (not modelled yet): tyre compound, weather, cheaper pit stops
under a safety car, virtual safety cars, team orders, red flags.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

# Pit-stop probability table buckets
_AGE_EDGES = [0, 5, 10, 15, 20, 25, 30, 40, 200]      # tyre age in laps
_LEFT_EDGES = [0, 3, 8, 15, 25, 40, 200]              # laps remaining
_MAX_STOPS = 2                                        # stops made, capped (0, 1, 2+)

_CLEAN_LAPS_WINDOW = 8     # recent clean laps used for a car's observed pace
_HELD_UP_GAP = 0.3         # seconds behind the car that was not passed
_SC_SPACING = 0.4          # seconds between cars after a safety car bunches the field
_GRID_SLOT_GAP = 0.25      # seconds per grid slot at the start


@dataclass
class SimParams:
    """
    Simulator settings. The first block is estimated from past seasons by
    estimate_params(); the second block is hand-set and not yet tuned.
    """
    # --- estimated from data
    lap_noise_sd: float = 0.6          # lap-to-lap variation in a car's lap time (s)
    quali_to_race: float = 0.6         # race pace deficit (s/lap) per second of qualifying deficit
    pace_prior_sd: float = 0.45        # how wrong the qualifying-based pace guess typically is (s/lap)
    pit_loss: float = 22.0             # time lost by making a stop (s)
    dnf_per_lap: float = 0.002         # chance a car retires on any given lap
    safety_car_per_lap: float = 0.03   # chance a safety car period starts on any given lap
    tyre_deg: float = 0.03             # lap time lost per lap of tyre age beyond the field's (s)
    pit_hazard: list = field(default_factory=list)  # [age bucket][laps-left bucket][stops made] -> P(stop this lap)

    # --- hand-set (shortcut: not fitted, tune against held-out races before trusting the output)
    pace_prior_laps: float = 4.0       # the qualifying-based guess counts as this many observed laps
    # passing values were picked on 2023 races only (settings from 2022) out of four options
    pass_base: float = 0.02            # chance of completing a pass with no pace advantage
    pass_per_second: float = 0.1       # extra pass chance per second of advantage on that lap
    first_lap_sd: float = 1.0          # extra lap-time scatter on a standing start (s)

    def to_dict(self) -> dict:
        return asdict(self)


def _bucket(values, edges) -> np.ndarray:
    return np.clip(np.searchsorted(edges, values, side="right") - 1, 0, len(edges) - 2)


# ---------------------------------------------------------------------------
# Preparing lap data
# ---------------------------------------------------------------------------

def add_pace_columns(laps: pd.DataFrame) -> pd.DataFrame:
    """
    Add per-lap pace information to a lap table.

    Args:
        laps: One row per (session_key, driver_number, lap_number) with
            lap_duration and pit_stop_count.

    Returns:
        Copy sorted by (session_key, driver_number, lap_number) with:
          pitted        the driver made a stop on this lap
          neutralized   the whole field was slow on this lap (safety car / VSC)
          rel_pace      lap time minus the field's median on that lap (s), NaN
                        unless the lap is "clean" (green, not lap 1, not an
                        in- or out-lap)
          obs_pace      mean rel_pace over the driver's last clean laps up to
                        and including this lap
          obs_laps      how many clean laps obs_pace is based on
    """
    laps = laps.sort_values(["session_key", "driver_number", "lap_number"]).copy()
    by_driver = laps.groupby(["session_key", "driver_number"], sort=False)
    laps["pitted"] = by_driver["pit_stop_count"].diff().fillna(laps["pit_stop_count"]).gt(0)
    out_lap = by_driver["pitted"].shift(1, fill_value=False)

    lap_median = laps.groupby(["session_key", "lap_number"])["lap_duration"].transform("median")
    race_median = laps.groupby("session_key")["lap_duration"].transform("median")
    laps["neutralized"] = lap_median > 1.2 * race_median

    clean = ~laps["pitted"] & ~out_lap & ~laps["neutralized"] & (laps["lap_number"] > 1)
    laps["rel_pace"] = (laps["lap_duration"] - lap_median).where(clean)
    # a single very slow lap (spin, damage, traffic) is not pace
    laps["rel_pace"] = laps["rel_pace"].where(laps["rel_pace"].abs() < 5.0)

    rolling = laps.groupby(["session_key", "driver_number"], sort=False)["rel_pace"].rolling(
        _CLEAN_LAPS_WINDOW, min_periods=1
    )
    laps["obs_pace"] = rolling.mean().reset_index(level=[0, 1], drop=True)
    laps["obs_laps"] = rolling.count().reset_index(level=[0, 1], drop=True)
    by_driver = laps.groupby(["session_key", "driver_number"], sort=False)
    laps["obs_pace"] = by_driver["obs_pace"].ffill()
    laps["obs_laps"] = by_driver["obs_laps"].ffill().fillna(0.0)
    return laps


def estimate_params(laps: pd.DataFrame, safety_cars: "pd.Series | None" = None) -> SimParams:
    """
    Estimate the data-driven simulator settings from past races.

    Args:
        laps: Output of add_pace_columns() for the training seasons, also
            carrying qualifying_delta_to_pole, tyre_age_laps and laps_remaining.
        safety_cars: Number of full safety car deployments per race, indexed
            by session_key, for races with race-control records (0 where
            there was none). Races missing from it fall back to spotting
            slow laps, which over-counts.

    Returns:
        SimParams with the estimated block filled in and hand-set defaults elsewhere.
    """
    params = SimParams()
    car = ["session_key", "driver_number"]

    # lap-to-lap noise: spread of clean laps around each car's own race average (robust to outliers)
    resid = laps["rel_pace"] - laps.groupby(car)["rel_pace"].transform("mean")
    params.lap_noise_sd = float(1.4826 * resid.abs().median())

    # qualifying deficit -> race pace deficit, and how far off that guess typically is
    per_car = laps.groupby(car).agg(pace=("rel_pace", "mean"), quali=("qualifying_delta_to_pole", "first")).dropna()
    per_car["quali"] = per_car["quali"].clip(upper=3.0)
    centred = per_car - per_car.groupby("session_key").transform("mean")
    slope = float((centred["pace"] * centred["quali"]).sum() / (centred["quali"] ** 2).sum())
    params.quali_to_race = slope
    params.pace_prior_sd = float(1.4826 * (centred["pace"] - slope * centred["quali"]).abs().median())

    # time lost by stopping: in-lap + out-lap versus two of the car's normal laps
    nxt = laps.groupby(car, sort=False)["lap_duration"].shift(-1)
    normal = laps["lap_duration"].where(laps["rel_pace"].notna()).groupby([laps[c] for c in car]).transform("median")
    loss = (laps["lap_duration"] + nxt - 2 * normal)[laps["pitted"] & ~laps["neutralized"]]
    loss = loss[(loss > 10) & (loss < 60)]
    if len(loss):
        params.pit_loss = float(loss.median())

    # retirements: a car that stopped running 3+ laps before the end, per lap driven
    last = laps.groupby(car).agg(last_lap=("lap_number", "max"), left=("laps_remaining", "min"))
    params.dnf_per_lap = float((last["left"] >= 3).sum() / len(laps))

    # safety car periods started per racing lap. A period is 2+ slow laps in a
    # row after lap 2: a single slow lap or a slow start is usually something else.
    field_laps = laps.groupby(["session_key", "lap_number"])["neutralized"].first().reset_index()
    by_race = field_laps.groupby("session_key")["neutralized"]
    started = (
        field_laps["neutralized"]
        & ~by_race.shift(1, fill_value=False)
        & by_race.shift(-1, fill_value=False)
        & (field_laps["lap_number"] > 2)
    )
    params.safety_car_per_lap = float(started.sum() / len(field_laps))
    if safety_cars is not None:
        recorded = field_laps["session_key"].isin(safety_cars.index)
        if recorded.any():
            known = safety_cars[safety_cars.index.isin(field_laps["session_key"])]
            params.safety_car_per_lap = float(known.sum() / recorded.sum())

    # tyre wear: how much slower a car gets per lap its tyres are older than the
    # field's, comparing each car only with itself across the race
    age_rel = laps["tyre_age_laps"] - laps.groupby(["session_key", "lap_number"])["tyre_age_laps"].transform("median")
    wear = pd.DataFrame({"pace": laps["rel_pace"], "age": age_rel, "s": laps["session_key"], "d": laps["driver_number"]}).dropna()
    wear[["pace", "age"]] -= wear.groupby(["s", "d"])[["pace", "age"]].transform("mean")
    if (wear["age"] ** 2).sum() > 0:
        params.tyre_deg = float(max(0.0, (wear["pace"] * wear["age"]).sum() / (wear["age"] ** 2).sum()))

    # pit-stop probability by tyre age, laps remaining and stops already made (smoothed)
    known = laps.dropna(subset=["tyre_age_laps", "laps_remaining"])
    age = _bucket(known.groupby(car, sort=False)["tyre_age_laps"].shift(1).fillna(0).to_numpy(), _AGE_EDGES)
    left = _bucket(known["laps_remaining"].to_numpy(), _LEFT_EDGES)
    stops_before = np.minimum((known["pit_stop_count"] - known["pitted"]).to_numpy(), _MAX_STOPS).astype(int)
    shape = (len(_AGE_EDGES) - 1, len(_LEFT_EDGES) - 1, _MAX_STOPS + 1)
    stops, laps_seen = np.zeros(shape), np.zeros(shape)
    np.add.at(stops, (age, left, stops_before), known["pitted"].to_numpy(float))
    np.add.at(laps_seen, (age, left, stops_before), 1.0)
    overall = stops.sum() / laps_seen.sum()
    params.pit_hazard = ((stops + 20 * overall) / (laps_seen + 20)).tolist()
    return params


def field_state(laps: pd.DataFrame, session_key: int, lap_number: int, params: SimParams) -> pd.DataFrame:
    """
    The simulator's starting point for one race at the end of one lap.

    Args:
        laps: Output of add_pace_columns() carrying gap_to_leader, tyre_age_laps,
            pit_stop_count, laps_remaining and qualifying_delta_to_pole.
        session_key, lap_number: Which field.
        params: Settings (for turning qualifying into a pace guess).

    Returns:
        One row per running driver: driver_number, gap, tyre_age, stops,
        laps_remaining, pace_mean, pace_sd.
    """
    f = laps[(laps["session_key"] == session_key) & (laps["lap_number"] == lap_number)]
    return _state(
        f["driver_number"], f["gap_to_leader"], f["tyre_age_laps"], f["pit_stop_count"], f["laps_remaining"],
        f["qualifying_delta_to_pole"], f["obs_pace"], f["obs_laps"], params,
    )


def grid_state(prerace: pd.DataFrame, total_laps: int, params: SimParams) -> pd.DataFrame:
    """
    The simulator's starting point on the grid, before any lap is run.

    Args:
        prerace: One race's rows with driver_number, grid_position and
            qualifying_delta_to_pole.
        total_laps: Scheduled race distance.
        params: Settings.
    """
    grid = prerace["grid_position"].fillna(prerace["grid_position"].max() + 1)
    n = len(prerace)
    return _state(
        prerace["driver_number"], _GRID_SLOT_GAP * (grid - 1), pd.Series(0.0, index=prerace.index),
        pd.Series(0, index=prerace.index), pd.Series(total_laps, index=prerace.index),
        prerace["qualifying_delta_to_pole"], pd.Series(np.nan, index=prerace.index), pd.Series(0.0, index=prerace.index),
        params,
    ).assign(standing_start=[True] * n)


def _state(driver, gap, tyre_age, stops, laps_remaining, quali_delta, obs_pace, obs_laps, params) -> pd.DataFrame:
    quali = quali_delta.astype(float).clip(upper=3.0)
    prior = params.quali_to_race * quali.fillna(quali.mean() if quali.notna().any() else 0.0)
    n = obs_laps.fillna(0.0).to_numpy(float)
    obs = obs_pace.fillna(0.0).to_numpy(float)
    weight = n / (n + params.pace_prior_laps)
    age = tyre_age.fillna(0).to_numpy(float)
    # observed pace includes the tyres the car is on now; take that out so
    # the simulation can add wear back as tyres age and reset it at a stop
    obs = obs - params.tyre_deg * (age - np.median(age))
    return pd.DataFrame({
        "driver_number": driver.to_numpy(),
        "gap": gap.to_numpy(float),
        "tyre_age": tyre_age.fillna(0).to_numpy(float),
        "stops": stops.fillna(0).to_numpy(int),
        "laps_remaining": laps_remaining.to_numpy(int),
        # observed race pace, pulled towards the qualifying-based guess when few laps have been seen
        "pace_mean": weight * obs + (1 - weight) * prior.to_numpy(float),
        "pace_sd": params.pace_prior_sd * np.sqrt(1 - weight),
        "standing_start": False,
    })


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate_win_probs(state: pd.DataFrame, params: SimParams, n_sims: int = 2000, seed: int = 0) -> pd.Series:
    """
    Simulate the rest of the race and return each driver's share of wins.

    Args:
        state: Output of field_state() or grid_state().
        params: Simulator settings.
        n_sims: Number of simulated races.
        seed: Random seed (the same inputs and seed give the same answer).

    Returns:
        Series indexed by driver_number, summing to 1.
    """
    rng = np.random.default_rng(seed)
    drivers = state["driver_number"].to_numpy()
    n_cars = len(drivers)
    laps_left = int(state["laps_remaining"].max())
    hazard = np.asarray(params.pit_hazard) if params.pit_hazard else np.full(
        (len(_AGE_EDGES) - 1, len(_LEFT_EDGES) - 1, _MAX_STOPS + 1), 0.03
    )

    time = np.tile(state["gap"].to_numpy(float), (n_sims, 1))
    tyre_age = np.tile(state["tyre_age"].to_numpy(float), (n_sims, 1))
    stops = np.tile(state["stops"].to_numpy(int), (n_sims, 1))
    running = np.ones((n_sims, n_cars), dtype=bool)
    # each simulated race draws one "true" pace per car: the pace estimate is uncertain
    pace = state["pace_mean"].to_numpy(float) + state["pace_sd"].to_numpy(float) * rng.standard_normal((n_sims, n_cars))
    rows = np.arange(n_sims)[:, None]

    for lap in range(laps_left):
        left_after = laps_left - lap - 1

        pit_prob = hazard[_bucket(tyre_age, _AGE_EDGES), _bucket(left_after, _LEFT_EDGES), np.minimum(stops, _MAX_STOPS)]
        pits = running & (rng.random((n_sims, n_cars)) < pit_prob)
        if left_after == 0:
            pits &= False
        # shortcut: assumes a dry race where every car must stop once, lift when weather is modelled
        pits |= running & (stops == 0) & (left_after == 1)

        wear = params.tyre_deg * (tyre_age - np.median(tyre_age, axis=1, keepdims=True))
        lap_time = pace + wear + params.lap_noise_sd * rng.standard_normal((n_sims, n_cars)) + params.pit_loss * pits
        if lap == 0 and bool(state["standing_start"].iloc[0]):
            lap_time += params.first_lap_sd * rng.standard_normal((n_sims, n_cars))
        tentative = time + lap_time

        # overtaking: walk back through the running order; a car that would pass
        # the one directly ahead may be held up behind it instead
        order = np.argsort(time, axis=1)
        new_sorted = np.take_along_axis(tentative, order, axis=1)
        ahead_pitted = np.take_along_axis(pits, order, axis=1)
        for k in range(1, n_cars):
            ahead = new_sorted[:, k - 1]
            both_running = np.isfinite(ahead) & np.isfinite(new_sorted[:, k])
            advantage = np.where(both_running, ahead - np.where(both_running, new_sorted[:, k], 0.0), 0.0)
            p_pass = np.clip(params.pass_base + params.pass_per_second * advantage, 0.0, 1.0)
            held = (advantage > 0) & ~ahead_pitted[:, k - 1] & (rng.random(n_sims) > p_pass)
            new_sorted[:, k] = np.where(held, ahead + _HELD_UP_GAP, new_sorted[:, k])
        time[rows, order] = new_sorted

        tyre_age = np.where(pits, 0.0, tyre_age + 1)
        stops = stops + pits

        retired = running & (rng.random((n_sims, n_cars)) < params.dnf_per_lap)
        running &= ~retired
        time[~running] = np.inf

        safety_car = rng.random(n_sims) < params.safety_car_per_lap
        if safety_car.any() and left_after > 0:
            affected = time[safety_car]
            rank = np.argsort(np.argsort(affected, axis=1), axis=1)
            leader = np.where(np.isfinite(affected), affected, np.inf).min(axis=1, keepdims=True)
            leader = np.where(np.isfinite(leader), leader, 0.0)
            time[safety_car] = np.where(np.isfinite(affected), leader + _SC_SPACING * rank, np.inf)

    # if every car retired in a simulated race, nobody wins it
    finished = np.isfinite(time).any(axis=1)
    winners = np.argmin(time[finished], axis=1)
    wins = np.bincount(winners, minlength=n_cars).astype(float)
    return pd.Series(wins / wins.sum(), index=pd.Index(drivers, name="driver_number"), name="win_prob")
