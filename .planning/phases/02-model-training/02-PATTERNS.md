# Phase 2: Model Training - Pattern Map

**Mapped:** 2026-06-17
**Files analyzed:** 6 new/modified files
**Analogs found:** 5 / 6

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `scripts/train.py` | utility (CLI script) | batch, transform | `scripts/build_features.py` | exact |
| `src/f1_prediction/ml/__init__.py` | config (package init) | — | `src/f1_prediction/db/__init__.py` | exact |
| `src/f1_prediction/ml/loader.py` | service | CRUD (read-only) | `src/f1_prediction/features/prerace.py` | role-match |
| `src/f1_prediction/ml/pipeline.py` | service | batch, transform | `src/f1_prediction/features/prerace.py` | partial-match |
| `src/f1_prediction/ml/evaluate.py` | utility | batch, transform | `src/f1_prediction/features/prerace.py` | partial-match |
| `pyproject.toml` (modified) | config | — | `pyproject.toml` | exact |

---

## Pattern Assignments

### `scripts/train.py` (CLI script, batch)

**Analog:** `scripts/build_features.py` (lines 1–76) — exact match: same role (CLI entry point), same invocation pattern, same logging setup.

**Imports pattern** (`scripts/build_features.py` lines 1–31):
```python
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure src/ is on the path for non-installed runs
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from f1_prediction.features.builder import build_all_features  # noqa: E402
```

