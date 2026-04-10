"""Extract failure traces from τ²-bench banking_knowledge simulation results.

Reads results.json from eval runs, extracts failed tasks with:
- Task ground truth (user scenario, expected actions, golden procedure)
- Conversation transcript (agent + user messages, tool calls, tool results)
- Expected vs actual actions (what was wrong)
- DB state check, communicate_checks, nl_assertions
- Review errors (LLM judge analysis)
- Discoverable tool analysis — what was mentioned in KB, what was unlocked,
  what was called, and the gap between them

Outputs traces/latest.json that the meta-agent reads to diagnose failures
and plan improvements to agent.py (especially annotate_banking()).

Usage:
  python eval/extract_traces.py          # extract failures
  python eval/extract_traces.py --top 10 # only worst 10 failures
  python eval/extract_traces.py --include-passed  # also include passing tasks
"""

import argparse
import json
import re
import sys
from pathlib import Path

# τ²-bench saves results here by default
DATA_DIR = Path(__file__).parent.parent / "tau2-bench" / "data" / "simulations"
TRACES_DIR = Path(__file__).parent.parent / "traces"

DOMAIN = "banking_knowledge"

# Matches lowercase_name_with_underscores followed by 4+ digit suffix
# e.g. submit_cash_back_dispute_0589, update_transaction_rewards_3847
_DISCOVERABLE_TOOL_PATTERN = re.compile(r'\b([a-z][a-z_]{3,}_\d{4,})\b')

# Content inside these fields is where the 4+ digit tool names live
_KB_TOOL_NAMES = {
    "KB_search",
    "kb_search",
    "search_knowledge_base",
}

# The meta-tools the agent uses to unlock/give discoverable tools
_UNLOCK_TOOLS = {"unlock_discoverable_agent_tool"}
_GIVE_TOOLS = {"give_discoverable_user_tool"}
_LIST_TOOLS = {"list_discoverable_agent_tools"}
_CALL_TOOLS = {"call_discoverable_agent_tool"}

# Base tools that are always in the agent's initial tool list (not discoverable)
_BASE_TOOLS = {
    "get_user_information_by_id",
    "get_user_information_by_name",
    "get_user_information_by_email",
    "log_verification",
    "transfer_to_human_agents",
    "get_current_time",
    "get_referrals_by_user",
    "get_credit_card_transactions_by_user",
    "get_credit_card_accounts_by_user",
    "change_user_email",
    "KB_search",
    "grep",
    "shell",
} | _UNLOCK_TOOLS | _GIVE_TOOLS | _LIST_TOOLS | _CALL_TOOLS


def load_results() -> dict | None:
    """Load results.json for the banking_knowledge eval run."""
    results_path = DATA_DIR / f"eval_{DOMAIN}" / "results.json"
    if not results_path.exists():
        return None
    with open(results_path) as f:
        return json.load(f)


def extract_conversation(messages: list[dict]) -> list[dict]:
    """Extract a compact conversation transcript.

    Truncates agent/user text messages at 1000 chars. Tool results (what the
    agent actually saw from KB_search) are NOT truncated — they contain the
    signal we need to diagnose banking failures.
    """
    transcript = []
    for msg in messages:
        role = msg.get("role", "unknown")
        if role == "system":
            continue  # already known, skip

        entry = {"role": role}

        content = msg.get("content", "")
        if content:
            # Only truncate non-tool messages. Tool messages contain KB_search
            # results which are the primary diagnostic signal for banking.
            if role != "tool" and len(content) > 1000:
                entry["content"] = content[:800] + f"\n... [truncated, {len(content)} chars total]"
            else:
                entry["content"] = content

        tool_calls = msg.get("tool_calls")
        if tool_calls:
            entry["tool_calls"] = []
            for tc in tool_calls:
                tc_entry = {"name": tc.get("name", ""), "arguments": tc.get("arguments", {})}
                entry["tool_calls"].append(tc_entry)

        transcript.append(entry)

    return transcript


def extract_action_checks(reward_info: dict) -> list[dict]:
    """Extract action correctness details from reward_info."""
    checks = []
    for check in reward_info.get("action_checks", []):
        action = check.get("expected_action", {}) or check.get("action", {})
        checks.append({
            "expected_tool": action.get("name", ""),
            "expected_args": action.get("arguments", {}),
            "requestor": action.get("requestor", "assistant"),
            "matched": check.get("action_match", False),
            "tool_type": check.get("tool_type", ""),
        })
    return checks


