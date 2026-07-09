# Phase 2: Model Training - Pattern Map

**Mapped:** 2026-06-24
**Files analyzed:** 7
**Analogs found:** 6 / 7

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `scripts/backfill.py` (MODIFY) | utility/CLI | batch | `scripts/backfill.py` itself | exact (self) |
| `scripts/train.py` (NEW) | utility/CLI | batch | `scripts/build_features.py` | role-match |
| `scripts/validate_model.py` (NEW) | utility/CLI | batch | `scripts/build_features.py` | role-match |
| `data/models/prerace_model.joblib` (artifact) | — | — | — | n/a (output file) |
| `data/models/inrace_model.joblib` (artifact) | — | — | — | n/a (output file) |
| `tests/test_train.py` (NEW) | test | — | `tests/test_features.py` | role-match |
| `tests/test_backfill_extended.py` (NEW) | test | — | `tests/test_features.py` | role-match |

---

## Pattern Assignments

### `scripts/backfill.py` (MODIFY — add `fix_round_numbers` call)

**Analog:** `scripts/backfill.py` (self — lines 1–166)

The modification adds one new function `fix_round_numbers(engine)` and one new call site after the OpenF1 path completes. No changes to the CLI interface.

**Existing script structure pattern** (lines 33–46):
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
from f1_prediction.ingest import fastf1_loader, openf1_loader  # noqa: E402

logger = logging.getLogger(__name__)
```

**Logging setup pattern** (lines 104–109):
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
```

**Error handling + exit code pattern** (lines 110–162):
```python
had_error = False
# ... loop that sets had_error = True on exceptions ...
sys.exit(1 if had_error else 0)
```

**SQLAlchemy text() with bound params** (from `src/f1_prediction/features/prerace.py`, lines 55–58):
```python
race_meta_q = text(
    "SELECT year, round_number FROM raw_sessions WHERE session_key = :sk"
)
meta = conn.execute(race_meta_q, {"sk": session_key}).fetchone()
```

**New function to insert into backfill.py** (from RESEARCH.md — Backfill Extension Design):
```python
from collections import defaultdict
from sqlalchemy import text
from f1_prediction.db.engine import get_engine

def fix_round_numbers(engine):
    """
    Derive round_number for OpenF1 sessions from meeting_key rank within year.
    OpenF1 /v1/sessions does not return round_number; meeting_keys are
    sequential within a year and can be ranked to approximate round order.
    Run AFTER backfill, BEFORE build_features.
    """
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT session_key, year, meeting_key
            FROM raw_sessions
            WHERE year >= 2023 AND meeting_key IS NOT NULL
            ORDER BY year, meeting_key
        """)).fetchall()

        year_meeting_keys = defaultdict(list)
        for sk, year, mk in rows:
            if mk not in year_meeting_keys[year]:
                year_meeting_keys[year].append(mk)

        year_meeting_rank = {}
        for year, mks in year_meeting_keys.items():
            for rank, mk in enumerate(sorted(set(mks)), start=1):
                year_meeting_rank[(year, mk)] = rank

        for sk, year, mk in rows:
            rn = year_meeting_rank.get((year, mk), 0)
            conn.execute(
                text("UPDATE raw_sessions SET round_number = :rn WHERE session_key = :sk"),
                {"rn": rn, "sk": sk},
            )
```

**Call site location:** After the `for year in args.years:` loop completes (before `build_all_features` call), add:
```python
# Fix round_number=0 for all 2023+ sessions before feature build
if any(y >= 2023 for y in args.years):
    from f1_prediction.db.engine import get_engine
    fix_round_numbers(get_engine())
    logger.info("round_number fix applied for 2023+ sessions")
```

---

### `scripts/train.py` (NEW — main training script)

**Analog:** `scripts/build_features.py` (lines 1–75) — same CLI script role, same one-shot batch data flow.

**Module docstring pattern** (from `scripts/build_features.py`, lines 1–18):
```python
"""
Standalone feature builder CLI (D-13).

...

Usage:
    # Rebuild features for all available raw data:
    python scripts/build_features.py

    ...

Exit codes:
    0: success
    1: exception raised during feature build
"""
```

