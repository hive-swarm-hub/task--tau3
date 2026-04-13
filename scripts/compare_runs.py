#!/usr/bin/env python3
"""Diff two tau3-bench eval run directories produced by eval/rerun_harness.sh.

    python3 scripts/compare_runs.py <baseline_dir> <candidate_dir> [--verbose|--json|--stage-b]
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "eval"))
from rerun_analysis import two_prop_z, two_prop_pvalue  # noqa: E402

_TASK_RE = re.compile(r"Task ID:\s*(task_\d+)")
_REWARD_RE = re.compile(r"Reward:\s*([\u2705\u274c])\s*([0-9.]+)")
_SUMMARY_RE = re.compile(r"Summary:\s*(\d+)/(\d+)\s*passed")


def _load_lite_clusters():
    """Extract LITE_TASK_CLUSTERS from eval/run_eval.py without triggering tau2 import."""
    try:
        src = open(os.path.join(_REPO_ROOT, "eval", "run_eval.py"), encoding="utf-8").read()
    except OSError:
        return None
    m = re.search(r"LITE_TASK_CLUSTERS[^=]*=\s*(\{.*?^\})", src, re.S | re.M)
    if not m:
        return None
    try:
        ns: dict = {}
        exec("LITE_TASK_CLUSTERS = " + m.group(1), ns)
        return ns["LITE_TASK_CLUSTERS"]
    except Exception:
        return None


LITE_TASK_CLUSTERS = _load_lite_clusters()


def parse_run_log(path):
    """Return {task_id: True/False/None}. None means 'incomplete' (task started, no reward)."""
    results, current = {}, None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _TASK_RE.search(line)
            if m:
                current = m.group(1)
                results.setdefault(current, None)
                continue
            if current is not None and "Partial" not in line:
                r = _REWARD_RE.search(line)
                if r:
                    results[current] = (r.group(1) == "\u2705")
                    current = None
    return results


def parse_summary(path):
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None, None
    matches = _SUMMARY_RE.findall(txt)
    if not matches:
        return None, None
    p, t = matches[-1]
    return int(p), int(t)


def load_rundir(d):
    logs = []
    for path in sorted(glob.glob(os.path.join(d, "run_*.log"))):
        tasks = parse_run_log(path)
        passed, total = parse_summary(path)
        if passed is None:
            passed = sum(1 for v in tasks.values() if v is True)
            total = len(tasks) or None
        logs.append({"path": path, "passed": passed, "total": total, "tasks": tasks})
    return {"dir": d, "logs": logs}


def _stats(counts):
    n = len(counts)
    if n == 0:
        return 0.0, 0.0
    mean = sum(counts) / n
    sd = math.sqrt(sum((c - mean) ** 2 for c in counts) / max(1, n - 1))
    return mean, sd


def _consensus(logs, tid):
    """Return (state, pass_count, run_count). state in {pass, fail, flaky, unknown}."""
    vals = [l["tasks"].get(tid) for l in logs if tid in l["tasks"]]
    n = len(vals)
    if n == 0:
        return "unknown", 0, 0
    p = sum(1 for v in vals if v is True)
    f = sum(1 for v in vals if v is False)
    u = sum(1 for v in vals if v is None)
    if u == n:
        return "unknown", p, n
    if p == n - u and p > 0:
        return "pass", p, n
    if f == n - u and f > 0:
        return "fail", p, n
    return "flaky", p, n


def stage_verdict(delta_tasks):
    if delta_tasks <= 0:
        return "reject candidate (no improvement)"
    if delta_tasks >= 4:
        return "strong signal, proceed to Stage B (R=15) for statistical confirmation"
    return "inconclusive, escalate to Stage B (R=15)"


def compare(baseline, candidate):
    b_counts = [l["passed"] for l in baseline["logs"] if l["passed"] is not None]
    c_counts = [l["passed"] for l in candidate["logs"] if l["passed"] is not None]
    b_tot = [l["total"] for l in baseline["logs"] if l["total"]]
    c_tot = [l["total"] for l in candidate["logs"] if l["total"]]
    all_tot = b_tot + c_tot
    total = all_tot[0] if all_tot else 97
    lite = bool(all_tot) and all(t == 20 for t in all_tot)
    mixed = len(set(all_tot)) > 1

    b_mean, b_sd = _stats(b_counts)
    c_mean, c_sd = _stats(c_counts)
    n1, n2 = sum(b_tot), sum(c_tot)
    x1, x2 = sum(b_counts), sum(c_counts)
    z = two_prop_z(x2, n2, x1, n1) if n1 and n2 else 0.0
    p = two_prop_pvalue(x2, n2, x1, n1, two_sided=True) if n1 and n2 else 1.0

    tids = sorted({t for l in baseline["logs"] + candidate["logs"] for t in l["tasks"]})
    per_task = []
    for tid in tids:
        bs, bp, bn = _consensus(baseline["logs"], tid)
        cs, cp, cn = _consensus(candidate["logs"], tid)
        flip = "recovered" if (bs == "fail" and cs == "pass") else \
               "regressed" if (bs == "pass" and cs == "fail") else None
        per_task.append({"task_id": tid, "baseline_state": bs, "candidate_state": cs,
                         "baseline_passes": bp, "baseline_runs": bn,
                         "candidate_passes": cp, "candidate_runs": cn, "flip": flip})

    return {
        "baseline": {"dir": baseline["dir"], "runs": b_counts, "mean": b_mean,
                     "stddev": b_sd, "n_runs": len(baseline["logs"]), "totals": b_tot},
        "candidate": {"dir": candidate["dir"], "runs": c_counts, "mean": c_mean,
                      "stddev": c_sd, "n_runs": len(candidate["logs"]), "totals": c_tot},
        "total_tasks": total, "lite": lite, "mixed": mixed,
        "delta_mean": c_mean - b_mean, "z": z, "p_two_sided": p,
        "n_baseline_trials": n1, "n_candidate_trials": n2, "per_task": per_task,
    }


def _print_default(r, stage_b=False, verbose=False):
    b, c = r["baseline"], r["candidate"]
    t = r["total_tasks"]
    print("=" * 60)
    print(f"  Baseline:   {b['dir']}/  ({b['n_runs']} runs)")
    print(f"  Candidate:  {c['dir']}/  ({c['n_runs']} runs)")
    print("=" * 60)
    if r["mixed"]:
        print("  WARNING: mixed task counts across runs — interpret with care")
    print("\nScore comparison\n" + "-" * 16)
    print(f"  baseline mean:  {b['mean']:.2f} / {t}  ({100*b['mean']/t:.2f}%)   stddev={b['stddev']:.2f}")
    print(f"  candidate mean: {c['mean']:.2f} / {t}  ({100*c['mean']/t:.2f}%)   stddev={c['stddev']:.2f}")
    sign = "+" if r["delta_mean"] >= 0 else ""
    print(f"  delta:         {sign}{r['delta_mean']:.2f} tasks  ({sign}{100*r['delta_mean']/t:.2f} pp)")

    print("\nStatistical test (pooled two-proportion z-test)")
    print(f"  N: baseline={r['n_baseline_trials']}, candidate={r['n_candidate_trials']}")
    print(f"  z = {r['z']:.2f},  p (two-sided) = {r['p_two_sided']:.3f}")
    verdict = "significant at α=0.05" if r["p_two_sided"] < 0.05 else "not distinguishable from noise at α=0.05"
    print(f"  VERDICT:  {verdict}")

    label = "Stage B" if stage_b else "Stage A"
    print(f"\n{label} interpretation (program.md)")
    print(f"  mean Δ = {r['delta_mean']:+.2f} → {stage_verdict(r['delta_mean'])}")

    recovered = [p["task_id"] for p in r["per_task"] if p["flip"] == "recovered"]
    regressed = [p["task_id"] for p in r["per_task"] if p["flip"] == "regressed"]
    print("\nPer-task flips\n" + "-" * 14)
    print(f"  ✗→✓ ({len(recovered)} tasks now passing):" + ("\n    " + "  ".join(recovered) if recovered else ""))
    print(f"  ✓→✗ ({len(regressed)} tasks now failing):" + ("\n    " + "  ".join(regressed) if regressed else ""))
    print(f"  Net: {len(recovered)-len(regressed):+d} task flips ({len(recovered)} recovered, {len(regressed)} regressed)")

    flaky_b = [(p["task_id"], p["baseline_passes"], p["baseline_runs"])
               for p in r["per_task"] if p["baseline_state"] == "flaky"]
    flaky_c = [(p["task_id"], p["candidate_passes"], p["candidate_runs"])
               for p in r["per_task"] if p["candidate_state"] == "flaky"]
    if flaky_b or flaky_c:
        print("\nFlaky tasks (inconsistent within a condition)")
        for tid, pp, nn in flaky_b:
            print(f"  Flaky in baseline:  {tid} (passed {pp}/{nn})")
        for tid, pp, nn in flaky_c:
            print(f"  Flaky in candidate: {tid} (passed {pp}/{nn})")

    unknown = [p["task_id"] for p in r["per_task"]
               if p["baseline_state"] == "unknown" or p["candidate_state"] == "unknown"]
    if unknown:
        more = " ..." if len(unknown) > 8 else ""
        print(f"\nUnknown (log incomplete for {len(unknown)} tasks): {' '.join(unknown[:8])}{more}")

    if r["lite"] and LITE_TASK_CLUSTERS:
        print("\nPer-cluster (lite eval)\n" + "-" * 22)
        by_tid = {p["task_id"]: p for p in r["per_task"]}
        for label, tids in LITE_TASK_CLUSTERS.items():
            bp = sum(1 for t in tids if by_tid.get(t, {}).get("baseline_state") == "pass")
            cp = sum(1 for t in tids if by_tid.get(t, {}).get("candidate_state") == "pass")
            tag = "stable" if bp == cp else (f"+{cp-bp}" if cp > bp else "REGRESSION — investigate")
            print(f"  {label:22s} {bp}/{len(tids)} → {cp}/{len(tids)}    ({tag})")

    if verbose:
        print("\nPer-run scores")
        for i, s in enumerate(b["runs"], 1):
            print(f"  baseline  run_{i}: {s}")
        for i, s in enumerate(c["runs"], 1):
            print(f"  candidate run_{i}: {s}")
        print("\nLog files")
        for d in b["dir"], c["dir"]:
            for p in sorted(glob.glob(os.path.join(d, "run_*.log"))):
                print(f"  {p}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("baseline_dir")
    ap.add_argument("candidate_dir")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stage-b", action="store_true")
    args = ap.parse_args()

    for d in (args.baseline_dir, args.candidate_dir):
        if not os.path.isdir(d):
            print(f"error: not a directory: {d}", file=sys.stderr)
            sys.exit(2)

    baseline = load_rundir(args.baseline_dir)
    candidate = load_rundir(args.candidate_dir)
    if not baseline["logs"] or not candidate["logs"]:
        print("error: no run_*.log files found in one or both directories", file=sys.stderr)
        sys.exit(2)
    if len(baseline["logs"]) < 4 or len(candidate["logs"]) < 4:
        print(f"note: expected 4 runs per variant; got baseline={len(baseline['logs'])}, "
              f"candidate={len(candidate['logs'])}", file=sys.stderr)

    result = compare(baseline, candidate)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_default(result, stage_b=args.stage_b, verbose=args.verbose)


if __name__ == "__main__":
    main()
