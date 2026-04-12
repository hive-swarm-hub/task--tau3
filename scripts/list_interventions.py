#!/usr/bin/env python3
"""Dump the tau3-bench Interventions Registry in a human-readable format."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass

# Ensure the repo root is on sys.path so `import interventions` resolves.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

HOOKS = ("prompt", "annotator", "gate_pre", "gate_post", "state_track", "tool_result")
STATUSES = ("active", "disabled", "experimental")


def _load_registry():
    try:
        from interventions import REGISTRY  # type: ignore
    except Exception:
        print(
            "REGISTRY not yet available at interventions.REGISTRY — "
            "has the framework been built?",
            file=sys.stderr,
        )
        sys.exit(1)
    # Auto-import all interventions_*.py sibling modules so their
    # registration side effects populate REGISTRY before we dump it.
    # Without this the CLI would show an empty registry unless the caller
    # pre-imported agent.py (which imports the banking interventions).
    import glob
    import importlib
    for path in sorted(glob.glob(os.path.join(_REPO_ROOT, "interventions_*.py"))):
        module_name = os.path.splitext(os.path.basename(path))[0]
        try:
            importlib.import_module(module_name)
        except Exception as e:
            print(f"[warn] failed to import {module_name}: {e}", file=sys.stderr)
    return REGISTRY


def _to_dict(item) -> dict:
    if is_dataclass(item):
        return asdict(item)
    if hasattr(item, "__dict__"):
        return {k: v for k, v in vars(item).items() if not k.startswith("_")}
    return {
        k: getattr(item, k, None)
        for k in ("id", "name", "hook", "target_cluster", "author",
                  "description", "status", "measured_impact")
    }


def _impact_short(impact) -> str:
    if not impact:
        return "-"
    if not isinstance(impact, dict):
        return str(impact)
    lite = impact.get("lite_delta_tasks")
    full = impact.get("full_delta_tasks")
    if lite not in (None, 0, "0"):
        s = str(lite)
        s = f"+{s}" if not s.startswith(("+", "-")) else s
        return f"{s} lite"
    if full not in (None, 0, "0"):
        s = str(full)
        s = f"+{s}" if not s.startswith(("+", "-")) else s
        return f"{s} full"
    return "-"


def _impact_long(impact) -> str:
    if not impact:
        return "(none measured)"
    if not isinstance(impact, dict):
        return str(impact)
    parts = []
    if impact.get("lite_delta_tasks") is not None:
        parts.append(f"lite delta={impact['lite_delta_tasks']}")
    if impact.get("full_delta_tasks") is not None:
        parts.append(f"full delta={impact['full_delta_tasks']}")
    if impact.get("verified_sha"):
        parts.append(f"verified at sha {str(impact['verified_sha'])[:7]}")
    return ", ".join(parts) if parts else str(impact)


def _truncate(s: str, n: int) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: max(1, n - 1)] + "…"


def _filter(items, hook=None, cluster=None, status=None):
    out = []
    for it in items:
        d = _to_dict(it)
        if hook and d.get("hook") != hook:
            continue
        if cluster and d.get("target_cluster") != cluster:
            continue
        if status and d.get("status") != status:
            continue
        out.append(it)
    return out


def _print_table(items) -> None:
    headers = ["ID", "NAME", "HOOK", "CLUSTER", "STATUS", "IMPACT", "AUTHOR"]
    rows = []
    for it in items:
        d = _to_dict(it)
        rows.append([
            str(d.get("id", "") or ""),
            str(d.get("name", "") or ""),
            str(d.get("hook", "") or ""),
            str(d.get("target_cluster", "") or ""),
            str(d.get("status", "") or ""),
            _impact_short(d.get("measured_impact")),
            str(d.get("author", "") or ""),
        ])
    caps = [6, 26, 12, 14, 13, 14, 14]
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], min(len(cell), caps[i]))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line.rstrip())
    for r in rows:
        print("  ".join(_truncate(r[i], widths[i]).ljust(widths[i])
                        for i in range(len(headers))).rstrip())


def _print_verbose(items) -> None:
    for it in items:
        d = _to_dict(it)
        iid = d.get("id", "?")
        name = d.get("name", "")
        header = f"┌─ {iid}: {name} "
        pad = max(0, 63 - len(header))
        print(header + "─" * pad)
        for label, key in (("Hook", "hook"), ("Target", "target_cluster"),
                           ("Status", "status"), ("Author", "author"),
                           ("Description", "description")):
            print(f"│ {label + ':':<13}{d.get(key, '') or ''}")
        print(f"│ {'Impact:':<13}{_impact_long(d.get('measured_impact'))}")
        print("└" + "─" * 62)


def main() -> int:
    p = argparse.ArgumentParser(description="Dump the Interventions Registry.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--table", action="store_true", help="tabular view (default)")
    mode.add_argument("-v", "--verbose", action="store_true", help="expanded per-item blocks")
    mode.add_argument("--json", action="store_true", help="machine-readable JSON")
    mode.add_argument("--count", action="store_true", help="print count of active interventions")
    p.add_argument("--filter-hook", choices=HOOKS, metavar="HOOK")
    p.add_argument("--filter-cluster", metavar="CLUSTER")
    p.add_argument("--filter-status", choices=STATUSES, metavar="STATUS")
    args = p.parse_args()

    registry = _load_registry()
    try:
        items = list(registry.list(include_disabled=True))
    except TypeError:
        items = list(registry.list())
    except Exception as exc:
        print(f"Failed to enumerate registry: {exc}", file=sys.stderr)
        return 1

    if not items:
        print("(registry is empty)", file=sys.stderr)

    items = _filter(items, args.filter_hook, args.filter_cluster, args.filter_status)

    if args.count:
        print(sum(1 for it in items if _to_dict(it).get("status") == "active"))
    elif args.json:
        json.dump([_to_dict(it) for it in items], sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    elif args.verbose:
        _print_verbose(items)
    else:
        _print_table(items)
    return 0


if __name__ == "__main__":
    sys.exit(main())