**Imports + sys.path pattern** (from `scripts/build_features.py`, lines 20–31):
```python
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure src/ is on the path for non-installed runs
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

**argparse + main() pattern** (from `scripts/build_features.py`, lines 34–75):
```python
def main() -> None:
    """CLI entrypoint for the standalone feature builder."""
    parser = argparse.ArgumentParser(
        description="...",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--only-years",
        type=int,
        nargs="+",
        default=None,
        metavar="YEAR",
        help="...",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

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

**get_engine() import pattern** (from `src/f1_prediction/db/engine.py`, line 30):
```python
from f1_prediction.db.engine import get_engine
```

**SQLAlchemy data load pattern** (from `src/f1_prediction/features/prerace.py`, lines 54–104):
```python
with engine.connect() as conn:
    df = pd.read_sql(text("""
        SELECT ...
        FROM features_prerace fp
        JOIN raw_sessions rs ON fp.session_key = rs.session_key
        WHERE rs.session_type = 'Race'
          AND fp.finish_position IS NOT NULL
          AND rs.year <= :max_year
        ORDER BY rs.year, rs.round_number, fp.driver_number
    """), conn, params={"max_year": max_year})
```

**Core ML pipeline pattern** (from RESEARCH.md — Pattern 4 and Code Examples):
```python
import xgboost as xgb
from sklearn.calibration import CalibratedClassifierCV
from scipy.special import softmax
import joblib

# CRITICAL: cv=5 integer, NOT cv='prefit' (removed in sklearn 1.x)
# CRITICAL: scale_pos_weight=~19, NOT is_unbalance=True (LightGBM only)
base_clf = xgb.XGBClassifier(
    n_estimators=500,
    learning_rate=0.05,
    max_depth=6,
    scale_pos_weight=scale_pos_weight,   # (n_total - n_pos) / n_pos
    objective="binary:logistic",
    eval_metric="logloss",
    verbosity=0,
)
model = CalibratedClassifierCV(base_clf, cv=5, method="isotonic")
model.fit(X_train, y_train)

# Softmax per race (NOT globally across all rows)
raw_proba = model.predict_proba(X_val)[:, 1]
df["win_prob"] = df.groupby("session_key")["raw_prob"].transform(
    lambda x: softmax(x.values)
)

# Artifact save
Path(model_path).parent.mkdir(parents=True, exist_ok=True)
joblib.dump(model, model_path)
```

**is_winner derivation SQL — lap-completion filter** (from RESEARCH.md — Pattern 1, verified against data/f1.db):
```sql
CASE
    WHEN fp.finish_position = 1
     AND (
        SELECT MAX(rp2.lap_number)
        FROM raw_positions rp2
        WHERE rp2.session_key = fp.session_key
          AND rp2.driver_number = fp.driver_number
     ) >= rs.total_laps - 1
    THEN 1
    ELSE 0
END AS is_winner
```

**Tyre one-hot encoding** (from RESEARCH.md — Pattern 3):
```python
compound_dummies = pd.get_dummies(
    df["tyre_compound"],
    prefix="tyre",
    dummy_na=False,  # NaN rows become all-zero
)
# Produces: tyre_HARD, tyre_INTERMEDIATE, tyre_MEDIUM, tyre_SOFT, tyre_WET
X = pd.concat([df[numeric_cols], compound_dummies], axis=1)
```

**Season-boundary CV folds** (from RESEARCH.md — Pattern 5):
```python
def season_folds(df, year_col="year"):
    years = sorted(df[year_col].unique())
    folds = []
    for i in range(1, len(years)):
        t_mask = df[year_col].isin(years[:i])
        v_mask = df[year_col] == years[i]
        folds.append((t_mask, v_mask))
    return folds
```

**--model flag** (new argparse arg following backfill.py's --years pattern):
```python
parser.add_argument(
    "--model",
    choices=["prerace", "inrace"],
    required=True,
    help="Which model to train: prerace or inrace.",
)
```

---

### `scripts/validate_model.py` (NEW — sum-to-1 + metrics validation)

**Analog:** `scripts/build_features.py` (same one-shot CLI pattern)

**Script skeleton:** Copy the docstring + argparse + main() + try/except + sys.exit pattern verbatim from `scripts/build_features.py` lines 1–75. Replace the body with model loading and validation logic.

**Model loading pattern**:
```python
import joblib
model = joblib.load("data/models/prerace_model.joblib")
```

**Sum-to-1 validation pattern** (from RESEARCH.md — Code Examples):
```python
from scipy.special import softmax

def validate_sum_to_one(model, X_val, session_keys, tolerance=0.001):
    raw_proba = model.predict_proba(X_val)[:, 1]
    df = pd.DataFrame({"session_key": session_keys, "raw_prob": raw_proba})
    df["win_prob"] = df.groupby("session_key")["raw_prob"].transform(
        lambda x: softmax(x.values)
    )
    sums = df.groupby("session_key")["win_prob"].sum()
    violations = sums[(sums - 1.0).abs() > tolerance]
    if not violations.empty:
        raise AssertionError(
            f"Sum-to-1 violation for session_keys: {violations.index.tolist()}\n"
            f"Sums: {violations.values}"
        )
    print(f"Sum-to-1 PASSED for {len(sums)} races "
          f"(min={sums.min():.6f}, max={sums.max():.6f})")
    return True
```

**Metrics output pattern**:
```python
from sklearn.metrics import brier_score_loss, log_loss
brier = brier_score_loss(y_val, raw_proba)
ll = log_loss(y_val, raw_proba)
print(json.dumps({"brier_score": round(brier, 4), "log_loss": round(ll, 4)}))
```

---

### `tests/test_train.py` (NEW — covers PRERACE-01 through INRACE-03)

**Analog:** `tests/test_features.py` (lines 1–80) — same fixture-seeded in-memory DB pattern.

**Test file header pattern** (from `tests/test_features.py`, lines 1–24):
```python
"""
Tests for ... (Plan XX, Task Y).

Covers:
  - [list of behaviors]

All tests use the tmp_engine fixture (in-memory database) — no data/f1.db created.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from f1_prediction.db.engine import get_engine, init_db
```

**conftest.py fixture reuse pattern** (from `tests/conftest.py`, lines 15–30):
```python
# tests/conftest.py provides tmp_engine fixture; reuse it in test_train.py
# Do NOT redefine tmp_engine in test_train.py — import it implicitly via conftest.
@pytest.fixture
def tmp_engine(tmp_path):
    db_path = tmp_path / "test.db"
    engine = get_engine(db_path)
    init_db(engine)
    yield engine
    engine.dispose()
```

**Fixture with seeded data pattern** (from `tests/test_features.py`, lines 32–78):
```python
@pytest.fixture()
def train_engine(tmp_path):
    """
    In-memory SQLite engine seeded with minimal training data:
    - 2 race weekends (2022 round 1, 2023 round 1) — 2 seasons for fold testing
    - 3 drivers per race (driver_number: 1, 11, 44)
    - finish_position seeded (driver 1 wins both races)
    - raw_positions laps seeded for lap-completion filter
    """
    db_path = tmp_path / "test_train.db"
    engine = get_engine(db_path)
    init_db(engine)
    _seed_training_data(engine)
    return engine
```

**Test structure pattern** (from `tests/test_features.py`):
```python
def test_prerace_feature_set(train_engine):
    """PRERACE-01: model trains on the correct 7 feature columns."""
    # Arrange: load from seeded DB
    # Act: call load function / training function
    # Assert: feature matrix has correct columns, shape
    ...

def test_prerace_sum_to_one(train_engine):
    """PRERACE-02: per-race probabilities sum to 1.0 ± 0.001."""
    ...

def test_prerace_artifact_exists(tmp_path, train_engine):
    """PRERACE-03: model artifact written to data/models/prerace_model.joblib."""
    model_path = tmp_path / "prerace_model.joblib"
    # train and save
    assert model_path.exists()
    import joblib
    loaded = joblib.load(model_path)
    assert loaded is not None
```

---

### `tests/test_backfill_extended.py` (NEW — verifies row counts post-backfill)

**Analog:** `tests/test_features.py` — same pattern but this is an integration test against the real `data/f1.db` (not tmp_engine), similar to the spirit of `scripts/check_backfill.py`.

**Integration test against real DB pattern** (from `scripts/check_backfill.py`, lines 1–31 — note: this is a script, not a pytest file, but the query pattern applies):
```python
import sqlite3
conn = sqlite3.connect("data/f1.db")
# Query features_prerace by year to check row counts
```

**Pytest integration test pattern** (adapt for pytest):
```python
"""
Integration tests for backfill extension (D-01).

These tests verify that after running Plan 01's backfill extension,
features_prerace has rows for multiple rounds in 2023, 2024, and 2025.

IMPORTANT: These tests require a populated data/f1.db (post-backfill).
They are skipped automatically if the DB has fewer than 100 rows per year.
"""
import pytest
from sqlalchemy import text
from f1_prediction.db.engine import get_engine

@pytest.fixture(scope="module")
def real_engine():
    """Connect to the real data/f1.db (read-only queries only)."""
    return get_engine()

def test_prerace_rows_2023(real_engine):
    """Post-backfill: 2023 should have > 1 race (multiple rounds)."""
    with real_engine.connect() as conn:
        count = conn.execute(text(
            "SELECT COUNT(*) FROM features_prerace fp "
            "JOIN raw_sessions rs ON fp.session_key = rs.session_key "
            "WHERE rs.year = 2023 AND rs.session_type = 'Race'"
        )).scalar()
    assert count > 20, f"Expected >20 rows for 2023, got {count}"
```

---

## Shared Patterns

### get_engine() import
**Source:** `src/f1_prediction/db/engine.py` (line 30 — function signature)
**Apply to:** `scripts/train.py`, `scripts/validate_model.py`, `tests/test_backfill_extended.py`, `fix_round_numbers` in `backfill.py`
```python
from f1_prediction.db.engine import get_engine
```

### sys.path insert for scripts/
**Source:** `scripts/backfill.py` lines 42–43, `scripts/build_features.py` lines 28–29
**Apply to:** `scripts/train.py`, `scripts/validate_model.py`
```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

### SQL via text() with bound :param names (T-03-05)
**Source:** `src/f1_prediction/features/prerace.py` throughout (e.g., lines 55–58, 88–97, 205–218)
**Apply to:** ALL SQL in `scripts/train.py`, `fix_round_numbers` in `backfill.py`, `scripts/validate_model.py`
```python
# CORRECT — parameterized:
conn.execute(text("SELECT ... WHERE year = :yr"), {"yr": year})

# FORBIDDEN — f-string SQL:
conn.execute(f"SELECT ... WHERE year = {year}")
```

### Logging setup
**Source:** `scripts/backfill.py` lines 104–109, `scripts/build_features.py` lines 59–63
**Apply to:** `scripts/train.py`, `scripts/validate_model.py`
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)
```

### from __future__ import annotations + Path
**Source:** `scripts/backfill.py` line 33, `src/f1_prediction/features/prerace.py` lines 12–16
**Apply to:** All new files
```python
from __future__ import annotations
from pathlib import Path
```

### Test isolation (tmp_engine, no data/f1.db writes)
**Source:** `tests/conftest.py` lines 15–30
**Apply to:** `tests/test_train.py` (unit tests use tmp_engine or small seeded fixture)
- `tests/test_backfill_extended.py` is an exception — it reads the real DB but must never write to it.

---

## Anti-Patterns to Avoid (from RESEARCH.md)

| Anti-Pattern | Correct Pattern | Source |
|---|---|---|
| `CalibratedClassifierCV(clf, cv='prefit')` | `CalibratedClassifierCV(clf, cv=5, method='isotonic')` | RESEARCH.md Pitfall 2; sklearn 1.9.0 raises `InvalidParameterError` |
| `XGBClassifier(is_unbalance=True)` | `XGBClassifier(scale_pos_weight=~19)` | RESEARCH.md Pitfall 7; `is_unbalance` is LightGBM-only |
| `CASE WHEN finish_position = 1 THEN 1 END` without lap filter | Add `MAX(lap_number) >= total_laps - 1` subquery | RESEARCH.md Pitfall 3; Le Castellet 2022 DNF bug |
| `softmax(all_rows)` globally | `df.groupby("session_key").transform(lambda x: softmax(x.values))` | RESEARCH.md Pitfall 4 |
| `.dropna()` on feature columns | Leave NULLs as `np.nan`; XGBoost handles natively | RESEARCH.md Pitfall 5 |
| f-string SQL | `text()` with `:param` names | T-03-05 project convention |

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `data/models/prerace_model.joblib` | artifact | — | Binary output file; no code analog needed |
| `data/models/inrace_model.joblib` | artifact | — | Binary output file; no code analog needed |

---

## Metadata

**Analog search scope:** `scripts/`, `src/f1_prediction/db/`, `src/f1_prediction/features/`, `tests/`
**Files scanned:** 6 source files read in full
**Pattern extraction date:** 2026-06-24
