"""
Evaluate the win probabilities the app serves (Phase C).

Walk-forward: every season after the first is predicted by models fitted
only on earlier seasons, using the same training function as the served
artifact. The served field-choice model is compared with simple baselines
and an XGBoost challenger on the same races and drivers, and each model is
also scored on the races it was fitted on — a large seen/unseen gap means
it is memorising rather than generalising.

These seasons have already informed model choices, so the results are
development backtests, not untouched tests.

Metrics (per field = one race pre-race, one race lap in-race):
  brier    multiclass Brier: sum over drivers of (p_i - y_i)^2. 0 is perfect,
           a uniform 20-car field scores 0.95.
  nll      negative log of the probability given to the actual winner.
  top1     1 if the highest-probability driver won (ties share the credit).
In-race metrics are averaged within each race first, so every race counts
equally regardless of length. Intervals are 95% bootstrap intervals from
resampling whole races.

Usage:
    python scripts/evaluate.py                # prints tables, writes data/eval/eval-<timestamp>.json
    python scripts/evaluate.py --output-dir /tmp/eval

Exit codes:
    0: success
    1: fewer than two seasons of data
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import xgboost as xgb  # noqa: E402

from f1_prediction.db.engine import get_engine  # noqa: E402
from f1_prediction.inference.features import normalize_probs  # noqa: E402
from f1_prediction.inference.field_choice import (  # noqa: E402
    _field_index,
    _field_softmax,
    inrace_inputs,
    prerace_inputs,
)
from train import (  # noqa: E402
    code_revision,
    dataset_hash,
    load_inrace_training,
    load_prerace_training,
    train_inrace_model,
    train_prerace_model,
)

logger = logging.getLogger(__name__)

METRICS = ["brier", "nll", "top1"]
_NLL_FLOOR = 1e-6  # a 0% winner would otherwise score infinity; such fields are counted
_STAGES = ["early", "middle", "late"]
# inputs where a larger value can only hurt a driver's chances (for the tree challenger)
_NEVER_HELPS = {"log_grid", "recent_quali_delta", "log_pos", "log_gap", "log_gap_per_lap_left"}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_fields(df: pd.DataFrame, prob: pd.Series, keys: list[str]) -> pd.DataFrame:
    """
    Score one candidate's probabilities, one row per field.

    Args:
        df: Rows with the key columns and a 0/1 'is_winner' column.
        prob: Score per row (same index as df); normalized within each field here.
        keys: Columns identifying a field, e.g. ['session_key'] or
              ['session_key', 'lap_number'].

    Returns:
        DataFrame indexed by keys with columns brier, nll, top1, zero_winner.
        Fields without exactly one winner are dropped.
    """
    d = df[keys + ["is_winner"]].copy()
    d["p"] = prob.groupby([df[k] for k in keys]).transform(lambda x: normalize_probs(x.values))
    d = d[d.groupby(keys)["is_winner"].transform("sum") == 1]

    d["sq"] = (d["p"] - d["is_winner"]) ** 2
    out = d.groupby(keys).agg(brier=("sq", "sum"))
    winner_p = d[d["is_winner"] == 1].set_index(keys)["p"]
    out["nll"] = -np.log(winner_p.clip(lower=_NLL_FLOOR))
    # ties split the credit, so a uniform field scores 1/n rather than whoever is listed first
    top = d[d["p"] == d.groupby(keys)["p"].transform("max")]
    out["top1"] = top.groupby(keys)["is_winner"].mean()
    out["zero_winner"] = (winner_p == 0).astype(float)
    return out


def summarize(per_race: pd.DataFrame, *, n_boot: int = 2000, seed: int = 0) -> dict:
    """Mean of each metric over races with a 95% race-bootstrap interval."""
    values = per_race[METRICS].to_numpy()
    idx = np.random.default_rng(seed).integers(0, len(values), size=(n_boot, len(values)))
    boot = values[idx].mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5], axis=0)
    return {
        m: {"mean": round(float(values[:, i].mean()), 4), "ci95": [round(float(lo[i]), 4), round(float(hi[i]), 4)]}
        for i, m in enumerate(METRICS)
    }


# ---------------------------------------------------------------------------
# Candidates: each takes (train rows, rows to score) and returns a score per row
# ---------------------------------------------------------------------------

def frequency_baseline(train: pd.DataFrame, test: pd.DataFrame, cols: list[str]) -> pd.Series:
    """
    Historical win frequency for each value of cols, learned on train only.

    Smoothed towards one win per 20 starts so an unseen or empty bucket never
    yields exactly 0; values missing from train get the lowest observed rate.
    """
    stats = train.groupby(cols)["is_winner"].agg(["sum", "count"])
    rate = ((stats["sum"] + 1) / (stats["count"] + 20)).rename("rate").reset_index()
    merged = test[cols].merge(rate, on=cols, how="left")["rate"]
    return pd.Series(merged.fillna(rate["rate"].min()).to_numpy(), index=test.index)


def _uniform(train: pd.DataFrame, test: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0, index=test.index)


def _served(train_fn):
    """The served field-choice model, refitted on the given training seasons."""
    def run(train, test):
        model = train_fn(train, sorted(train["year"].unique()))
        return pd.Series(model.scores(test), index=test.index)
    return run


def _xgboost_challenger(inputs_fn, keys: list[str]):
    """
    Gradient-boosted trees on the served model's inputs, trained on the same
    objective (softmax over the field), kept shallow, and constrained so a
    worse grid slot / position / gap can never raise a driver's chances.
    """
    def run(train, test):
        X_all = inputs_fn(train)  # field-relative inputs need the complete fields
        keep = train.groupby(keys)["is_winner"].transform("sum") == 1
        order = train[keep].sort_values(keys).index
        X, fit_rows = X_all.loc[order], train.loc[order]
        y = fit_rows["is_winner"].to_numpy(float)
        starts, field_of_row = _field_index(fit_rows.groupby(keys, sort=False).ngroup().to_numpy())

        def objective(pred, _dtrain):
            p = _field_softmax(pred, starts, field_of_row)
            return p - y, np.maximum(p * (1 - p), 1e-6)

        params = {
            "max_depth": 2, "eta": 0.05, "subsample": 0.8, "colsample_bytree": 0.8,
            "base_score": 0.0, "seed": 0, "verbosity": 0,
            "monotone_constraints": "(" + ",".join("-1" if c in _NEVER_HELPS else "0" for c in X.columns) + ")",
        }
        booster = xgb.train(params, xgb.DMatrix(X.to_numpy(float), label=y), num_boost_round=100, obj=objective)
        margin = booster.predict(xgb.DMatrix(inputs_fn(test).to_numpy(float)), output_margin=True)
        return pd.Series(np.exp(np.clip(margin, -50, 50)), index=test.index)
    return run


def _stage(df: pd.DataFrame) -> pd.Series:
    progress = df["lap_number"] / (df["lap_number"] + df["laps_remaining"])
    return pd.cut(progress, [0, 1 / 3, 2 / 3, 1.0], labels=_STAGES, include_lowest=True).astype(str)


# ---------------------------------------------------------------------------
# Walk-forward evaluation
# ---------------------------------------------------------------------------

def walk_forward(df: pd.DataFrame, keys: list[str], candidates: dict, reference: str, learned: list[str]) -> dict:
    """
    Predict each season after the first from all earlier seasons.

    Args:
        df: Training frame with 'year', 'session_key', 'is_winner' (+ 'stage' for in-race).
        keys: Columns identifying a field.
        candidates: name -> callable(train_df, test_df) returning a score per test row.
        reference: The baseline candidate the served model is compared against.
        learned: Candidates to also score on the races they were fitted on.

    Returns:
        Results dict: one block per test season plus 'all'.
    """
    years = sorted(int(y) for y in df["year"].unique())
    if len(years) < 2:
        raise ValueError(f"Need at least two seasons to evaluate, found {years}.")

    unseen = {name: [] for name in candidates}   # per-field scores on the predicted season
    seen = {name: [] for name in learned}        # per-field scores on the seasons fitted on
    for year in years[1:]:
        train, test = df[df["year"] < year], df[df["year"] == year]
        for name, candidate in candidates.items():
            unseen[name].append(score_fields(test, candidate(train, test), keys).assign(year=year))
            if name in seen:
                seen[name].append(score_fields(train, candidate(train, train), keys).assign(year=year))

    unseen = {name: pd.concat(parts) for name, parts in unseen.items()}
    seen = {name: pd.concat(parts) for name, parts in seen.items()}
    stage_of_field = df.groupby(keys)["stage"].first() if "stage" in df.columns else None

    def per_race(fields: pd.DataFrame) -> pd.DataFrame:
        # every race counts once, however many laps it has
        return fields.groupby("session_key")[METRICS].mean()

    results = {}
    for label, block_years in [*[(str(y), [y]) for y in years[1:]], ("all", years[1:])]:
        pick = {name: f[f["year"].isin(block_years)] for name, f in unseen.items()}
        races = {name: per_race(f) for name, f in pick.items()}
        block = {
            "n_races": int(len(races["served"])),
            "n_fields": int(len(pick["served"])),
            "fields_with_zero_probability_winner": int(pick["served"]["zero_winner"].sum()),
            "candidates": {name: summarize(r) for name, r in races.items()},
            f"served_minus_{reference}": summarize(races["served"] - races[reference]),
            "seen_vs_unseen": {
                name: {
                    "seen": summarize(per_race(seen[name][seen[name]["year"].isin(block_years)])),
                    "unseen": summarize(races[name]),
                }
                for name in learned
            },
        }
        if stage_of_field is not None:
            block["by_stage"] = {
                stage: {
                    name: summarize(per_race(f[(stage_of_field.reindex(f.index) == stage).to_numpy()]))
                    for name, f in pick.items() if name in ("served", reference)
                }
                for stage in _STAGES
            }
        results[label] = block
    return results


def evaluate_all(engine) -> dict:
    """Walk-forward evaluation of both prediction kinds; returns the machine-readable report."""
    report = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "kind": "development backtest, walk-forward by season (these seasons have informed model selection)",
        "code_revision": code_revision(),
        "brier_convention": "sum over drivers of (p_i - y_i)^2 per field, averaged per race then over races",
        "interval": "95% percentile bootstrap over whole races, 2000 resamples, seed 0",
    }

    df = load_prerace_training(engine, max_year=9999).reset_index(drop=True)
    keys = ["session_key"]
    report["prerace"] = {
        "eval_dataset_hash": dataset_hash(df),
        "reference": "grid",
        "results": walk_forward(df, keys, {
            "uniform": _uniform,
            "grid": lambda tr, te: frequency_baseline(tr, te, ["grid_position"]),
            "championship": lambda tr, te: frequency_baseline(tr, te, ["championship_position"]),
            "served": _served(train_prerace_model),
            "xgboost_challenger": _xgboost_challenger(prerace_inputs, keys),
        }, "grid", ["served", "xgboost_challenger"]),
    }

    df = load_inrace_training(engine, max_year=9999).reset_index(drop=True)
    df["stage"] = _stage(df)
    keys = ["session_key", "lap_number"]
    report["inrace"] = {
        "eval_dataset_hash": dataset_hash(df),
        "reference": "position",
        "results": walk_forward(df, keys, {
            "uniform": _uniform,
            "position": lambda tr, te: frequency_baseline(tr, te, ["position", "stage"]),
            "served": _served(train_inrace_model),
            "xgboost_challenger": _xgboost_challenger(inrace_inputs, keys),
        }, "position", ["served", "xgboost_challenger"]),
    }
    return report


def _print_report(report: dict) -> None:
    def cells(s):
        return "".join(f"{s[m]['mean']:>8.3f} [{s[m]['ci95'][0]:>5.2f},{s[m]['ci95'][1]:>5.2f}]" for m in METRICS)

    for kind in ("prerace", "inrace"):
        reference = report[kind]["reference"]
        for label, block in report[kind]["results"].items():
            print(f"\n{kind} {label}: {block['n_races']} races, {block['n_fields']} fields, "
                  f"{block['fields_with_zero_probability_winner']} with a 0% winner")
            print(f"  {'candidate':<24}{'brier':>22}{'nll':>22}{'top1':>22}")
            for name, s in block["candidates"].items():
                print(f"  {name:<24}{cells(s)}")
            print(f"  {'served - ' + reference:<24}{cells(block[f'served_minus_{reference}'])}")
            for name, s in block["seen_vs_unseen"].items():
                print(f"  overfit check {name}: seen nll={s['seen']['nll']['mean']:.3f} top1={s['seen']['top1']['mean']:.3f}"
                      f" | unseen nll={s['unseen']['nll']['mean']:.3f} top1={s['unseen']['top1']['mean']:.3f}")
            for stage, cands in block.get("by_stage", {}).items():
                print(f"  {stage:<7}" + "  ".join(
                    f"{name} nll={s['nll']['mean']:.3f} top1={s['top1']['mean']:.3f}" for name, s in cands.items()
                ))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Walk-forward evaluation of served win probabilities against baselines and a challenger.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/eval"), metavar="DIR",
                        help="Directory for the timestamped JSON report (never overwritten).")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    try:
        report = evaluate_all(get_engine())
    except ValueError as exc:
        print(f"evaluate: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_report(report)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / f"eval-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
