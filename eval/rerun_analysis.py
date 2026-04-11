#!/usr/bin/env python3
"""Rerun sizing analysis for tau3-bench improvement claims.

Standalone, stdlib-only. Given a baseline and treatment pass count on a fixed
number of binary tasks, compute how many independent reruns per variant are
needed to defensibly claim the treatment is better than the baseline.

Usage
-----
    python3 eval/rerun_analysis.py
    python3 eval/rerun_analysis.py <baseline_x> <treatment_x> <tasks>

Examples
--------
    python3 eval/rerun_analysis.py           # default 7/97 -> 9/97 scenario
    python3 eval/rerun_analysis.py 7 9 97    # same, explicit
    python3 eval/rerun_analysis.py 10 14 97  # a +4 lift scenario
"""

from __future__ import annotations

import math
import sys
from typing import Tuple


# ---------------------------------------------------------------------------
# Core statistical primitives
# ---------------------------------------------------------------------------

def _phi(z: float) -> float:
    """Standard-normal CDF using math.erf."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def wilson_interval(x: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score 95% CI for a single proportion.

    Parameters
    ----------
    x : int
        Number of successes.
    n : int
        Number of trials.
    z : float
        Normal critical value (default 1.96 for 95%).

    Returns
    -------
    (lo, hi) : tuple of floats
        Lower and upper bounds of the Wilson score interval, clipped to [0, 1].
    """
    if n <= 0:
        return (0.0, 1.0)
    phat = x / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(phat * (1.0 - phat) / n + z2 / (4.0 * n * n))
    lo = max(0.0, center - half)
    hi = min(1.0, center + half)
    return (lo, hi)


def two_prop_z(x1: int, n1: int, x0: int, n0: int) -> float:
    """Pooled-variance two-proportion z-statistic (treatment minus baseline).

    z = (p1 - p0) / sqrt( p_pool * (1 - p_pool) * (1/n1 + 1/n0) )
    with p_pool = (x1 + x0) / (n1 + n0).
    """
    if n1 <= 0 or n0 <= 0:
        raise ValueError("n1 and n0 must be positive")
    p1 = x1 / n1
    p0 = x0 / n0
    p_pool = (x1 + x0) / (n1 + n0)
    var = p_pool * (1.0 - p_pool) * (1.0 / n1 + 1.0 / n0)
    if var <= 0.0:
        # Degenerate pooled variance (e.g. both at 0% or 100%).
        if p1 == p0:
            return 0.0
        return math.inf if p1 > p0 else -math.inf
    return (p1 - p0) / math.sqrt(var)


def two_prop_pvalue(
    x1: int, n1: int, x0: int, n0: int, two_sided: bool = True
) -> float:
    """p-value for H0: p1 == p0 (two-sided) or H0: p1 <= p0 (one-sided upper).

    Uses the pooled-variance z-statistic and the normal CDF built from math.erf.
    """
    z = two_prop_z(x1, n1, x0, n0)
    if math.isinf(z):
        return 0.0 if z > 0 else 1.0
    if two_sided:
        return 2.0 * (1.0 - _phi(abs(z)))
    return 1.0 - _phi(z)


def newcombe_wilson_diff_ci(
    x1: int, n1: int, x0: int, n0: int, z: float = 1.96
) -> Tuple[float, float]:
    """Newcombe method 10: difference CI from two Wilson intervals.

    Given W1 = (L1, U1) and W0 = (L0, U0), the CI for p1 - p0 is
    (L1 - U0, U1 - L0).
    """
    l1, u1 = wilson_interval(x1, n1, z=z)
    l0, u0 = wilson_interval(x0, n0, z=z)
    return (l1 - u0, u1 - l0)


def min_r_for_z(
    delta: float, tasks_per_run: int, zcrit: float, p_pool_hint: float
) -> int:
    """Minimum reruns per variant for a pooled-variance z-test to reach zcrit.

    Solves for R in
        (delta) / sqrt( 2 * p_pool * (1 - p_pool) / (R * tasks_per_run) ) >= zcrit
    which rearranges to
        R >= 2 * p_pool * (1 - p_pool) * (zcrit / delta)^2 / tasks_per_run.

    Parameters
    ----------
    delta : float
        Assumed true effect size as a proportion difference (e.g. 2/97).
    tasks_per_run : int
        Number of tasks in a single eval run (fixed bench size).
    zcrit : float
        Critical z value (e.g. 1.96 for two-sided alpha=0.05, 1.645 one-sided).
    p_pool_hint : float
        Planning estimate of the pooled pass rate (e.g. midpoint 8/97).

    Returns
    -------
    int
        Smallest R >= 1 that satisfies the inequality.
    """
    if delta <= 0:
        raise ValueError("delta must be positive")
    if tasks_per_run <= 0:
        raise ValueError("tasks_per_run must be positive")
    numerator = 2.0 * p_pool_hint * (1.0 - p_pool_hint) * (zcrit / delta) ** 2
    r = numerator / tasks_per_run
    return max(1, math.ceil(r))