def extract_task_ground_truth(task: dict) -> dict:
    """Extract the task's ground truth: what the customer wanted and what
    the agent was supposed to do.

    Without this, the meta-agent has to reconstruct the task from the
    conversation transcript. With it, failures are immediately diagnosable.
    """
    gt = {}

    user_scenario = task.get("user_scenario", {})
    if user_scenario:
        instructions = user_scenario.get("instructions", {})
        if isinstance(instructions, dict):
            gt["reason_for_call"] = instructions.get("reason_for_call", "")
            gt["known_info"] = instructions.get("known_info", "")
            gt["task_instructions"] = instructions.get("task_instructions", "")
        elif isinstance(instructions, str):
            gt["task_instructions"] = instructions

        persona = user_scenario.get("persona")
        if persona:
            gt["persona"] = persona

    # Golden actions — what the agent was supposed to do
    eval_criteria = task.get("evaluation_criteria", {})
    if eval_criteria:
        golden_actions = eval_criteria.get("actions", [])
        if golden_actions:
            gt["expected_actions"] = [
                {
                    "name": a.get("name", ""),
                    "arguments": a.get("arguments", {}),
                    "requestor": a.get("requestor", "assistant"),
                    "info": a.get("info", ""),
                }
                for a in golden_actions
            ]

        communicate_info = eval_criteria.get("communicate_info", [])
        if communicate_info:
            gt["expected_communicate"] = communicate_info

    description = task.get("description", {})
    if description:
        gt["purpose"] = description.get("purpose", "")
        gt["relevant_policies"] = description.get("relevant_policies", "")

    return gt


def analyze_discoverable_tools(messages: list[dict]) -> dict:
    """Scan the conversation to identify discoverable tool usage patterns.

    This is the critical diagnostic signal for banking failures. It detects:
    - Which discoverable tool names were mentioned in KB_search results
    - Which the agent unlocked via unlock_discoverable_agent_tool
    - Which it gave to the user via give_discoverable_user_tool
    - Which were actually called
    - The GAPS: mentioned but never unlocked (primary failure class),
      unlocked but never called (wasted), called without unlocking (errors)
    """
    mentioned_in_kb: set[str] = set()
    unlocked_for_agent: set[str] = set()
    unlocked_for_user: set[str] = set()
    actually_called: set[str] = set()

    for msg in messages:
        role = msg.get("role", "")

        # KB_search results → extract mentioned tool names
        if role == "tool":
            content = msg.get("content", "") or ""
            found = _DISCOVERABLE_TOOL_PATTERN.findall(content)
            for name in found:
                mentioned_in_kb.add(name)

        # Agent tool calls → look for unlock/give/call of discoverable tools
        if role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                name = tc.get("name", "") or ""
                args = tc.get("arguments", {}) or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                target = args.get("tool_name", "") if isinstance(args, dict) else ""

                if name in _UNLOCK_TOOLS and target:
                    unlocked_for_agent.add(target)
                elif name in _GIVE_TOOLS and target:
                    unlocked_for_user.add(target)
                elif name in _CALL_TOOLS and target:
                    actually_called.add(target)
                elif name and name not in _BASE_TOOLS and _DISCOVERABLE_TOOL_PATTERN.fullmatch(name):
                    # Agent called a discoverable tool directly (not via call_*)
                    actually_called.add(name)

    missing_unlocks = sorted(
        mentioned_in_kb - unlocked_for_agent - unlocked_for_user
    )
    wasted_unlocks = sorted(
        (unlocked_for_agent | unlocked_for_user) - actually_called
    )
    unlocked_without_mention = sorted(
        (unlocked_for_agent | unlocked_for_user) - mentioned_in_kb
    )
    # Tools called without ever being unlocked — likely tool_not_found errors
    called_without_unlock = sorted(
        actually_called - unlocked_for_agent - unlocked_for_user
    )

    return {
        "mentioned_in_kb": sorted(mentioned_in_kb),
        "unlocked_for_agent": sorted(unlocked_for_agent),
        "unlocked_for_user": sorted(unlocked_for_user),
        "actually_called": sorted(actually_called),
        "missing_unlocks": missing_unlocks,
        "wasted_unlocks": wasted_unlocks,
        "unlocked_without_mention": unlocked_without_mention,
        "called_without_unlock": called_without_unlock,
    }


