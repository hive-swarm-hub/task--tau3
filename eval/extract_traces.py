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

# Identity fields that satisfy τ-Banking's 2-of-4 verification requirement
_IDENTITY_FIELD_PATTERNS = {
    "dob": re.compile(
        r'\b(?:dob|date\s+of\s+birth|birth\s*date|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b',
        re.IGNORECASE,
    ),
    "email": re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'),
    # Phone must be a real NANP-ish 10-digit structure (3+3+4), not an ISO date.
    # Matches: 5551234567, 555-123-4567, (555) 123-4567, +1 555 123 4567
    # Does NOT match: 1990-01-15 (ISO date has wrong digit grouping)
    "phone": re.compile(
        r'(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}|\b\d{10}\b'
    ),
    "address": re.compile(
        r'\b(?:\d+\s+\w+\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way)|address)\b',
        re.IGNORECASE,
    ),
}

# KB_search "no results" marker — τ²-bench returns this verbatim when BM25 finds nothing
_KB_NO_RESULTS = "No relevant documents found"

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


# ── Priority 1 analyzer: verification gating ───────────────────────────────

def analyze_verification(messages: list[dict]) -> dict:
    """Measure verification-related signals.

    Neutral measurement — does NOT prescribe what should happen. Returns counts
    agents can use to diagnose whether verification gating is a dominant issue.

    Fields:
    - verification_calls: count of log_verification invocations
    - mutation_calls_before_verify: count of discoverable-pattern tool calls
      that happened BEFORE the first log_verification (heuristic only; a real
      implementation in agent.py would enforce this, not just count it)
    - identity_fields_mentioned: which of {dob,email,phone,address} appeared
      in user messages (shows whether the user gave enough info to verify)
    """
    verification_calls = 0
    first_verify_idx: int | None = None
    mutation_calls_before_verify = 0
    identity_fields: set[str] = set()

    for idx, msg in enumerate(messages):
        role = msg.get("role", "")

        # User-provided identity fields
        if role == "user":
            content = msg.get("content", "") or ""
            for field, pattern in _IDENTITY_FIELD_PATTERNS.items():
                if pattern.search(content):
                    identity_fields.add(field)

        # Assistant tool calls
        if role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                name = tc.get("name", "") or ""
                if name == "log_verification":
                    verification_calls += 1
                    if first_verify_idx is None:
                        first_verify_idx = idx
                elif _DISCOVERABLE_TOOL_PATTERN.fullmatch(name) and first_verify_idx is None:
                    mutation_calls_before_verify += 1

    return {
        "verification_calls": verification_calls,
        "mutation_calls_before_verify": mutation_calls_before_verify,
        "identity_fields_mentioned": sorted(identity_fields),
    }


# ── Priority 2 analyzer: argument fidelity ─────────────────────────────────

def analyze_arguments(messages: list[dict], expected_actions: list[dict]) -> dict:
    """Measure argument fidelity — right tool, wrong args.

    The τ²-bench evaluator matches on exact equality of specified argument
    keys. If the agent called the correct tool name but with arguments that
    don't match the golden action, this is a provenance/fidelity failure —
    not a retrieval failure, not a missing unlock.

    Fields:
    - wrong_arg_events: total number of argument-mismatch events
    - correct_tool_wrong_args: tasks with right-tool-wrong-args
    - arg_key_mismatches: {tool_name: [mismatched_keys]}
    """
    # Collect predicted tool calls (name -> list of arg dicts)
    predicted_calls: dict[str, list[dict]] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            name = tc.get("name", "") or ""
            args = tc.get("arguments", {}) or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            predicted_calls.setdefault(name, []).append(args if isinstance(args, dict) else {})

    wrong_arg_events = 0
    correct_tool_wrong_args = 0
    arg_key_mismatches: dict[str, list[str]] = {}

    for expected in expected_actions or []:
        tool_name = expected.get("name", "") if isinstance(expected, dict) else ""
        expected_args = expected.get("arguments", {}) if isinstance(expected, dict) else {}
        if not tool_name or tool_name not in predicted_calls:
            continue

        # Tool name matched — check if any predicted call satisfies the args
        matched = False
        per_call_mismatches: list[set[str]] = []
        for pred_args in predicted_calls[tool_name]:
            mismatched_keys: set[str] = set()
            for k, v in expected_args.items():
                if pred_args.get(k) != v:
                    mismatched_keys.add(k)
            if not mismatched_keys:
                matched = True
                break
            per_call_mismatches.append(mismatched_keys)

        if not matched:
            wrong_arg_events += 1
            correct_tool_wrong_args += 1
            # Union of mismatched keys across all predicted calls for this tool
            all_mismatched = set()
            for m in per_call_mismatches:
                all_mismatched |= m
            arg_key_mismatches[tool_name] = sorted(all_mismatched)

    return {
        "wrong_arg_events": wrong_arg_events,
        "correct_tool_wrong_args": correct_tool_wrong_args,
        "arg_key_mismatches": arg_key_mismatches,
    }


