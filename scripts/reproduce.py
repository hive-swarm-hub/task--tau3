#!/usr/bin/env python3
"""Print the exact shell command that produced a past τ³-bench eval run.

Every eval run writes `eval_runs/last_config.json` (see `eval/run_eval.py`).
This tool reads that snapshot — or any other `config_snapshot.json` saved
alongside a results.json — and reconstructs the EXACT command an agent
would run to replay it: git SHA to checkout, env vars, script invocation.

Usage:
  python3 scripts/reproduce.py                          # read the most recent snapshot, print command
  python3 scripts/reproduce.py --snapshot PATH          # read a specific snapshot
  python3 scripts/reproduce.py --run                    # actually execute the reproduction command

Design: print-only by default (never shell out without the user opting in).
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SNAPSHOT = _REPO_ROOT / "eval_runs" / "last_config.json"


# Env vars we reconstruct on the command line — in the order the run_eval.py
# snapshot collects them. We only emit vars the user explicitly set (i.e. not
# the defaults), since unnecessary env=VAL noise muddies the reproduction
# command. Pairs map the snapshot-key to (emit_if_not_equal_to_default).
_ENV_DEFAULTS = {
    "RETRIEVAL_VARIANT": "bm25",
    "SOLVER_MODEL": "gpt-4.1-mini",
    "USER_MODEL": "gpt-4.1-2025-04-14",
    "EVAL_CONCURRENCY": "8",
    "EVAL_LITE": "0",
    "SAMPLE_FRAC": "1.0",
    "DISABLED_INTERVENTIONS": "",
    "ENABLE_EXPERIMENTAL": "0",
}

# Emit-order tuned so the "punchy" vars (DISABLED_INTERVENTIONS,
# RETRIEVAL_VARIANT, EVAL_LITE) appear first in the command line.
_ENV_EMIT_ORDER = [
    "DISABLED_INTERVENTIONS",
    "ENABLE_EXPERIMENTAL",
    "RETRIEVAL_VARIANT",
    "SOLVER_MODEL",
    "USER_MODEL",
    "EVAL_LITE",
    "SAMPLE_FRAC",
    "EVAL_CONCURRENCY",
]


def _load_snapshot(path: Path) -> dict:
    """Read and parse a snapshot JSON file. Exits 1 on IO / parse errors."""
    if not path.exists():
        print(
            f"error: snapshot file not found: {path}\n"
            f"hint: run `bash eval/eval.sh` first to generate one, "
            f"or pass --snapshot PATH to pick a different file.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"error: could not parse snapshot {path}: {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"error: could not read snapshot {path}: {e}", file=sys.stderr)
        sys.exit(1)


def _build_env_lines(env: dict) -> list[str]:
    """Emit `KEY=VALUE \\` lines for every env var that differs from the default.

    Empty string (the default for DISABLED_INTERVENTIONS) suppresses emission.
    Uses shlex.quote so values with spaces / special chars reproduce safely.
    """
    lines: list[str] = []
    for key in _ENV_EMIT_ORDER:
        value = str(env.get(key, ""))
        default = _ENV_DEFAULTS.get(key, "")
        if not value or value == default:
            continue
        lines.append(f"{key}={shlex.quote(value)}")
    return lines


def _build_command(snapshot: dict) -> tuple[list[str], str]:
    """Build the reproduce command as (lines_for_pretty_printing, oneline_for_subprocess).

    Multi-line form (pretty):
        git checkout SHA && \\
        FOO=bar \\
        BAZ=qux \\
        bash eval/eval.sh > run.log 2>&1

    One-line form (used by --run): `bash -c 'git checkout ...; FOO=bar ... bash eval/eval.sh'`
    """
    raw_sha = snapshot.get("git_sha", "unknown")
    # Strip the -dirty suffix — you can't `git checkout` a dirty marker. The
    # warning in the output tells the user exact reproduction isn't possible.
    checkout_sha = raw_sha.replace("-dirty", "")

    env_lines = _build_env_lines(snapshot.get("env", {}) or {})

    pretty_lines: list[str] = []
    if checkout_sha and checkout_sha != "unknown":
        pretty_lines.append(f"git checkout {checkout_sha} && \\")
    for line in env_lines:
        pretty_lines.append(f"{line} \\")
    pretty_lines.append("bash eval/eval.sh > run.log 2>&1")

    # Flatten for --run (drop the `\` continuations, insert actual spaces)
    oneline_parts: list[str] = []
    if checkout_sha and checkout_sha != "unknown":
        oneline_parts.append(f"git checkout {checkout_sha}")
    prefix = " ".join(env_lines)
    invoke = "bash eval/eval.sh > run.log 2>&1"
    oneline_parts.append(f"{prefix} {invoke}" if prefix else invoke)
    oneline = " && ".join(oneline_parts)

    return pretty_lines, oneline


def _print_reproduce_report(snapshot_path: Path, snapshot: dict) -> None:
    """Pretty-print the snapshot summary + command + caveats to stdout."""
    raw_sha = snapshot.get("git_sha", "unknown")
    dirty = raw_sha.endswith("-dirty")
    sha_short = raw_sha[:7] if raw_sha != "unknown" else "unknown"
    branch = snapshot.get("git_branch", "unknown")
    timestamp = snapshot.get("timestamp", "unknown")
    state = "DIRTY" if dirty else "CLEAN"

    interventions = snapshot.get("interventions", []) or []
    n_active = sum(1 for i in interventions if i.get("status") == "active")
    n_experimental = sum(1 for i in interventions if i.get("status") == "experimental")

    print(f"Reproducing eval run from {snapshot_path}:\n")
    print(f"  timestamp:     {timestamp}")
    print(f"  git:           {sha_short} (branch: {branch}) [{state}]")
    print(f"  interventions: {n_active} active + {n_experimental} experimental")
    print()
    print("Command:")
    print()
    pretty_lines, _ = _build_command(snapshot)
    for line in pretty_lines:
        print(f"    {line}")
    print()
    print("Caveats:")
    print("  - OpenAI temp=0 is not perfectly deterministic; expect +/-2 task variance")
    print(f"  - tau2_version: {snapshot.get('tau2_version', 'unknown')} (ensure installed)")
    print("  - If the snapshot's SHA doesn't match HEAD, you'll need to checkout")
    if dirty:
        print()
        print("  WARNING The original run was produced with uncommitted changes (SHA shows -dirty).")
        print("     Exact reproduction is not possible without that working tree.")


def _execute(snapshot: dict) -> int:
    """Actually run the reproduction command. Returns the subprocess exit code."""
    _, oneline = _build_command(snapshot)
    print(f"[reproduce] executing: {oneline}", file=sys.stderr)
    return subprocess.call(["bash", "-c", oneline], cwd=str(_REPO_ROOT))


def main() -> int:
    p = argparse.ArgumentParser(
        description="Print (or run) the exact command that produced a past τ³-bench eval run.",
    )
    p.add_argument(
        "--snapshot",
        type=Path,
        default=_DEFAULT_SNAPSHOT,
        help=f"Path to a snapshot JSON (default: {_DEFAULT_SNAPSHOT})",
    )
    p.add_argument(
        "--run",
        action="store_true",
        help="Actually execute the reproduction command (default: print only)",
    )
    args = p.parse_args()

    snapshot = _load_snapshot(args.snapshot)

    if args.run:
        # Still print the report so the user sees what they're about to run.
        _print_reproduce_report(args.snapshot, snapshot)
        print("\n--- executing --run ---\n", file=sys.stderr)
        return _execute(snapshot)
    else:
        _print_reproduce_report(args.snapshot, snapshot)
        return 0


if __name__ == "__main__":
    sys.exit(main())
