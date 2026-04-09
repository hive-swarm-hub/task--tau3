"""Extract failure traces from τ²-bench simulation results for meta-agent diagnosis.

Reads results.json from eval runs, extracts failed tasks with:
- Conversation transcript (agent + user messages)
- Tool calls made (name + arguments)
- Expected vs actual actions (what was wrong)
- DB state check results
- Termination reason
- Reward breakdown

Outputs a compact JSON summary to traces/latest.json that the meta-agent
can read to diagnose failure modes and plan improvements.

Usage:
  python eval/extract_traces.py                          # all domains
  python eval/extract_traces.py --domain banking_knowledge  # single domain
  python eval/extract_traces.py --top 10                 # only worst 10 failures
"""

import argparse
import json
import sys
from pathlib import Path

# τ²-bench saves results here by default
DATA_DIR = Path(__file__).parent.parent / "tau2-bench" / "data" / "simulations"
TRACES_DIR = Path(__file__).parent.parent / "traces"

DOMAINS = ["airline", "retail", "telecom", "banking_knowledge"]


def load_results(domain: str) -> dict | None:
    """Load results.json for a domain eval run."""
    results_path = DATA_DIR / f"eval_{domain}" / "results.json"
    if not results_path.exists():
        return None
    with open(results_path) as f:
        return json.load(f)


def extract_conversation(messages: list[dict]) -> list[dict]:
    """Extract a compact conversation transcript from simulation messages."""
    transcript = []
    for msg in messages:
        role = msg.get("role", "unknown")
        entry = {"role": role}

        content = msg.get("content", "")
        if content:
            # Truncate very long content (e.g., KB search results)
            if len(content) > 1000:
                entry["content"] = content[:800] + f"\n... [truncated, {len(content)} chars total]"
            else:
                entry["content"] = content

        tool_calls = msg.get("tool_calls")
        if tool_calls:
            entry["tool_calls"] = []
            for tc in tool_calls:
                tc_entry = {"name": tc.get("name", ""), "arguments": tc.get("arguments", {})}
                entry["tool_calls"].append(tc_entry)

        if role != "system":  # skip system messages (too long, already known)
            transcript.append(entry)

    return transcript


def extract_action_checks(reward_info: dict) -> list[dict]:
    """Extract action correctness details from reward_info."""
    checks = []
    for check in reward_info.get("action_checks", []):
        checks.append({
            "expected_tool": check.get("expected_action", {}).get("name", ""),
            "expected_args": check.get("expected_action", {}).get("arguments", {}),
            "matched": check.get("action_match", False),
            "tool_type": check.get("tool_type", ""),
        })
    return checks


def extract_task_trace(sim: dict, task_map: dict) -> dict:
    """Extract a single task's failure trace."""
    task_id = sim.get("task_id", "unknown")
    reward_info = sim.get("reward_info", {})
    reward = reward_info.get("reward", 0.0)

    trace = {
        "task_id": task_id,
        "reward": reward,
        "passed": reward >= 0.99,
        "termination_reason": sim.get("termination_reason", "unknown"),
        "num_turns": len(sim.get("messages", [])),
        "duration_s": sim.get("duration", 0),
        "agent_cost": sim.get("agent_cost", 0),
    }

    # Reward breakdown
    db_check = reward_info.get("db_check", {})
    if db_check:
        trace["db_match"] = db_check.get("match", None)

    action_checks = extract_action_checks(reward_info)
    if action_checks:
        trace["actions_expected"] = len(action_checks)
        trace["actions_matched"] = sum(1 for a in action_checks if a["matched"])
        trace["action_details"] = action_checks

    # Communication checks
    nl_assertions = reward_info.get("nl_assertions", [])
    if nl_assertions:
        trace["communicate_checks"] = nl_assertions

    # Conversation transcript (compact)
    messages = sim.get("messages", [])
    if messages:
        trace["conversation"] = extract_conversation(messages)

    # Review/error analysis if available
    review = sim.get("review", {})
    if review and review.get("errors"):
        trace["review_errors"] = review["errors"]

    return trace


def run(domain_filter: str | None = None, top_n: int | None = None, include_passed: bool = False):
    """Extract traces and write to traces/latest.json."""
    domains = [domain_filter] if domain_filter else DOMAINS
    all_traces = []
    summary = {"domains": {}, "total_tasks": 0, "total_passed": 0, "total_failed": 0}

    for domain in domains:
        results = load_results(domain)
        if results is None:
            print(f"  [skip] No results for {domain}", file=sys.stderr)
            continue

        tasks = results.get("tasks", [])
        task_map = {t["id"]: t for t in tasks}
        sims = results.get("simulations", [])

        domain_passed = 0
        domain_failed = 0

        for sim in sims:
            trace = extract_task_trace(sim, task_map)

            if trace["passed"]:
                domain_passed += 1
                if not include_passed:
                    continue
            else:
                domain_failed += 1

            trace["domain"] = domain
            all_traces.append(trace)

        summary["domains"][domain] = {
            "passed": domain_passed,
            "failed": domain_failed,
            "total": domain_passed + domain_failed,
            "pass_rate": domain_passed / max(1, domain_passed + domain_failed),
        }
        summary["total_tasks"] += domain_passed + domain_failed
        summary["total_passed"] += domain_passed
        summary["total_failed"] += domain_failed

        print(
            f"  {domain}: {domain_passed}/{domain_passed + domain_failed} passed, "
            f"{domain_failed} failures extracted",
            file=sys.stderr,
        )

    # Sort by reward (worst first) so meta-agent sees hardest failures first
    all_traces.sort(key=lambda t: (t["reward"], t["task_id"]))

    if top_n:
        all_traces = all_traces[:top_n]

    output = {
        "summary": summary,
        "failure_traces": all_traces,
    }

    TRACES_DIR.mkdir(exist_ok=True)
    out_path = TRACES_DIR / "latest.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n  Wrote {len(all_traces)} traces to {out_path}", file=sys.stderr)
    print(f"  Summary: {summary['total_passed']}/{summary['total_tasks']} passed", file=sys.stderr)

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract failure traces for meta-agent diagnosis")
    parser.add_argument("--domain", type=str, default=None, help="Single domain to extract")
    parser.add_argument("--top", type=int, default=None, help="Only keep N worst failures")
    parser.add_argument("--include-passed", action="store_true", help="Also include passed tasks")
    args = parser.parse_args()
    run(domain_filter=args.domain, top_n=args.top, include_passed=args.include_passed)
