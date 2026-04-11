"""Tests for eval/rerun_analysis.py.

Run with:
    python3 -m pytest eval/test_rerun_analysis.py -q
"""

from __future__ import annotations

import os
import sys

# Allow running from the repo root without installing the package.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from rerun_analysis import (  # noqa: E402
    min_r_for_z,
    newcombe_wilson_diff_ci,
    two_prop_z,
    wilson_interval,
)


# ---------------------------------------------------------------------------
# Wilson interval
# ---------------------------------------------------------------------------

def test_wilson_interval_7_of_97():
    """7/97 Wilson 95% CI is approximately (0.035, 0.142).

    Reference fixture: Wilson (1927) / Newcombe (1998) example tables
    and the R binom::binom.confint default agree on ~(0.0353, 0.1425)
    for x=7, n=97.
    """
    lo, hi = wilson_interval(7, 97)
    assert abs(lo - 0.035) < 0.005, f"lo={lo}"
    assert abs(hi - 0.142) < 0.005, f"hi={hi}"


def test_wilson_interval_zero_success_edge_case():
    """x=0 should give a lower bound exactly at 0.0."""
    lo, hi = wilson_interval(0, 10)
    assert lo == 0.0
    assert hi > 0.0


def test_wilson_interval_full_success_edge_case():
    """x=n should give an upper bound exactly at 1.0."""
    lo, hi = wilson_interval(10, 10)
    assert hi == 1.0
    assert lo < 1.0


def test_wilson_interval_is_ordered():
    """lo <= phat <= hi for a generic interior case."""
    lo, hi = wilson_interval(9, 97)
    assert 0.0 <= lo <= 9 / 97 <= hi <= 1.0


# ---------------------------------------------------------------------------
# Two-proportion z-test
# ---------------------------------------------------------------------------

def test_two_prop_z_negative_when_treatment_worse():
    """Treatment below baseline should give a negative z."""
    z = two_prop_z(5, 97, 7, 97)
    assert z < 0


def test_two_prop_z_9_vs_7_out_of_97():
    """9/97 vs 7/97 pooled z is ~0.52."""
    z = two_prop_z(9, 97, 7, 97)
    assert abs(z - 0.52) < 0.02, f"z={z}"


# ---------------------------------------------------------------------------
# Minimum-R planning helper
# ---------------------------------------------------------------------------

def test_min_r_for_z_two_sided_matches_gpt_deep_research():
    """Two-sided z-test for +2/97 lift with pooled rate 8/97 needs ~15 reruns."""
    r = min_r_for_z(
        delta=2 / 97,
        tasks_per_run=97,
        zcrit=1.96,
        p_pool_hint=8 / 97,
    )
    assert abs(r - 15) <= 2, f"R={r}"


def test_min_r_for_z_one_sided_is_smaller_than_two_sided():
    """One-sided test should never require more reruns than two-sided."""
    r2 = min_r_for_z(2 / 97, 97, 1.96, 8 / 97)
    r1 = min_r_for_z(2 / 97, 97, 1.645, 8 / 97)
    assert r1 <= r2
    assert abs(r1 - 10) <= 2, f"one-sided R={r1}"


# ---------------------------------------------------------------------------
# Newcombe Wilson difference CI
# ---------------------------------------------------------------------------

def test_newcombe_diff_ci_positive_at_r29():
    """With R=29, scaled 9/97 vs 7/97 diff CI lower bound should be > 0."""
    r = 29
    n = 97 * r
    lo, _ = newcombe_wilson_diff_ci(9 * r, n, 7 * r, n)
    assert lo > 0.0, f"lo={lo}"


def test_newcombe_diff_ci_negative_at_r4():
    """With only R=4, the diff CI lower bound should still be negative."""
    r = 4
    n = 97 * r
    lo, _ = newcombe_wilson_diff_ci(9 * r, n, 7 * r, n)
    assert lo < 0.0, f"lo={lo}"