**Logging setup pattern** (`scripts/build_features.py` lines 58–62):
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
```

**Core CLI pattern** (`scripts/build_features.py` lines 34–75):
```python
def main() -> None:
    """CLI entrypoint for the standalone feature builder."""
    parser = argparse.ArgumentParser(
        description=("..."),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # ... add_argument calls ...
    args = parser.parse_args()

    logging.basicConfig(...)

    try:
        result = build_all_features(only_years=args.only_years)
        print(json.dumps(result, indent=2))
        sys.exit(0)
    except Exception as exc:
        logging.getLogger(__name__).error("Feature build failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Key difference for train.py:** No argparse arguments (D-14: always retrain both models; no flags). The `try/except` wraps both `train_prerace_model()` and `train_inrace_model()` calls. Use `print()` for metric output (not `json.dumps`) per D-16.

**Guard clause pattern** (required per RESEARCH.md Pitfall 5):
```python
if len(train_df) == 0:
    raise RuntimeError("features_prerace is empty — run scripts/backfill.py first")
```

**Directory creation pattern** (from RESEARCH.md Pattern 5):
```python
from pathlib import Path
Path("data/models").mkdir(parents=True, exist_ok=True)
```

---

### `src/f1_prediction/ml/__init__.py` (package init)

**Analog:** `src/f1_prediction/db/__init__.py`

Copy the empty `__init__.py` pattern — these files are empty (zero bytes) in the existing codebase. The `ml/` package needs only `__init__.py` to be importable.

---

### `src/f1_prediction/ml/loader.py` (service, CRUD read-only)

**Analog:** `src/f1_prediction/features/prerace.py` (lines 1–21) — same pattern: module-level docstring, `from __future__ import annotations`, logging, `pd.read_sql` with `text()`.

**Module-level pattern** (`src/f1_prediction/features/prerace.py` lines 1–21):
```python
"""
Pre-race feature computation functions (D-02).

Security: All SQL uses text() with bound :param names — no f-string SQL (T-03-05).
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)
```

**SQL query pattern** (`src/f1_prediction/features/prerace.py` lines 55–62):
```python
race_meta_q = text(
    "SELECT year, round_number FROM raw_sessions WHERE session_key = :sk"
)
meta = conn.execute(race_meta_q, {"sk": session_key}).fetchone()
```

**`pd.read_sql` with `text()` pattern** (from CONTEXT.md §Established Patterns and RESEARCH.md Pattern 4):
```python
from sqlalchemy import text

query = text("""
    SELECT fp.*, rs.year
    FROM features_prerace fp
    JOIN raw_sessions rs ON fp.session_key = rs.session_key
    WHERE rs.session_type = 'Race'
      AND fp.finish_position IS NOT NULL
""")
df = pd.read_sql(query, conn)
```

**Temporal split pattern** (RESEARCH.md Pattern 4):
```python
train_df = df[df["year"] <= 2024].copy()
test_df  = df[df["year"] == 2025].copy()
```

---

### `src/f1_prediction/ml/pipeline.py` (service, batch/transform)

**Analog:** No direct codebase analog — no ML training code exists yet. Use RESEARCH.md patterns exclusively.

**OrdinalEncoder pattern** (RESEARCH.md Pattern 1, anti-pattern warning):
```python
import numpy as np
from sklearn.preprocessing import OrdinalEncoder
import xgboost as xgb

# IMPORTANT: fit on train split only — not on full df (see RESEARCH.md Pitfall 3)
encoder = OrdinalEncoder(
    handle_unknown="use_encoded_value",
    unknown_value=np.nan,          # NOT -1 — see RESEARCH.md Pitfall 7
    encoded_missing_value=np.nan,
)
X_train_enc = X_train.copy()
X_test_enc = X_test.copy()
X_train_enc[cat_cols] = encoder.fit_transform(X_train[cat_cols])
X_test_enc[cat_cols]  = encoder.transform(X_test[cat_cols])
```

**XGBoost training pattern** (RESEARCH.md Pattern 1):
```python
model = xgb.XGBClassifier(
    n_estimators=300,
    max_depth=4,
    learning_rate=0.05,
    scale_pos_weight=19,
    enable_categorical=True,
    tree_method="hist",        # required for enable_categorical
    random_state=42,
    eval_metric="logloss",
)
model.fit(X_train_enc.values, y_train.values)
```

**Calibration pattern — FrozenEstimator (RESEARCH.md Pattern 2):**
```python
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator

# CRITICAL: use FrozenEstimator, NOT cv='prefit' (removed in sklearn 1.8)
calibrated = CalibratedClassifierCV(
    FrozenEstimator(model),
    method="sigmoid",   # Platt scaling
)
calibrated.fit(X_test_enc.values, y_test.values)
```

**Artifact serialization pattern** (RESEARCH.md Pattern 5):
```python
import joblib
from pathlib import Path

artifact = {
    "model": calibrated,
    "feature_cols": feature_cols,   # ordered list
    "cat_cols": cat_cols,
    "encoder": encoder,             # fitted OrdinalEncoder — Phase 3 calls encoder.transform()
    "trained_on": "2022-2024",
    "tested_on": "2025",
}
Path("data/models").mkdir(parents=True, exist_ok=True)
joblib.dump(artifact, "data/models/prerace_model.joblib")
```

---

### `src/f1_prediction/ml/evaluate.py` (utility, batch/transform)

**Analog:** No direct codebase analog. Use RESEARCH.md patterns.

**Log loss pattern** (RESEARCH.md Don't Hand-Roll):
```python
from sklearn.metrics import log_loss

proba = calibrated.predict_proba(X_test_enc.values)
ll = log_loss(y_test.values, proba[:, 1])
```

**Top-3 hit rate pattern** (RESEARCH.md full scaffold, lines 491–498):
```python
hits = 0
sessions = test_df["session_key"].unique()
for sk in sessions:
    sg = test_df[test_df["session_key"] == sk]
    top3 = sg.nlargest(3, "win_prob")["label"].sum()
    if top3 > 0:
        hits += 1
top3_rate = hits / len(sessions) if len(sessions) > 0 else 0.0
```

**Per-session softmax pattern** (RESEARCH.md Pattern 3 + Pitfall 4):
```python
from scipy.special import softmax

# IMPORTANT: apply per session_key, not globally (see RESEARCH.md Pitfall 4)
# Correct pattern:
test_df["win_prob"] = test_df.groupby("session_key")["raw_win_score"].transform(
    lambda g: softmax(g.values)
)
```

**Print output pattern** (D-16: stdout, not logging):
```python
print(f"prerace  log_loss={ll:.4f}  top3_hit_rate={top3_rate:.3f}  ({hits}/{len(sessions)} races)")
print(f"inrace   log_loss={ll:.4f}  top3_hit_rate={top3_rate:.3f}  ({hits}/{len(sessions)} races)")
```

---

### `pyproject.toml` (modified — add xgboost dependency)

**Analog:** `pyproject.toml` lines 6–14 (existing dependency block).

**Current dependency block pattern** (`pyproject.toml` lines 6–14):
```toml
dependencies = [
    "fastf1==3.8.1",
    "pandas==2.3.3",
    "numpy==2.4.3",
    "apscheduler==3.11.2",
    "httpx==0.28.1",
    "sqlalchemy==2.0.51",
    "tenacity==9.1.4",
]
```

**Required addition** (D-01, RESEARCH.md Standard Stack):
```toml
    "xgboost>=3.0,<4.0",
    "scikit-learn>=1.8,<2.0",
    "scipy>=1.17,<2.0",
    "joblib>=1.5,<2.0",
```

Note: scikit-learn, scipy, joblib are already installed in the venv but absent from `pyproject.toml`. They should be added for reproducibility. xgboost is the only hard-missing dependency.

---

## Shared Patterns

### DB Connection (applies to `loader.py`, `scripts/train.py`)
**Source:** `src/f1_prediction/db/engine.py` lines 30–51
```python
from f1_prediction.db.engine import get_engine

engine = get_engine()
with engine.connect() as conn:
    df = pd.read_sql(text("SELECT ..."), conn)
```

### Parameterized SQL — Security T-03-05 (applies to all SQL in `loader.py`)
**Source:** `src/f1_prediction/features/prerace.py` lines 55–62
```python
# NEVER f-string SQL. Always use text() with bound :param names.
q = text("SELECT ... WHERE session_key = :sk")
conn.execute(q, {"sk": session_key})
# or with pd.read_sql:
df = pd.read_sql(text("SELECT ... WHERE year <= :year"), conn, params={"year": 2024})
```

### Module header (applies to all new `src/` files)
**Source:** `src/f1_prediction/features/prerace.py` lines 1–21
```python
"""Module docstring."""
from __future__ import annotations

import logging
...

logger = logging.getLogger(__name__)
```

### sys.path bootstrap (applies to `scripts/train.py`)
**Source:** `scripts/build_features.py` line 29
```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `src/f1_prediction/ml/pipeline.py` | service | batch/transform | No ML training code exists in the codebase yet — greenfield |
| `src/f1_prediction/ml/evaluate.py` | utility | batch/transform | No evaluation or metrics code exists yet — greenfield |

For these files, use RESEARCH.md Code Examples section (full pre-race training scaffold, lines 398–512) as the primary pattern reference.

---

## Key Anti-Patterns to Avoid

These are called out explicitly because the context documents warn about them:

1. **`cv='prefit'` in CalibratedClassifierCV** — removed in sklearn 1.8. Use `FrozenEstimator` only.
2. **`unknown_value=-1` in OrdinalEncoder** — use `np.nan` to align with XGBoost's missing sentinel.
3. **OrdinalEncoder fitted before train/test split** — fit on `train_df` only.
4. **Softmax applied globally** — must be per `session_key` group.
5. **`finish_position` included in `feature_cols`** — it is the target; exclude it, `session_key`, `driver_number`, and `year`.
6. **Empty DataFrame not guarded** — raise `RuntimeError` if `len(train_df) == 0`.

---

## Metadata

**Analog search scope:** `scripts/`, `src/f1_prediction/`
**Files scanned:** 6 source files read
**Pattern extraction date:** 2026-06-17
