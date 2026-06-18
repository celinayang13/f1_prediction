"""
Model training CLI entry point (D-13).

Trains both the pre-race and in-race XGBoost win probability models from
the Phase 1 feature tables in data/f1.db and serializes the calibrated
artifacts to data/models/.

Usage:
    python scripts/train.py

Output (stdout):
    prerace  log_loss=...  top3_hit_rate=...  (.../... races)
    (in-race metrics printed by Plan 02-02 once train_inrace_model is wired)

Exit codes:
    0: success — both model artifacts written to data/models/
    1: exception raised during training (e.g. features_prerace is empty)

Note: Always retrains both models on every run (D-14: no selective flags).
Training is fast at the project data scale (~1,440 pre-race rows for 2022-2024).
Run 'python scripts/backfill.py' first to populate data/f1.db before training.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure src/ is on the path for non-installed runs (mirrors build_features.py line 29)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from f1_prediction.db.engine import get_engine  # noqa: E402
from f1_prediction.ml.pipeline import train_prerace_model  # noqa: E402


def main() -> None:
    """CLI entrypoint that trains pre-race and in-race models."""
    parser = argparse.ArgumentParser(
        description=(
            "Train the pre-race and in-race XGBoost win probability models. "
            "Reads features_prerace and features_inrace from data/f1.db, "
            "trains with a 2022-2024 temporal split, calibrates on 2025 data, "
            "and writes data/models/prerace_model.joblib (and inrace_model.joblib "
            "once Plan 02-02 is complete). Always retrains both models on every run."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # D-14: no --prerace-only, --inrace-only, or --force flags
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    try:
        engine = get_engine()
        train_prerace_model(engine)
        # TODO(02-02): train_inrace_model(engine)
        sys.exit(0)
    except Exception as exc:
        logging.getLogger(__name__).error("Training failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
