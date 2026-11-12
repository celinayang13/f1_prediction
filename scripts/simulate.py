"""
Evaluate the experimental Monte Carlo race simulator (simulation/race_sim.py).

Walk-forward, like scripts/evaluate.py: each season after the first is
simulated with settings estimated only from earlier seasons, and scored on
the same fields as the served model and the simple baseline.

Usage:
    python scripts/simulate.py                 # every 3rd lap, 1000 simulated races per field
    python scripts/simulate.py --every 1 --sims 2000

Exit codes:
    0: success
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from evaluate import METRICS, _stage, frequency_baseline, score_fields, summarize  # noqa: E402
from f1_prediction.db.engine import get_engine  # noqa: E402
from f1_prediction.simulation.race_sim import (  # noqa: E402
    add_pace_columns,
    estimate_params,
    field_state,
    grid_state,
    simulate_win_probs,
)
from train import load_inrace_training, load_prerace_training, train_inrace_model, train_prerace_model  # noqa: E402


def load_sim_laps(engine) -> pd.DataFrame:
    """In-race training rows plus lap times and qualifying gaps, with pace columns added."""
    laps = load_inrace_training(engine, max_year=9999)
    with engine.connect() as conn:
        durations = pd.read_sql(text(
            "SELECT l.session_key, l.driver_number, l.lap_number, l.lap_duration "
            "FROM raw_laps l JOIN raw_sessions rs USING (session_key) WHERE rs.session_type = 'Race'"
        ), conn)
        quali = pd.read_sql(text(
            "SELECT session_key, driver_number, qualifying_delta_to_pole FROM features_prerace"
        ), conn)
    laps = laps.merge(durations, on=["session_key", "driver_number", "lap_number"], how="left")
    laps = laps.merge(quali, on=["session_key", "driver_number"], how="left")
    return add_pace_columns(laps).reset_index(drop=True)


def _report(title: str, df: pd.DataFrame, keys: list[str], candidates: dict[str, pd.Series], reference: str) -> dict:
    per_race = {
        name: score_fields(df, p, keys).groupby("session_key")[METRICS].mean() for name, p in candidates.items()
    }
    print(f"\n{title}: {len(per_race['simulation'])} races")
    print(f"  {'candidate':<26}{'brier':>22}{'nll':>22}{'top1':>22}")
    out = {}
    rows = {**per_race, f"simulation - {reference}": per_race["simulation"] - per_race[reference]}
    for name, r in rows.items():
        s = out[name] = summarize(r)
        print(f"  {name:<26}" + "".join(
            f"{s[m]['mean']:>8.3f} [{s[m]['ci95'][0]:>5.2f},{s[m]['ci95'][1]:>5.2f}]" for m in METRICS
        ))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1],
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--every", type=int, default=3, help="Simulate every Nth lap of each race.")
    parser.add_argument("--sims", type=int, default=1000, help="Simulated races per field.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    engine = get_engine()
    laps = load_sim_laps(engine)
    laps["stage"] = _stage(laps)
    pre = load_prerace_training(engine, max_year=9999).reset_index(drop=True)
    with engine.connect() as conn:
        pre = pre.merge(pd.read_sql(text("SELECT session_key, total_laps FROM raw_sessions"), conn), on="session_key")
    years = sorted(int(y) for y in laps["year"].unique())

    in_parts, pre_parts, settings = [], [], {}
    for year in years[1:]:
        train = laps[laps["year"] < year]
        params = settings[year] = estimate_params(train)

        # in-race: every Nth lap of every race this season
        test = laps[(laps["year"] == year) & (laps["lap_number"] % args.every == 0)].copy()
        sim = []
        for (session_key, lap_number), _ in test.groupby(["session_key", "lap_number"]):
            probs = simulate_win_probs(field_state(laps, session_key, lap_number, params), params, args.sims)
            sim.append(probs.reset_index().assign(session_key=session_key, lap_number=lap_number))
        test = test.merge(pd.concat(sim), on=["session_key", "lap_number", "driver_number"], how="left")
        test["simulation"] = test["win_prob"].fillna(0.0)
        test["served"] = train_inrace_model(train, sorted(train["year"].unique())).scores(test)
        test["position"] = frequency_baseline(train, test, ["position", "stage"])
        in_parts.append(test)

        # pre-race: from the grid
        pre_train, pre_test = pre[pre["year"] < year], pre[pre["year"] == year].copy()
        sim = [
            simulate_win_probs(grid_state(race, int(race["total_laps"].iloc[0]), params), params, args.sims)
            .reset_index().assign(session_key=session_key)
            for session_key, race in pre_test.groupby("session_key")
        ]
        pre_test = pre_test.merge(pd.concat(sim), on=["session_key", "driver_number"], how="left")
        pre_test["simulation"] = pre_test["win_prob"].fillna(0.0)
        pre_test["served"] = train_prerace_model(pre_train, sorted(pre_train["year"].unique())).scores(pre_test)
        pre_test["grid"] = frequency_baseline(pre_train, pre_test, ["grid_position"])
        pre_parts.append(pre_test)

    latest = settings[years[-1]].to_dict()
    latest.pop("pit_hazard")
    print("simulator settings estimated from seasons before", years[-1], ":", json.dumps({k: round(v, 4) for k, v in latest.items()}))

    inrace, prerace = pd.concat(in_parts, ignore_index=True), pd.concat(pre_parts, ignore_index=True)
    _report("pre-race (from the grid)", prerace, ["session_key"],
            {n: prerace[n] for n in ("grid", "served", "simulation")}, "served")
    _report(f"in-race (every {args.every} laps)", inrace, ["session_key", "lap_number"],
            {n: inrace[n] for n in ("position", "served", "simulation")}, "served")
    for stage in ("early", "middle", "late"):
        part = inrace[inrace["stage"] == stage]
        _report(f"in-race, {stage} third", part, ["session_key", "lap_number"],
                {n: part[n] for n in ("served", "simulation")}, "served")


if __name__ == "__main__":
    main()
