"""
Unit tests for validate_model.py — validate_sum_to_one and _assert_sums_within_tolerance.

All tests use synthetic numpy / pandas inputs — no DB access, no joblib.load.
These tests run in milliseconds and isolate the math from the I/O.

Covers:
  - Uniform distribution (20 drivers × 2 sessions → n_groups=2, sums≈1.0)
  - Varied distribution (random probabilities still sum to 1.0 after softmax)
  - Artificial violation path via _assert_sums_within_tolerance directly
  - Multi-column grouping (session_key × lap_number → n_groups=4)
  - min_sum and max_sum are within tolerance of 1.0 after successful validation
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Make scripts/validate_model.py importable without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_model import _assert_sums_within_tolerance, validate_sum_to_one  # noqa: E402


# ---------------------------------------------------------------------------
# Test 1: Uniform distribution
# ---------------------------------------------------------------------------

def test_validate_sum_to_one_passes_for_uniform_distribution():
    """
    Uniform raw probabilities (0.5 for all drivers) must produce valid per-race
    softmax sums. After softmax, each driver gets 1/20 ≈ 0.05 and the 20 drivers
    in each session sum to 1.0 exactly.
    """
    n_sessions = 2
    n_drivers = 20
    n_rows = n_sessions * n_drivers  # 40

    raw_proba = np.full(n_rows, 0.5)
    group_keys = pd.DataFrame({
        "session_key": [1] * n_drivers + [2] * n_drivers,
    })

    result = validate_sum_to_one(raw_proba, group_keys, ["session_key"], tolerance=0.001)

    assert result["n_groups"] == 2, f"Expected 2 groups, got {result['n_groups']}"
    assert result["violations"] == [], f"Expected no violations: {result['violations']}"
    assert abs(result["min_sum"] - 1.0) < 0.001, f"min_sum too far from 1.0: {result['min_sum']}"
    assert abs(result["max_sum"] - 1.0) < 0.001, f"max_sum too far from 1.0: {result['max_sum']}"


# ---------------------------------------------------------------------------
# Test 2: Varied distribution
# ---------------------------------------------------------------------------

def test_validate_sum_to_one_passes_for_varied_distribution():
    """
    After per-session softmax, sums must equal 1.0 regardless of how varied
    the raw probabilities are. Softmax is sum-to-1 by construction.
    """
    n_sessions = 2
    n_drivers = 20
    n_rows = n_sessions * n_drivers  # 40

    rng = np.random.RandomState(42)
    raw_proba = rng.rand(n_rows)

    group_keys = pd.DataFrame({
        "session_key": [1] * n_drivers + [2] * n_drivers,
    })

    # Should not raise — softmax is sum-to-1 by definition
    result = validate_sum_to_one(raw_proba, group_keys, ["session_key"], tolerance=0.001)

    assert result["violations"] == []
    # Softmax is exact within float64 precision — sums should be extremely close to 1.0
    assert abs(result["min_sum"] - 1.0) < 1e-10, f"min_sum not ~1.0: {result['min_sum']}"
    assert abs(result["max_sum"] - 1.0) < 1e-10, f"max_sum not ~1.0: {result['max_sum']}"


# ---------------------------------------------------------------------------
# Test 3: Artificial violation path via _assert_sums_within_tolerance
# ---------------------------------------------------------------------------

def test_validate_sum_to_one_raises_for_artificial_violation():
    """
    Directly exercise the violation-detection path by calling
    _assert_sums_within_tolerance with a manufactured pd.Series whose values
    are NOT within tolerance of 1.0.

    This bypasses softmax entirely — the whole point is to test the assertion
    helper in isolation without going through softmax (which would make a
    violation mathematically impossible to construct).
    """
    # Groups (2,) and (3,) violate at 0.9 and 1.05 respectively
    violating_sums = pd.Series(
        {(1,): 1.0, (2,): 0.9, (3,): 1.05},
        name="win_prob",
    )

    with pytest.raises(AssertionError) as exc_info:
        _assert_sums_within_tolerance(violating_sums, tolerance=0.001)

    error_msg = str(exc_info.value)
    # The error message must name the offending groups and their sums
    assert "2" in error_msg or "(2,)" in error_msg, (
        f"Expected offending group '2' in error: {error_msg}"
    )
    assert "0.9" in error_msg, f"Expected violating sum '0.9' in error: {error_msg}"

    # Positive path: valid sums must NOT raise
    valid_sums = pd.Series(
        {(1,): 1.0, (2,): 1.0},
        name="win_prob",
    )
    result = _assert_sums_within_tolerance(valid_sums, tolerance=0.001)
    assert result is None, "Expected None return on valid sums"


# ---------------------------------------------------------------------------
# Test 4: Multi-column grouping (session_key × lap_number)
# ---------------------------------------------------------------------------

def test_validate_sum_to_one_grouping_by_multiple_columns():
    """
    In-race style: group by (session_key, lap_number). With 2 sessions × 2 laps
    × N drivers, there should be 4 groups total.
    """
    n_drivers = 20
    # 2 sessions × 2 laps × 20 drivers = 80 rows
    rows = []
    for session in [1, 2]:
        for lap in [10, 20]:
            for _ in range(n_drivers):
                rows.append({"session_key": session, "lap_number": lap})

    group_keys = pd.DataFrame(rows)
    raw_proba = np.random.RandomState(7).rand(len(group_keys))

    result = validate_sum_to_one(
        raw_proba,
        group_keys,
        ["session_key", "lap_number"],
        tolerance=0.001,
    )

    assert result["n_groups"] == 4, f"Expected 4 groups (2 sessions × 2 laps), got {result['n_groups']}"
    assert result["violations"] == []


# ---------------------------------------------------------------------------
# Test 5: min_sum and max_sum are within tolerance
# ---------------------------------------------------------------------------

def test_validate_sum_to_one_returns_min_max_sums():
    """
    After successful validation, min_sum and max_sum must be within tolerance
    of 1.0. Validates the return dict contract.
    """
    n_drivers = 20
    n_sessions = 3
    raw_proba = np.random.RandomState(99).rand(n_sessions * n_drivers)
    group_keys = pd.DataFrame({
        "session_key": [i for i in range(1, n_sessions + 1) for _ in range(n_drivers)],
    })

    result = validate_sum_to_one(raw_proba, group_keys, ["session_key"], tolerance=0.001)

    assert result["min_sum"] >= 1.0 - 0.001, (
        f"min_sum {result['min_sum']} is below tolerance floor"
    )
    assert result["max_sum"] <= 1.0 + 0.001, (
        f"max_sum {result['max_sum']} is above tolerance ceiling"
    )
    assert "n_groups" in result
    assert "violations" in result
    assert result["violations"] == []