# ── Priority 3 analyzer: retrieval quality ─────────────────────────────────

def analyze_retrieval(messages: list[dict]) -> dict:
    """Measure retrieval query quality and productivity.

    Neutral measurement. Agents use these counts to diagnose whether the
    agent is querying ineffectively (lots of searches, no tool names found)
    or hitting the same query repeatedly (duplicate_query_events > 0).

    Fields:
    - kb_query_count: total KB_search calls
    - unique_kb_queries: distinct query strings (normalized)
    - kb_queries_with_hits: queries whose result was NOT "No relevant documents found"
    - kb_queries_yielding_tool_names: queries whose result contained a discoverable
      tool name pattern — the ONLY signal that matters for Priority 1 success
    - duplicate_query_events: count of repeated queries
    """
    seen_queries: set[str] = set()
    kb_query_count = 0
    unique_kb_queries = 0
    kb_queries_with_hits = 0
    kb_queries_yielding_tool_names = 0
    duplicate_query_events = 0

    # Pair each KB_search call with its result (next tool message after the call)
    pending_query: str | None = None
    for msg in messages:
        role = msg.get("role", "")

        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                name = tc.get("name", "") or ""
                if name == "KB_search":
                    kb_query_count += 1
                    args = tc.get("arguments", {}) or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    query = args.get("query", "") if isinstance(args, dict) else ""
                    normalized = _normalize_query(query)
                    if normalized in seen_queries:
                        duplicate_query_events += 1
                    else:
                        seen_queries.add(normalized)
                        unique_kb_queries += 1
                    pending_query = query
        elif role == "tool" and pending_query is not None:
            content = msg.get("content", "") or ""
            if _KB_NO_RESULTS not in content and content.strip():
                kb_queries_with_hits += 1
            if _DISCOVERABLE_TOOL_PATTERN.search(content):
                kb_queries_yielding_tool_names += 1
            pending_query = None

    return {
        "kb_query_count": kb_query_count,
        "unique_kb_queries": unique_kb_queries,
        "kb_queries_with_hits": kb_queries_with_hits,
        "kb_queries_yielding_tool_names": kb_queries_yielding_tool_names,
        "duplicate_query_events": duplicate_query_events,
    }


def _normalize_query(q: str) -> str:
    """Lowercase, strip punctuation, sort tokens for dedup comparison."""
    import string
    if not q:
        return ""
    cleaned = q.lower().translate(str.maketrans("", "", string.punctuation))
    tokens = sorted(cleaned.split())
    return " ".join(tokens)


# ── Priority 4 analyzer: execution discipline ──────────────────────────────

def analyze_execution(
    messages: list[dict],
    communicate_checks: list[dict],
    expected_actions: list[dict],
    actions_matched: int,
) -> dict:
    """Measure execution-discipline signals.

    Neutral measurement. Agents use these to diagnose whether the agent is
    stopping too early, too late, or failing to communicate required info.

    Fields:
    - turns_until_first_tool_call: how many assistant turns before first action
    - turns_after_last_tool_call: how many text-only turns after the last action
    - communicate_missed: required info strings the agent did not say
    - action_completeness: fraction of expected actions that matched (0.0 to 1.0)
    """
    first_tool_call_turn: int | None = None
    last_tool_call_turn: int | None = None
    assistant_turn = 0
    total_assistant_turns = 0

    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        total_assistant_turns += 1
        assistant_turn += 1
        if msg.get("tool_calls"):
            if first_tool_call_turn is None:
                first_tool_call_turn = assistant_turn
            last_tool_call_turn = assistant_turn

    turns_until_first = first_tool_call_turn or 0
    turns_after_last = (total_assistant_turns - last_tool_call_turn) if last_tool_call_turn else 0

    communicate_missed = []
    for cc in communicate_checks or []:
        if isinstance(cc, dict) and not cc.get("met", False):
            info = cc.get("info", "")
            if info:
                communicate_missed.append(info)

    expected_count = len(expected_actions or [])
    if expected_count > 0:
        action_completeness = actions_matched / expected_count
    else:
        action_completeness = 1.0

    return {
        "turns_until_first_tool_call": turns_until_first,
        "turns_after_last_tool_call": turns_after_last,
        "communicate_missed": communicate_missed,
        "action_completeness": round(action_completeness, 4),
    }


