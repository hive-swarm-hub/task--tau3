#!/usr/bin/env python3
"""Rerun sizing for τ³-bench improvement claims. Stdlib only.

  python3 eval/rerun_analysis.py

Prints Wilson CIs, pooled-variance z-test, and the minimum R (reruns per
variant) to reach α=0.05 for the canonical 7/97 → 9/97 scenario.
"""

from __future__ import annotations

import math


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def wilson_interval(x: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for a single proportion."""
    phat = x / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(phat * (1.0 - phat) / n + z2 / (4.0 * n * n))
    return max(0.0, center - half), min(1.0, center + half)


def two_prop_z(x1: int, n1: int, x0: int, n0: int) -> float:
    """Pooled-variance two-proportion z-statistic (treatment minus baseline)."""
    p1 = x1 / n1
    p0 = x0 / n0
    p_pool = (x1 + x0) / (n1 + n0)
    var = p_pool * (1.0 - p_pool) * (1.0 / n1 + 1.0 / n0)
    if var <= 0.0:
        return 0.0 if p1 == p0 else (math.inf if p1 > p0 else -math.inf)
    return (p1 - p0) / math.sqrt(var)


def two_prop_pvalue(x1: int, n1: int, x0: int, n0: int, two_sided: bool = True) -> float:
    """p-value for H0: p1 == p0 (or p1 <= p0 if one-sided)."""
    z = two_prop_z(x1, n1, x0, n0)
    if math.isinf(z):
        return 0.0 if z > 0 else 1.0
    return 2.0 * (1.0 - _phi(abs(z))) if two_sided else 1.0 - _phi(z)


def main() -> None:
    baseline_x, treat_x, tasks = 7, 9, 97
    delta = (treat_x - baseline_x) / tasks
    p_pool = (baseline_x + treat_x) / (2.0 * tasks)

    print(f"scenario: {baseline_x}/{tasks} → {treat_x}/{tasks}  (Δ = +{treat_x - baseline_x} tasks)\n")

    lo_b, hi_b = wilson_interval(baseline_x, tasks)
    lo_t, hi_t = wilson_interval(treat_x, tasks)
    print("single-run Wilson 95% CI")
    print(f"  baseline  {baseline_x}/{tasks} = {100 * baseline_x / tasks:5.2f}%   [{100 * lo_b:5.2f}%, {100 * hi_b:5.2f}%]")
    print(f"  treatment {treat_x}/{tasks} = {100 * treat_x / tasks:5.2f}%   [{100 * lo_t:5.2f}%, {100 * hi_t:5.2f}%]\n")

    # Min R from: z = Δ / sqrt(p_pool*(1-p_pool) * 2/(R*tasks)) >= zcrit
    def min_r(zcrit: float) -> int:
        return max(1, math.ceil(2.0 * p_pool * (1.0 - p_pool) * (zcrit / delta) ** 2 / tasks))

    print("minimum reruns per variant (pooled-variance z-test)")
    print(f"  two-sided α=0.05 (z=1.96)   R >= {min_r(1.96)}")
    print(f"  one-sided α=0.05 (z=1.645)  R >= {min_r(1.645)}\n")

    print("sanity-check table (scaled counts)")
    print(f"  {'R':>4}  {'x1':>5}  {'x0':>5}  {'z':>7}  {'p(2s)':>8}")
    for r in (4, 8, 10, 15, 20):
        n = r * tasks
        x0 = baseline_x * r
        x1 = treat_x * r
        z = two_prop_z(x1, n, x0, n)
        p2 = two_prop_pvalue(x1, n, x0, n, two_sided=True)
        print(f"  {r:>4}  {x1:>5}  {x0:>5}  {z:>7.3f}  {p2:>8.4f}")


if __name__ == "__main__":
    main()
