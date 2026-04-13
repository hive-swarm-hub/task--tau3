#!/usr/bin/env python3
"""Dump everything that happened on a single tau3-bench task.

Usage:
    python3 scripts/show_task.py <task_id>
    python3 scripts/show_task.py <task_id> --json
    python3 scripts/show_task.py <task_id> --only messages|tools|diagnostic
    python3 scripts/show_task.py --list
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

BAR = "=" * 71
DASH = "-" * 71


# ---------------------------------------------------------------- discovery --
def _default_results_path() -> str:
    """Mirror eval/eval.sh: walk up from the tau2 package to find data/.

    We avoid `import tau2` (which triggers side-effect logging) and instead
    look up its file location the cheap way via importlib.util.
    """
    try:
        import importlib.util
        spec = importlib.util.find_spec("tau2")
        origin = getattr(spec, "origin", None) if spec else None
        if origin:
            d = os.path.dirname(origin)
            for _ in range(6):
                cand = os.path.join(d, "data", "simulations",
                                    "eval_banking_knowledge", "results.json")
                if os.path.isfile(cand):
                    return cand
                d = os.path.dirname(d)
    except Exception:
        pass
    # Fallback: local checkout.
    here = Path(__file__).resolve().parents[1]
    local = here / "tau2-bench" / "data" / "simulations" \
        / "eval_banking_knowledge" / "results.json"
    return str(local)


def _default_traces_path() -> str:
    here = Path(__file__).resolve().parents[1]
    return str(here / "traces" / "latest.json")


def _load_json(path: str) -> dict | list | None:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        print(f"(could not parse {path}: {exc})", file=sys.stderr)
        return None


def _find_sim(results: dict, task_id: str) -> dict | None:
    for sim in (results or {}).get("simulations", []) or []:
        if sim.get("task_id") == task_id:
            return sim
    return None


def _find_task(results: dict, task_id: str) -> dict | None:
    for t in (results or {}).get("tasks", []) or []:
        if t.get("id") == task_id:
            return t
    return None


def _find_trace(traces: dict, task_id: str) -> dict | None:
    for t in (traces or {}).get("failure_traces", []) or []:
        if t.get("task_id") == task_id:
            return t
    return None


# ----------------------------------------------------------------- helpers --
def _trunc(s, n=140):
    s = "" if s is None else str(s).replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "..."


def _fmt_args(args: dict | None) -> str:
    if not args:
        return ""
    bits = [f"{k}={json.dumps(v, default=str)}" for k, v in args.items()]
    return ", ".join(bits)


def _summarize_tool_result(content: str, max_docs: int = 2) -> str:
    s = (content or "").strip()
    if not s:
        return "(empty)"
    # Heuristic KB-style "1. ...  2. ...  3. ..." truncation.
    lines = s.splitlines()
    doc_starts = [i for i, ln in enumerate(lines)
                  if ln[:3].strip().rstrip(".").isdigit() and ln.strip().endswith(":")
                  or ln.lstrip().startswith(("1. ", "2. ", "3. ", "4. ", "5. "))]
    if len(doc_starts) >= max_docs + 1:
        keep = "\n".join(lines[: doc_starts[max_docs]]).strip()
        more = len(doc_starts) - max_docs
        return keep + f"\n   ... {more} more docs"
    return _trunc(s, 400)


# ----------------------------------------------------------------- headers --
def _reward_line(sim: dict) -> str:
    ri = sim.get("reward_info") or {}
    r = ri.get("reward", None)
    mark = "PASS" if r == 1.0 else "FAIL"
    breakdown = ri.get("reward_breakdown") or {}
    bd = ", ".join(f"{k}: {v}" for k, v in breakdown.items()) or "-"
    return f"Reward: {mark} {float(r or 0):.4f}  ({bd})"


def _header(sim: dict, trace: dict | None) -> list[str]:
    dur = sim.get("duration") or 0
    turns = len(sim.get("messages") or [])
    term = sim.get("termination_reason") or "?"
    pfc = (trace or {}).get("primary_failure_class") or "(no trace)"
    return [
        BAR,
        f"  Task: {sim.get('task_id')}",
        f"  {_reward_line(sim)}",
        f"  Primary failure class: {pfc}",
        f"  Duration: {dur:.1f}s | Turns: {turns} | Termination: {term}",
        BAR,
    ]


# ---------------------------------------------------------------- messages --
def _render_messages(sim: dict) -> list[str]:
    out: list[str] = []
    msgs = sim.get("messages") or []
    for i, m in enumerate(msgs):
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        tcs = m.get("tool_calls") or []
        if role == "user":
            out.append(f"\nCUSTOMER (turn {i})")
            if content:
                out.append("  " + _trunc(content, 400))
        elif role == "assistant":
            out.append(f"\nAGENT TURN {i} (assistant)")
            if content:
                for line in textwrap.wrap(content, 86):
                    out.append("  " + line)
            for tc in tcs:
                out.append(f"  -> {tc.get('name')}({_trunc(_fmt_args(tc.get('arguments')), 200)})")
        elif role == "tool":
            body = _summarize_tool_result(content)
            for line in body.splitlines():
                out.append("  <- " + _trunc(line, 120))
        else:
            out.append(f"\n{role.upper()} (turn {i})")
            if content:
                out.append("  " + _trunc(content, 400))
    return out


def _render_tools_only(sim: dict) -> list[str]:
    out: list[str] = []
    msgs = sim.get("messages") or []
    pending: list[dict] = []
    for m in msgs:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                pending.append(tc)
                out.append(f"-> {tc.get('name')}({_fmt_args(tc.get('arguments'))})")
        elif m.get("role") == "tool":
            out.append("   <- " + _summarize_tool_result(m.get("content"), max_docs=2).splitlines()[0])
    return out


# -------------------------------------------------------------- action check -
def _render_action_checks(sim: dict, trace: dict | None) -> list[str]:
    out = ["\nACTION CHECKS (oracle expected vs actual)"]
    ri = sim.get("reward_info") or {}
    checks = ri.get("action_checks") or []
    if not checks and trace:
        # Fall back to the trace's action_details (has matched flag).
        for d in trace.get("action_details") or []:
            flag = "OK" if d.get("matched") else "FAIL"
            out.append(f"  [{flag}] {d.get('expected_tool')}({_fmt_args(d.get('expected_args'))})")
        return out
    if not checks:
        out.append("  (no action checks recorded)")
        return out
    for c in checks:
        a = c.get("action") or {}
        flag = "OK" if c.get("action_match") else "FAIL"
        out.append(f"  [{flag}] {a.get('name')}({_fmt_args(a.get('arguments'))})  "
                   f"[{c.get('tool_type', '?')}]  reward={c.get('action_reward')}")
    return out


# -------------------------------------------------------------- diagnostic --
ANALYZER_KEYS = (
    "discoverable_tool_analysis",
    "argument_analysis",
    "verification_analysis",
    "retrieval_analysis",
    "execution_analysis",
)


def _render_diagnostic(trace: dict | None) -> list[str]:
    out = ["\nDIAGNOSTIC ANALYZERS"]
    if not trace:
        out.append("  (no diagnostic data - run eval + extract_traces first)")
        return out
    for k in ANALYZER_KEYS:
        v = trace.get(k)
        if v is None:
            continue
        out.append(f"  {k}:")
        if isinstance(v, dict):
            for kk, vv in v.items():
                if isinstance(vv, (dict, list)):
                    s = json.dumps(vv, default=str)
                    out.append(f"    {kk}: {_trunc(s, 200)}")
                else:
                    out.append(f"    {kk}: {vv}")
        else:
            out.append(f"    {v!r}")
    out.append(f"  primary_failure_class: {trace.get('primary_failure_class')}")
    return out


def _render_interventions(sim: dict) -> list[str]:
    # Best-effort: interventions aren't currently persisted in results.json.
    # Look for any ledger-ish fields.
    for key in ("tool_call_ledger", "gate_interventions", "interventions",
                "intervention_log"):
        if sim.get(key):
            out = ["\nINTERVENTIONS FIRED"]
            out.append("  " + _trunc(json.dumps(sim[key], default=str), 500))
            return out
    return []


# -------------------------------------------------------------------- main --
def _list_tasks(results: dict, traces: dict | None) -> int:
    sims = (results or {}).get("simulations") or []
    if not sims:
        print("(no simulations in results.json)")
        return 1
    pfc_by_id = {}
    if traces:
        for t in traces.get("failure_traces") or []:
            pfc_by_id[t.get("task_id")] = t.get("primary_failure_class")
    for sim in sims:
        tid = sim.get("task_id")
        ri = sim.get("reward_info") or {}
        r = ri.get("reward")
        flag = "PASS" if r == 1.0 else "FAIL"
        bd = ri.get("reward_breakdown") or {}
        parts = ", ".join(f"{k}: {v}" for k, v in bd.items())
        extra = f"  primary={pfc_by_id[tid]}" if pfc_by_id.get(tid) else ""
        print(f"{tid}  {flag}  reward={float(r or 0):.2f}  ({parts}){extra}")
    return 0


def _show(task_id: str, results: dict, traces: dict | None, only: str | None) -> int:
    sim = _find_sim(results, task_id)
    if not sim:
        avail = ", ".join(s.get("task_id", "?") for s in (results or {}).get("simulations", []) or [])
        print(f"Task {task_id!r} not found in results.json.\nAvailable: {avail}",
              file=sys.stderr)
        return 2
    trace = _find_trace(traces, task_id) if traces else None
    blocks: list[str] = []
    if only is None:
        blocks += _header(sim, trace)
        blocks += _render_messages(sim)
        blocks += _render_action_checks(sim, trace)
        blocks += _render_diagnostic(trace)
        blocks += _render_interventions(sim)
    elif only == "messages":
        blocks += _render_messages(sim)
    elif only == "tools":
        blocks += _render_tools_only(sim)
    elif only == "diagnostic":
        blocks += _render_diagnostic(trace)
    print("\n".join(blocks))
    return 0


def _show_json(task_id: str, results: dict, traces: dict | None) -> int:
    sim = _find_sim(results, task_id)
    if not sim:
        print(json.dumps({"error": f"task {task_id} not found"}), file=sys.stderr)
        return 2
    trace = _find_trace(traces, task_id) if traces else None
    task = _find_task(results, task_id)
    out = {
        "task_id": task_id,
        "reward_info": sim.get("reward_info"),
        "duration": sim.get("duration"),
        "termination_reason": sim.get("termination_reason"),
        "messages": sim.get("messages"),
        "evaluation_criteria": (task or {}).get("evaluation_criteria"),
        "trace": trace,
    }
    json.dump(out, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("task_id", nargs="?", help="e.g. task_058")
    p.add_argument("--list", action="store_true",
                   help="dump available task ids from results.json")
    p.add_argument("--results", default=None, help="path to results.json")
    p.add_argument("--traces", default=None, help="path to traces/latest.json")
    p.add_argument("--json", action="store_true", help="machine-readable JSON")
    p.add_argument("--only", choices=("messages", "tools", "diagnostic"),
                   help="restrict output to one section")
    args = p.parse_args()

    results_path = args.results or _default_results_path()
    traces_path = args.traces or _default_traces_path()

    results = _load_json(results_path)
    if results is None:
        print(f"No results.json at {results_path}.\n"
              f"Run eval first (e.g. `bash eval/eval.sh`), or pass --results.",
              file=sys.stderr)
        return 1
    traces = _load_json(traces_path)
    if traces is None and not args.list:
        print(f"(no diagnostic data at {traces_path} - continuing without)",
              file=sys.stderr)

    if args.list:
        return _list_tasks(results, traces)
    if not args.task_id:
        p.error("task_id required (or pass --list)")
    if args.json:
        return _show_json(args.task_id, results, traces)
    return _show(args.task_id, results, traces, args.only)


if __name__ == "__main__":
    sys.exit(main())