def _min_r_for_newcombe_positive(
    baseline_x: int,
    treat_x: int,
    tasks_per_run: int,
    max_r: int = 100,
    z: float = 1.96,
) -> int:
    """Smallest R in [1, max_r] such that the Newcombe diff CI lower bound > 0.

    Returns 0 if no R in the range works (caller can treat that as "infeasible").
    """
    for r in range(1, max_r + 1):
        n = r * tasks_per_run
        x0 = baseline_x * r
        x1 = treat_x * r
        lo, _ = newcombe_wilson_diff_ci(x1, n, x0, n, z=z)
        if lo > 0.0:
            return r
    return 0


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _fmt_pct(p: float) -> str:
    return f"{100.0 * p:6.2f}%"


def _print_single_run_wilson(baseline_x: int, treat_x: int, tasks: int) -> None:
    lo_b, hi_b = wilson_interval(baseline_x, tasks)
    lo_t, hi_t = wilson_interval(treat_x, tasks)
    print("Single-run Wilson 95% CIs")
    print("-" * 60)
    print(
        f"  baseline  {baseline_x}/{tasks} = {_fmt_pct(baseline_x / tasks)}   "
        f"CI = [{_fmt_pct(lo_b)}, {_fmt_pct(hi_b)}]"
    )
    print(
        f"  treatment {treat_x}/{tasks} = {_fmt_pct(treat_x / tasks)}   "
        f"CI = [{_fmt_pct(lo_t)}, {_fmt_pct(hi_t)}]"
    )
    print()


def _print_min_r(baseline_x: int, treat_x: int, tasks: int) -> None:
    delta = (treat_x - baseline_x) / tasks
    p_pool_hint = (baseline_x + treat_x) / (2.0 * tasks)
    r_two = min_r_for_z(delta, tasks, zcrit=1.96, p_pool_hint=p_pool_hint)
    r_one = min_r_for_z(delta, tasks, zcrit=1.645, p_pool_hint=p_pool_hint)
    r_newc = _min_r_for_newcombe_positive(baseline_x, treat_x, tasks, max_r=100)

    print("Minimum reruns per variant")
    print("-" * 60)
    print(
        f"  assumed true delta    = {delta:.4f}  "
        f"({treat_x - baseline_x} tasks out of {tasks})"
    )
    print(f"  pooled-rate planning  = {p_pool_hint:.4f}")
    print(f"  two-sided z-test (a=0.05, z=1.96)   R >= {r_two}")
    print(f"  one-sided z-test (a=0.05, z=1.645)  R >= {r_one}")
    if r_newc > 0:
        print(f"  Newcombe diff CI lower > 0          R >= {r_newc}")
    else:
        print("  Newcombe diff CI lower > 0          R > 100 (not reached)")
    print()


def _print_sanity_table(baseline_x: int, treat_x: int, tasks: int) -> None:
    print("Sanity-check table (scaled counts, assuming rate holds exactly)")
    print("-" * 60)
    print(f"{'R':>4}  {'n':>6}  {'x1':>5}  {'x0':>5}  {'z':>8}  "
          f"{'p(2s)':>8}  {'p(1s)':>8}  {'diffCI_lo':>10}")
    for r in (4, 8, 10, 15, 20, 29):
        n = r * tasks
        x0 = baseline_x * r
        x1 = treat_x * r
        z = two_prop_z(x1, n, x0, n)
        p2 = two_prop_pvalue(x1, n, x0, n, two_sided=True)
        p1 = two_prop_pvalue(x1, n, x0, n, two_sided=False)
        lo, _ = newcombe_wilson_diff_ci(x1, n, x0, n)
        print(
            f"{r:>4}  {n:>6}  {x1:>5}  {x0:>5}  {z:>8.3f}  "
            f"{p2:>8.4f}  {p1:>8.4f}  {lo:>10.4f}"
        )
    print()


def main() -> None:
    # Defaults: the tau3-bench banking scenario discussed in the task.
    baseline_x = 7
    treat_x = 9
    tasks = 97

    argv = sys.argv[1:]
    if len(argv) == 3:
        try:
            baseline_x = int(argv[0])
            treat_x = int(argv[1])
            tasks = int(argv[2])
        except ValueError:
            print(
                "usage: python3 eval/rerun_analysis.py "
                "[baseline_x treatment_x tasks]",
                file=sys.stderr,
            )
            sys.exit(2)
    elif len(argv) not in (0,):
        print(
            "usage: python3 eval/rerun_analysis.py "
            "[baseline_x treatment_x tasks]",
            file=sys.stderr,
        )
        sys.exit(2)

    if not (0 <= baseline_x <= tasks and 0 <= treat_x <= tasks and tasks > 0):
        print(
            f"error: need 0 <= baseline ({baseline_x}) <= tasks ({tasks}) "
            f"and 0 <= treatment ({treat_x}) <= tasks",
            file=sys.stderr,
        )
        sys.exit(2)

    print("=" * 60)
    print("tau3-bench rerun sizing analysis")
    print("=" * 60)
    print(
        f"scenario: baseline = {baseline_x}/{tasks}, "
        f"treatment = {treat_x}/{tasks}\n"
    )

    _print_single_run_wilson(baseline_x, treat_x, tasks)
    _print_min_r(baseline_x, treat_x, tasks)
    _print_sanity_table(baseline_x, treat_x, tasks)


if __name__ == "__main__":
    main()