def extract_task_trace(sim: dict, task_map: dict) -> dict:
    """Extract a single task's failure trace."""
    task_id = sim.get("task_id", "unknown")
    reward_info = sim.get("reward_info", {}) or {}
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

    # Task ground truth — what was the customer trying to do?
    task = task_map.get(task_id)
    if task:
        ground_truth = extract_task_ground_truth(task)
        if ground_truth:
            trace["ground_truth"] = ground_truth

    # Reward breakdown
    db_check = reward_info.get("db_check", {}) or {}
    if db_check:
        trace["db_match"] = db_check.get("db_match", db_check.get("match"))

    reward_breakdown = reward_info.get("reward_breakdown", {}) or {}
    if reward_breakdown:
        trace["reward_breakdown"] = reward_breakdown

    reward_basis = reward_info.get("reward_basis", []) or []
    if reward_basis:
        trace["reward_basis"] = reward_basis

    # Expected actions vs what the agent did
    action_checks = extract_action_checks(reward_info)
    if action_checks:
        trace["actions_expected"] = len(action_checks)
        trace["actions_matched"] = sum(1 for a in action_checks if a["matched"])
        trace["action_details"] = action_checks

    # Communication checks — did agent tell user required info?
    # This is the FIX for the former bug where nl_assertions was mislabeled.
    communicate_checks = reward_info.get("communicate_checks", []) or []
    if communicate_checks:
        trace["communicate_checks"] = communicate_checks

    # NL assertions (separate from communicate_checks — they're different fields)
    nl_assertions = reward_info.get("nl_assertions", []) or []
    if nl_assertions:
        trace["nl_assertions"] = nl_assertions

    # Environment assertions
    env_assertions = reward_info.get("env_assertions", []) or []
    if env_assertions:
        trace["env_assertions"] = env_assertions

    # Conversation transcript (tool results NOT truncated)
    messages = sim.get("messages", [])
    if messages:
        trace["conversation"] = extract_conversation(messages)

    # Discoverable tool analysis — the critical diagnostic for banking
    trace["discoverable_tool_analysis"] = analyze_discoverable_tools(messages)

    # Review/error analysis if available
    review = sim.get("review", {}) or {}
    if review and review.get("errors"):
        trace["review_errors"] = review["errors"]
    if review and review.get("summary"):
        trace["review_summary"] = review["summary"]

    return trace


def run(top_n: int | None = None, include_passed: bool = False):
    """Extract traces and write to traces/latest.json."""
    results = load_results()
    if results is None:
        print(f"  [error] No results for {DOMAIN}", file=sys.stderr)
        print(f"  Expected: {DATA_DIR / f'eval_{DOMAIN}' / 'results.json'}", file=sys.stderr)
        return None

    tasks = results.get("tasks", [])
    task_map = {t["id"]: t for t in tasks}
    sims = results.get("simulations", [])

    all_traces = []
    passed = 0
    failed = 0

    for sim in sims:
        trace = extract_task_trace(sim, task_map)

        if trace["passed"]:
            passed += 1
            if not include_passed:
                continue
        else:
            failed += 1

        trace["domain"] = DOMAIN
        all_traces.append(trace)

    # Aggregate discoverable tool failure signal across failures
    total_missing_unlocks = 0
    total_called_without_unlock = 0
    tasks_with_missing_unlocks = 0
    for t in all_traces:
        if t["passed"]:
            continue
        dta = t.get("discoverable_tool_analysis", {})
        if dta.get("missing_unlocks"):
            tasks_with_missing_unlocks += 1
            total_missing_unlocks += len(dta["missing_unlocks"])
        if dta.get("called_without_unlock"):
            total_called_without_unlock += len(dta["called_without_unlock"])

    summary = {
        "domain": DOMAIN,
        "total_tasks": passed + failed,
        "passed": passed,
        "failed": failed,
        "pass_rate": passed / max(1, passed + failed),
        "discoverable_tool_signal": {
            "tasks_with_missing_unlocks": tasks_with_missing_unlocks,
            "total_missing_unlock_events": total_missing_unlocks,
            "total_called_without_unlock": total_called_without_unlock,
        },
    }

    print(
        f"  {DOMAIN}: {passed}/{passed + failed} passed, "
        f"{failed} failures extracted",
        file=sys.stderr,
    )
    if tasks_with_missing_unlocks:
        print(
            f"  → {tasks_with_missing_unlocks} tasks have unmentioned-then-uncalled tools "
            f"({total_missing_unlocks} events)",
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
    print(f"  Summary: {summary['passed']}/{summary['total_tasks']} passed", file=sys.stderr)

    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract failure traces for meta-agent diagnosis")
    parser.add_argument("--top", type=int, default=None, help="Only keep N worst failures")
    parser.add_argument("--include-passed", action="store_true", help="Also include passed tasks")
    args = parser.parse_args()
    run(top_n=args.top, include_passed=args.include_passed)
