"""
Evaluate the win probabilities the app actually serves (Phase C).

Scores the saved model artifacts on every season they were NOT trained on
(read from <artifact>.meta.json), using the final per-field distribution —
not the raw binary classifier output — and compares it with simple
baselines on the same races and drivers.

2024 and 2025 have already informed model choices, so results on them are
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
    python scripts/evaluate.py                # prints a table, writes data/eval/eval-<timestamp>.json
    python scripts/evaluate.py --output-dir /tmp/eval

Exit codes:
    0: success
    1: missing artifact/metadata or no unseen seasons to evaluate
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

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from f1_prediction.db.engine import get_engine  # noqa: E402
from f1_prediction.inference.features import (  # noqa: E402
    PRERACE_FEATURE_COLS,
    build_inrace_feature_matrix,
    normalize_probs,
)
from train import (  # noqa: E402
    code_revision,
    dataset_hash,
    load_inrace_training,
    load_prerace_training,
)

logger = logging.getLogger(__name__)

METRICS = ["brier", "nll", "top1"]
_NLL_FLOOR = 1e-6  # a 0% winner would otherwise score infinity; such fields are counted
_STAGES = ["early", "middle", "late"]


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


def paired_difference(a: pd.DataFrame, b: pd.DataFrame, *, n_boot: int = 2000, seed: int = 0) -> dict:
    """Mean of (a - b) per race with a 95% race-bootstrap interval. Negative brier/nll favours a."""
    return summarize(a[METRICS] - b[METRICS], n_boot=n_boot, seed=seed)


# ---------------------------------------------------------------------------
# Candidates
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


def odds(p: np.ndarray) -> np.ndarray:
    """p / (1 - p): normalizing these is a softmax over the classifier's logits."""
    return p / (1 - np.clip(p, 0, 1 - 1e-9))


def _stage(df: pd.DataFrame) -> pd.Series:
    progress = df["lap_number"] / (df["lap_number"] + df["laps_remaining"])
    return pd.cut(progress, [0, 1 / 3, 2 / 3, 1.0], labels=_STAGES, include_lowest=True).astype(str)


def _load_artifact(path: Path) -> tuple[object, dict]:
    meta_path = path.with_suffix(".meta.json")
    if not path.exists() or not meta_path.exists():
        raise FileNotFoundError(
            f"{path} or {meta_path.name} is missing — run scripts/train.py to produce both."
        )
    return joblib.load(path), json.loads(meta_path.read_text())


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _evaluate(df, raw, keys, baselines: dict, reference: str, train_years: list[int]) -> dict:
    """Score the model and baselines on each unseen season; returns the results dict."""
    eval_years = sorted(int(y) for y in df["year"].unique() if y > max(train_years))
    if not eval_years:
        raise ValueError(f"No seasons after training years {train_years} to evaluate on.")

    train = df[df["year"].isin(train_years)]
    results = {}
    for label, years in [*[(str(y), [y]) for y in eval_years], ("all", eval_years)]:
        test = df[df["year"].isin(years)]
        candidates = {
            "uniform": pd.Series(1.0, index=test.index),
            **{name: frequency_baseline(train, test, cols) for name, cols in baselines.items()},
            "model": pd.Series(raw[test.index], index=test.index),
            "model_odds_normalized": pd.Series(odds(raw[test.index]), index=test.index),
        }
        fields = {name: score_fields(test, p, keys) for name, p in candidates.items()}
        # every race counts once, however many laps it has
        per_race = {name: f.groupby("session_key").mean() for name, f in fields.items()}

        block = {
            "n_races": int(len(per_race["model"])),
            "n_fields": int(len(fields["model"])),
            "n_fields_dropped_no_single_winner": int(test.groupby(keys).ngroups - len(fields["model"])),
            "fields_with_zero_probability_winner": int(fields["model"]["zero_winner"].sum()),
            "candidates": {name: summarize(r) for name, r in per_race.items()},
            f"model_minus_{reference}": paired_difference(per_race["model"], per_race[reference]),
        }
        if "stage" in test.columns:
            stage_of_field = test.groupby(keys)["stage"].first()
            block["by_stage"] = {
                stage: {
                    name: summarize(f[stage_of_field.reindex(f.index) == stage].groupby("session_key").mean())
                    for name, f in fields.items() if name in ("model", reference)
                }
                for stage in _STAGES
            }
        results[label] = block
    return results


def evaluate_all(engine, models_dir: Path) -> dict:
    """Evaluate both saved models; returns the full machine-readable report."""
    report = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "kind": "development backtest (2024 and 2025 have informed model selection)",
        "code_revision": code_revision(),
        "brier_convention": "sum over drivers of (p_i - y_i)^2 per field, averaged per race then over races",
        "interval": "95% percentile bootstrap over whole races, 2000 resamples, seed 0",
    }

    model, meta = _load_artifact(models_dir / "prerace_model.joblib")
    df = load_prerace_training(engine, max_year=9999).reset_index(drop=True)
    raw = model.predict_proba(df[PRERACE_FEATURE_COLS].values)[:, 1]
    report["prerace"] = {
        "artifact": meta,
        "eval_dataset_hash": dataset_hash(df),
        "results": _evaluate(
            df, raw, ["session_key"],
            {"grid": ["grid_position"], "championship": ["championship_position"]},
            "grid", meta["train_years"],
        ),
    }

    model, meta = _load_artifact(models_dir / "inrace_model.joblib")
    df = load_inrace_training(engine, max_year=9999).reset_index(drop=True)
    df["stage"] = _stage(df)
    raw = model.predict_proba(build_inrace_feature_matrix(df).values)[:, 1]
    report["inrace"] = {
        "artifact": meta,
        "eval_dataset_hash": dataset_hash(df),
        "results": _evaluate(
            df, raw, ["session_key", "lap_number"],
            {"position": ["position", "stage"]},
            "position", meta["train_years"],
        ),
    }
    return report


def _print_report(report: dict) -> None:
    for kind in ("prerace", "inrace"):
        reference = "grid" if kind == "prerace" else "position"
        for label, block in report[kind]["results"].items():
            print(f"\n{kind} {label}: {block['n_races']} races, {block['n_fields']} fields, "
                  f"{block['fields_with_zero_probability_winner']} with a 0% winner")
            print(f"  {'candidate':<24}{'brier':>22}{'nll':>22}{'top1':>22}")
            rows = {**block["candidates"], f"model - {reference}": block[f"model_minus_{reference}"]}
            for name, s in rows.items():
                cells = "".join(
                    f"{s[m]['mean']:>8.3f} [{s[m]['ci95'][0]:>5.2f},{s[m]['ci95'][1]:>5.2f}]" for m in METRICS
                )
                print(f"  {name:<24}{cells}")
            for stage, cands in block.get("by_stage", {}).items():
                print(f"  {stage:<7}" + "  ".join(
                    f"{name} nll={s['nll']['mean']:.3f} top1={s['top1']['mean']:.3f}" for name, s in cands.items()
                ))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate served win probabilities against simple baselines on unseen seasons.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--models-dir", type=Path, default=Path("data/models"), metavar="DIR")
    parser.add_argument("--output-dir", type=Path, default=Path("data/eval"), metavar="DIR",
                        help="Directory for the timestamped JSON report (never overwritten).")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    try:
        report = evaluate_all(get_engine(), args.models_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"evaluate: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_report(report)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / f"eval-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