# ── Failure classification ─────────────────────────────────────────────────

def classify_primary_failure(trace: dict) -> str:
    """Classify a trace into one of 4 priority failure classes.

    Deterministic rules in priority order. If a trace matches multiple
    priorities, the HIGHEST priority (lowest number) wins — because
    deterministic blockers mask downstream failures (you can't see a
    communication miss until you get past verification gating).

    Returns one of:
      passed
      priority_1_verification_or_unlock
      priority_2_wrong_arguments
      priority_3_retrieval_miss
      priority_4_execution_discipline
      unknown
    """
    if trace.get("passed"):
        return "passed"

    dta = trace.get("discoverable_tool_analysis", {}) or {}

    # P1: hard-gating failures (deterministic blockers)
    if dta.get("missing_unlocks") or dta.get("called_without_unlock"):
        return "priority_1_verification_or_unlock"

    verification = trace.get("verification_analysis", {}) or {}
    if verification.get("mutation_calls_before_verify", 0) > 0:
        return "priority_1_verification_or_unlock"

    # P2: right tool, wrong args (exact-equality evaluator kills these)
    arg_analysis = trace.get("argument_analysis", {}) or {}
    if arg_analysis.get("correct_tool_wrong_args", 0) > 0:
        return "priority_2_wrong_arguments"

    # P3: retrieval-side failure — searched but didn't find useful docs
    retrieval = trace.get("retrieval_analysis", {}) or {}
    if (
        retrieval.get("kb_query_count", 0) >= 3
        and retrieval.get("kb_queries_yielding_tool_names", 0) == 0
    ):
        return "priority_3_retrieval_miss"

    # P4: catch-all for under/over action, max_steps, communication
    if trace.get("termination_reason") == "max_steps":
        return "priority_4_execution_discipline"
    if trace.get("communicate_checks") and any(
        not c.get("met", False) for c in trace["communicate_checks"] if isinstance(c, dict)
    ):
        return "priority_4_execution_discipline"
    if trace.get("actions_expected", 0) != trace.get("actions_matched", 0):
        return "priority_4_execution_discipline"

    return "unknown"


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

    # Priority 1-4 analyzers (neutral measurement, no prescription)
    trace["verification_analysis"] = analyze_verification(messages)

    # Build expected_actions list from ground_truth for analyzers that need it
    expected_actions = []
    if "ground_truth" in trace and trace["ground_truth"].get("expected_actions"):
        expected_actions = trace["ground_truth"]["expected_actions"]

    trace["argument_analysis"] = analyze_arguments(messages, expected_actions)
    trace["retrieval_analysis"] = analyze_retrieval(messages)
    trace["execution_analysis"] = analyze_execution(
        messages,
        trace.get("communicate_checks", []),
        expected_actions,
        trace.get("actions_matched", 0),
    )

    # Classify into one of the 4 priority failure classes
    trace["primary_failure_class"] = classify_primary_failure(trace)

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
    failure_class_counts = {
        "priority_1_verification_or_unlock": 0,
        "priority_2_wrong_arguments": 0,
        "priority_3_retrieval_miss": 0,
        "priority_4_execution_discipline": 0,
        "unknown": 0,
    }
    for t in all_traces:
        if t["passed"]:
            continue
        dta = t.get("discoverable_tool_analysis", {})
        if dta.get("missing_unlocks"):
            tasks_with_missing_unlocks += 1
            total_missing_unlocks += len(dta["missing_unlocks"])
        if dta.get("called_without_unlock"):
            total_called_without_unlock += len(dta["called_without_unlock"])
        cls = t.get("primary_failure_class", "unknown")
        if cls in failure_class_counts:
            failure_class_counts[cls] += 1
        else:
            failure_class_counts["unknown"] += 1

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
        "failure_class_counts": failure_class_counts,
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
    # Priority breakdown
    nz = {k: v for k, v in failure_class_counts.items() if v > 0}
    if nz:
        print(f"  Failure classes:", file=sys.stderr)
        for cls, count in sorted(nz.items(), key=lambda x: -x[1]):
            print(f"    {cls}: {count}", file=sys.stderr)

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
