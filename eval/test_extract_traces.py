"""Standalone tests for extract_traces.py — no τ²-bench install needed.

Tests the discoverable_tool_analysis logic against mock conversation data
covering every edge case:

    1. Happy path: KB mentions → unlock → call (clean, no gaps)
    2. Missing unlock: KB mentions tool, agent never unlocks (primary failure)
    3. Called without unlock: agent calls tool before unlocking (would error)
    4. Wasted unlock: agent unlocks but never calls
    5. Unlocked without mention: unlocks tool never seen in KB
    6. User give: give_discoverable_user_tool instead of unlock
    7. Multiple tools mixed across a conversation
    8. Empty messages
    9. Tool name regex edge cases
    10. extract_task_ground_truth: task metadata pulled from task definition
    11. End-to-end: extract_task_trace builds complete trace with all fields
    12. extract_conversation: tool results not truncated, agent text truncated

Run:
    python eval/test_extract_traces.py

This test only depends on the Python stdlib — you do NOT need to run
prepare.sh or install τ²-bench first. It tests extract_traces.py in
isolation using mock data.

For testing annotate_banking() (the annotator in agent.py), run
test_annotator.py separately after bash prepare.sh.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.extract_traces import (
    analyze_discoverable_tools,
    analyze_verification,
    analyze_arguments,
    analyze_retrieval,
    analyze_execution,
    classify_primary_failure,
    extract_task_ground_truth,
    extract_conversation,
    extract_task_trace,
    _DISCOVERABLE_TOOL_PATTERN,
    _normalize_query,
)


# ── test harness ─────────────────────────────────────────────────────────────

PASSED = 0
FAILED = 0


def assert_eq(actual, expected, label):
    global PASSED, FAILED
    if actual == expected:
        PASSED += 1
        print(f"  ✓ {label}")
    else:
        FAILED += 1
        print(f"  ✗ {label}")
        print(f"    expected: {expected}")
        print(f"    actual:   {actual}")


def assert_true(cond, label):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {label}")
    else:
        FAILED += 1
        print(f"  ✗ {label}")


def section(title):
    print(f"\n── {title} ─────────────────────────────────────────")


# ── test 1: happy path ───────────────────────────────────────────────────────
# Agent searches KB → sees tool name → unlocks it → calls it
# Expected: clean analysis with no gaps

def test_happy_path():
    section("test_happy_path — KB → unlock → call")
    messages = [
        {"role": "user", "content": "I want to dispute a charge on my credit card"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "KB_search", "arguments": {"query": "credit card dispute procedure"}}
        ]},
        {"role": "tool", "content": (
            "To file a dispute on a Crypto-Cash Back card, the agent must call "
            "submit_cash_back_dispute_0589 with the transaction_id. Before calling, "
            "verify the customer's identity."
        )},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "unlock_discoverable_agent_tool",
             "arguments": {"agent_tool_name": "submit_cash_back_dispute_0589"}}
        ]},
        {"role": "tool", "content": "Tool unlocked"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "submit_cash_back_dispute_0589",
             "arguments": {"transaction_id": "txn_123"}}
        ]},
        {"role": "tool", "content": "Dispute submitted"},
    ]

    dta = analyze_discoverable_tools(messages)
    assert_eq(dta["mentioned_in_kb"], ["submit_cash_back_dispute_0589"], "KB mention detected")
    assert_eq(dta["unlocked_for_agent"], ["submit_cash_back_dispute_0589"], "agent unlock detected")
    assert_eq(dta["actually_called"], ["submit_cash_back_dispute_0589"], "tool call detected")
    assert_eq(dta["missing_unlocks"], [], "no missing unlocks")
    assert_eq(dta["wasted_unlocks"], [], "no wasted unlocks")
    assert_eq(dta["called_without_unlock"], [], "no orphan calls")


# ── test 2: missing unlock (primary failure class) ───────────────────────────

def test_missing_unlock():
    section("test_missing_unlock — KB mention, no unlock (primary banking failure)")
    messages = [
        {"role": "user", "content": "Update my transaction rewards please"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "KB_search", "arguments": {"query": "update rewards"}}
        ]},
        {"role": "tool", "content": (
            "To update transaction rewards, call update_transaction_rewards_3847 "
            "with the user_id and the amount."
        )},
        # Agent never unlocks, just says something and gives up
        {"role": "assistant", "content": "Let me check with another team."},
    ]

    dta = analyze_discoverable_tools(messages)
    assert_eq(dta["mentioned_in_kb"], ["update_transaction_rewards_3847"], "KB mention detected")
    assert_eq(dta["unlocked_for_agent"], [], "no unlock")
    assert_eq(dta["actually_called"], [], "no call")
    assert_eq(dta["missing_unlocks"], ["update_transaction_rewards_3847"], "missing_unlocks populated")


# ── test 3: called without unlock (would error in real eval) ─────────────────

def test_called_without_unlock():
    section("test_called_without_unlock — agent skips the unlock step")
    messages = [
        {"role": "user", "content": "File a dispute"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "KB_search", "arguments": {"query": "dispute"}}
        ]},
        {"role": "tool", "content": "To file a dispute, use submit_dispute_tool_1111."},
        # Agent skips the unlock and calls directly — would get tool_not_found
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "submit_dispute_tool_1111", "arguments": {}}
        ]},
        {"role": "tool", "content": "Error: tool_not_found"},
    ]

    dta = analyze_discoverable_tools(messages)
    assert_eq(dta["mentioned_in_kb"], ["submit_dispute_tool_1111"], "KB mention detected")
    assert_eq(dta["actually_called"], ["submit_dispute_tool_1111"], "direct call detected")
    assert_eq(dta["unlocked_for_agent"], [], "no unlock")
    assert_eq(dta["called_without_unlock"], ["submit_dispute_tool_1111"], "called_without_unlock populated")


# ── test 4: wasted unlock (unlock but never call) ────────────────────────────

def test_wasted_unlock():
    section("test_wasted_unlock — unlock but never call")
    messages = [
        {"role": "user", "content": "Help"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "KB_search", "arguments": {"query": "loan"}}
        ]},
        {"role": "tool", "content": "Use apply_loan_9999 to submit a loan application."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "unlock_discoverable_agent_tool",
             "arguments": {"agent_tool_name": "apply_loan_9999"}}
        ]},
        {"role": "tool", "content": "Tool unlocked"},
        # Agent gets stuck and never actually calls the tool
        {"role": "assistant", "content": "Actually, let me think again."},
    ]

    dta = analyze_discoverable_tools(messages)
    assert_eq(dta["mentioned_in_kb"], ["apply_loan_9999"], "KB mention detected")
    assert_eq(dta["unlocked_for_agent"], ["apply_loan_9999"], "unlock detected")
    assert_eq(dta["actually_called"], [], "no call")
    assert_eq(dta["wasted_unlocks"], ["apply_loan_9999"], "wasted_unlocks populated")


# ── test 5: unlocked without mention (agent hallucinated tool name) ──────────

def test_unlocked_without_mention():
    section("test_unlocked_without_mention — agent guesses a tool name")
    messages = [
        {"role": "user", "content": "Just do something"},
        # Agent unlocks a tool without searching KB first — probably hallucinated
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "unlock_discoverable_agent_tool",
             "arguments": {"agent_tool_name": "make_up_tool_5555"}}
        ]},
        {"role": "tool", "content": "Error: tool not found in registry"},
    ]

    dta = analyze_discoverable_tools(messages)
    assert_eq(dta["mentioned_in_kb"], [], "no KB mention")
    assert_eq(dta["unlocked_for_agent"], ["make_up_tool_5555"], "unlock detected")
    assert_eq(dta["unlocked_without_mention"], ["make_up_tool_5555"], "unlocked_without_mention populated")


# ── test 6: give to user (customer performs action) ──────────────────────────

def test_give_to_user():
    section("test_give_to_user — customer performs action")
    messages = [
        {"role": "user", "content": "I want to submit my own dispute"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "KB_search", "arguments": {"query": "customer dispute"}}
        ]},
        {"role": "tool", "content": (
            "When the customer submits a dispute themselves, give them "
            "customer_file_dispute_7777 so they can call it."
        )},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "give_discoverable_user_tool",
             "arguments": {"discoverable_tool_name": "customer_file_dispute_7777"}}
        ]},
        {"role": "tool", "content": "Tool given to user"},
    ]

    dta = analyze_discoverable_tools(messages)
    assert_eq(dta["mentioned_in_kb"], ["customer_file_dispute_7777"], "KB mention")
    assert_eq(dta["unlocked_for_user"], ["customer_file_dispute_7777"], "give detected")
    assert_eq(dta["unlocked_for_agent"], [], "no agent unlock")
    assert_eq(dta["missing_unlocks"], [], "no missing (it was given to user)")


# ── test 7: multiple tools mixed across a conversation ───────────────────────

def test_multiple_tools():
    section("test_multiple_tools — three tools, one mishandled")
    messages = [
        {"role": "user", "content": "I need help with three things"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "KB_search", "arguments": {"query": "multiple"}}
        ]},
        {"role": "tool", "content": (
            "For issue A use submit_a_1111. For issue B use update_b_2222. "
            "For issue C the customer must use customer_c_3333."
        )},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "unlock_discoverable_agent_tool",
             "arguments": {"agent_tool_name": "submit_a_1111"}}
        ]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "submit_a_1111", "arguments": {}}
        ]},
        # tool B mentioned but forgotten — should show in missing_unlocks
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "give_discoverable_user_tool",
             "arguments": {"discoverable_tool_name": "customer_c_3333"}}
        ]},
    ]

    dta = analyze_discoverable_tools(messages)
    assert_eq(
        sorted(dta["mentioned_in_kb"]),
        ["customer_c_3333", "submit_a_1111", "update_b_2222"],
        "all three mentioned"
    )
    assert_eq(dta["unlocked_for_agent"], ["submit_a_1111"], "A unlocked for agent")
    assert_eq(dta["unlocked_for_user"], ["customer_c_3333"], "C given to user")
    assert_eq(dta["actually_called"], ["submit_a_1111"], "A called")
    assert_eq(dta["missing_unlocks"], ["update_b_2222"], "B is the missing unlock")


# ── test 7b: v1.0.0 argument name variants (regression guard) ───────────────

def test_v1_param_names():
    """τ²-bench v1.0.0 renamed the meta-tool parameters. Ensure the extractor
    recognizes all three variants (legacy + the two real v1.0.0 names).
    This is a regression guard: a previous bug hard-coded only 'tool_name',
    making every task report inflated missing_unlocks on real eval runs.
    """
    section("test_v1_param_names — agent_tool_name / discoverable_tool_name / tool_name")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "KB_search", "arguments": {"query": "all"}}
        ]},
        {"role": "tool", "content": (
            "Use agent_side_0001 for agent action. "
            "Use legacy_style_0002 for legacy compat. "
            "Have the customer use user_side_0003."
        )},
        # v1.0.0 real parameter name for unlock_discoverable_agent_tool
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "unlock_discoverable_agent_tool",
             "arguments": {"agent_tool_name": "agent_side_0001"}}
        ]},
        # legacy param name (pre-v1.0.0) should still be recognized
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "unlock_discoverable_agent_tool",
             "arguments": {"tool_name": "legacy_style_0002"}}
        ]},
        # v1.0.0 real parameter name for give_discoverable_user_tool
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "give_discoverable_user_tool",
             "arguments": {"discoverable_tool_name": "user_side_0003"}}
        ]},
    ]
    dta = analyze_discoverable_tools(messages)
    assert_eq(
        sorted(dta["unlocked_for_agent"]),
        ["agent_side_0001", "legacy_style_0002"],
        "both v1.0.0 and legacy agent unlock names detected",
    )
    assert_eq(
        dta["unlocked_for_user"],
        ["user_side_0003"],
        "v1.0.0 user give name detected",
    )
    assert_eq(
        dta["missing_unlocks"],
        [],
        "no missing unlocks when all three variants are recognized",
    )


# ── test 8: empty / edge cases ───────────────────────────────────────────────

def test_empty_messages():
    section("test_empty_messages")
    dta = analyze_discoverable_tools([])
    assert_eq(dta["mentioned_in_kb"], [], "empty mentioned")
    assert_eq(dta["missing_unlocks"], [], "empty missing")
    assert_eq(dta["called_without_unlock"], [], "empty called_without")


# ── test 9: tool name regex edge cases ───────────────────────────────────────

def test_regex_patterns():
    section("test_regex_patterns")
    test_cases = [
        ("submit_cash_back_dispute_0589", True, "standard 4-digit suffix"),
        ("update_transaction_rewards_3847", True, "multi-word"),
        ("my_tool_12345", True, "5-digit suffix"),
        ("tool_123", False, "only 3 digits — too short"),
        ("SUBMIT_TOOL_9999", False, "uppercase not allowed"),
        ("submit-cash-back-0589", False, "hyphens not allowed"),
        ("no_digit_suffix", False, "no digit suffix"),
    ]
    for text, should_find, label in test_cases:
        match = _DISCOVERABLE_TOOL_PATTERN.search(text)
        found = match is not None
        # For positive cases, the full text should be the match
        ok = (should_find == found) and (not should_find or match.group(0) == text)
        assert_true(ok, f"{label}: {text!r}")


# ── test 10: task ground truth extraction ────────────────────────────────────

def test_ground_truth():
    section("test_extract_task_ground_truth")
    task = {
        "id": "bk_042",
        "user_scenario": {
            "persona": "A busy professional",
            "instructions": {
                "reason_for_call": "Dispute an unauthorized charge",
                "known_info": "user_id=u_123, transaction_id=txn_456",
                "task_instructions": "Get the agent to file the dispute and confirm the refund amount",
            },
        },
        "evaluation_criteria": {
            "actions": [
                {
                    "name": "log_verification",
                    "arguments": {"user_id": "u_123"},
                    "requestor": "assistant",
                    "info": "must verify identity first",
                },
                {
                    "name": "submit_cash_back_dispute_0589",
                    "arguments": {"transaction_id": "txn_456"},
                    "requestor": "assistant",
                },
            ],
            "communicate_info": ["refund of $50"],
        },
    }
    gt = extract_task_ground_truth(task)
    assert_eq(gt["reason_for_call"], "Dispute an unauthorized charge", "reason_for_call")
    assert_eq(gt["persona"], "A busy professional", "persona")
    assert_eq(len(gt["expected_actions"]), 2, "two expected actions")
    assert_eq(gt["expected_actions"][0]["name"], "log_verification", "first action name")
    assert_eq(gt["expected_communicate"], ["refund of $50"], "communicate info")


# ── test 11: end-to-end trace extraction ─────────────────────────────────────

def test_end_to_end_trace():
    section("test_end_to_end extract_task_trace")
    sim = {
        "task_id": "bk_042",
        "reward_info": {
            "reward": 0.0,
            "db_check": {"db_match": False, "db_reward": 0.0},
            "action_checks": [
                {
                    "expected_action": {
                        "name": "log_verification",
                        "arguments": {"user_id": "u_123"},
                    },
                    "action_match": True,
                    "tool_type": "WRITE",
                },
                {
                    "expected_action": {
                        "name": "submit_cash_back_dispute_0589",
                        "arguments": {"transaction_id": "txn_456"},
                    },
                    "action_match": False,
                    "tool_type": "WRITE",
                },
            ],
            "communicate_checks": [{"info": "refund of $50", "met": False}],
            "reward_breakdown": {"DB": 0.0, "ACTION": 0.0, "COMMUNICATE": 0.0},
        },
        "termination_reason": "agent_stop",
        "duration": 42.5,
        "agent_cost": 0.015,
        "messages": [
            {"role": "user", "content": "Dispute my charge"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"name": "log_verification", "arguments": {"user_id": "u_123"}}
            ]},
            {"role": "tool", "content": "Verified"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"name": "KB_search", "arguments": {"query": "dispute"}}
            ]},
            {"role": "tool", "content": (
                "Call submit_cash_back_dispute_0589 to file the dispute."
            )},
            # Agent forgot to unlock
            {"role": "assistant", "content": "I'm not sure how to proceed."},
        ],
    }

    task_map = {
        "bk_042": {
            "id": "bk_042",
            "user_scenario": {
                "instructions": {
                    "reason_for_call": "dispute",
                    "task_instructions": "file the dispute",
                },
            },
            "evaluation_criteria": {
                "actions": [{"name": "submit_cash_back_dispute_0589", "arguments": {}}],
            },
        },
    }

    trace = extract_task_trace(sim, task_map)
    assert_eq(trace["task_id"], "bk_042", "task_id")
    assert_eq(trace["reward"], 0.0, "reward")
    assert_eq(trace["passed"], False, "passed")
    assert_eq(trace["db_match"], False, "db_match")
    assert_eq(trace["actions_expected"], 2, "actions_expected")
    assert_eq(trace["actions_matched"], 1, "actions_matched")
    assert_true("ground_truth" in trace, "ground_truth present")
    assert_true("discoverable_tool_analysis" in trace, "discoverable_tool_analysis present")
    assert_eq(
        trace["discoverable_tool_analysis"]["missing_unlocks"],
        ["submit_cash_back_dispute_0589"],
        "missing_unlocks captured in end-to-end trace",
    )
    assert_eq(
        trace["communicate_checks"],
        [{"info": "refund of $50", "met": False}],
        "communicate_checks correctly read (not mislabeled as nl_assertions)",
    )


# ── test 12: conversation extraction with tool-result non-truncation ─────────

def test_extract_conversation_truncation():
    section("test_extract_conversation — tool results NOT truncated, agent text IS")

    long_agent_text = "x" * 2000  # 2000 chars of agent text
    long_tool_result = "y" * 3000  # 3000 chars of tool result (e.g., KB_search)

    messages = [
        {"role": "user", "content": "test"},
        {"role": "assistant", "content": long_agent_text},
        {"role": "tool", "content": long_tool_result},
    ]

    transcript = extract_conversation(messages)
    assert_eq(len(transcript), 3, "three entries")
    # Agent text should be truncated
    assert_true(
        len(transcript[1]["content"]) < 1000,
        "agent text truncated (was 2000 chars)"
    )
    assert_true(
        "truncated" in transcript[1]["content"],
        "truncation marker present on agent text"
    )
    # Tool result should NOT be truncated — this is the key fix
    assert_eq(
        transcript[2]["content"],
        long_tool_result,
        "tool result NOT truncated (3000 chars preserved — critical for KB_search diagnosis)"
    )


# ── test 13: analyze_verification ────────────────────────────────────────────

def test_analyze_verification_clean():
    section("test_analyze_verification — verify before mutation (clean)")
    messages = [
        {"role": "user", "content": "email: alice@test.com, DOB: 1990-01-15"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "log_verification", "arguments": {"user_id": "u_1", "fields": ["email", "dob"]}}
        ]},
        {"role": "tool", "content": "verified"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "unlock_discoverable_agent_tool", "arguments": {"agent_tool_name": "submit_dispute_1234"}}
        ]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "submit_dispute_1234", "arguments": {}}
        ]},
    ]
    va = analyze_verification(messages)
    assert_eq(va["verification_calls"], 1, "one verification call")
    assert_eq(va["mutation_calls_before_verify"], 0, "no mutation before verify")
    assert_eq(sorted(va["identity_fields_mentioned"]), ["dob", "email"], "dob + email mentioned")


def test_analyze_verification_gate_violation():
    section("test_analyze_verification — mutation before verify")
    messages = [
        {"role": "user", "content": "please dispute this"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "submit_dispute_0001", "arguments": {"amount": 50}}
        ]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "log_verification", "arguments": {"user_id": "u_1"}}
        ]},
    ]
    va = analyze_verification(messages)
    assert_eq(va["verification_calls"], 1, "verification called")
    assert_eq(va["mutation_calls_before_verify"], 1, "one mutation before verify")


def test_analyze_verification_no_identity():
    section("test_analyze_verification — no identity fields in user messages")
    messages = [
        {"role": "user", "content": "hey help me"},
        {"role": "assistant", "content": "I need to verify you first."},
    ]
    va = analyze_verification(messages)
    assert_eq(va["verification_calls"], 0, "no verification")
    assert_eq(va["identity_fields_mentioned"], [], "no identity fields")


# ── test 14: analyze_arguments ───────────────────────────────────────────────

def test_analyze_arguments_match():
    section("test_analyze_arguments — correct tool, correct args")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "submit_dispute_0001", "arguments": {"transaction_id": "txn_42"}}
        ]},
    ]
    expected = [{"name": "submit_dispute_0001", "arguments": {"transaction_id": "txn_42"}}]
    result = analyze_arguments(messages, expected)
    assert_eq(result["wrong_arg_events"], 0, "no wrong arg events")
    assert_eq(result["correct_tool_wrong_args"], 0, "no correct-tool-wrong-args")


def test_analyze_arguments_wrong():
    section("test_analyze_arguments — correct tool, wrong argument value")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "submit_dispute_0001", "arguments": {"transaction_id": "txn_99", "amount": 50}}
        ]},
    ]
    expected = [{"name": "submit_dispute_0001", "arguments": {"transaction_id": "txn_42", "amount": 50}}]
    result = analyze_arguments(messages, expected)
    assert_eq(result["wrong_arg_events"], 1, "one wrong arg event")
    assert_eq(result["correct_tool_wrong_args"], 1, "correct_tool_wrong_args = 1")
    assert_eq(
        result["arg_key_mismatches"]["submit_dispute_0001"],
        ["transaction_id"],
        "transaction_id flagged as mismatched",
    )


def test_analyze_arguments_tool_not_called():
    section("test_analyze_arguments — expected tool never called (not our concern)")
    messages = [
        {"role": "assistant", "content": "I don't know how to help."},
    ]
    expected = [{"name": "submit_dispute_0001", "arguments": {"transaction_id": "txn_42"}}]
    result = analyze_arguments(messages, expected)
    # If the tool wasn't called at all, this is a different failure class (P3 or P4), not P2
    assert_eq(result["wrong_arg_events"], 0, "not a wrong-arg event when tool never called")


# ── test 15: analyze_retrieval ───────────────────────────────────────────────

def test_analyze_retrieval_productive():
    section("test_analyze_retrieval — searches that yield tool names")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "KB_search", "arguments": {"query": "dispute procedure"}}
        ]},
        {"role": "tool", "content": "Use submit_cash_back_dispute_0589 to file it."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "KB_search", "arguments": {"query": "refund timing"}}
        ]},
        {"role": "tool", "content": "processed in 5-7 business days"},
    ]
    r = analyze_retrieval(messages)
    assert_eq(r["kb_query_count"], 2, "2 queries")
    assert_eq(r["unique_kb_queries"], 2, "both unique")
    assert_eq(r["kb_queries_with_hits"], 2, "both had hits")
    assert_eq(r["kb_queries_yielding_tool_names"], 1, "only first yielded tool name")
    assert_eq(r["duplicate_query_events"], 0, "no duplicates")


def test_analyze_retrieval_duplicate_queries():
    section("test_analyze_retrieval — duplicate queries")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "KB_search", "arguments": {"query": "dispute procedure"}}
        ]},
        {"role": "tool", "content": "No relevant documents found"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "KB_search", "arguments": {"query": "Dispute  Procedure"}}
        ]},
        {"role": "tool", "content": "No relevant documents found"},
    ]
    r = analyze_retrieval(messages)
    assert_eq(r["kb_query_count"], 2, "2 queries")
    assert_eq(r["unique_kb_queries"], 1, "only 1 unique after normalization")
    assert_eq(r["duplicate_query_events"], 1, "1 duplicate event")
    assert_eq(r["kb_queries_with_hits"], 0, "no hits")


def test_normalize_query():
    section("test_normalize_query — deterministic normalization")
    assert_eq(_normalize_query("Dispute Procedure"), "dispute procedure", "lowercase")
    assert_eq(_normalize_query("Dispute, procedure."), "dispute procedure", "strip punctuation")
    assert_eq(_normalize_query("procedure Dispute"), "dispute procedure", "sorted tokens")
    assert_eq(_normalize_query(""), "", "empty")


# ── test 16: analyze_execution ───────────────────────────────────────────────

def test_analyze_execution_complete():
    section("test_analyze_execution — all actions matched")
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "log_verification", "arguments": {}}
        ]},
        {"role": "tool", "content": "verified"},
        {"role": "assistant", "content": "Done — refund of $50 processed."},
    ]
    e = analyze_execution(messages, [{"info": "refund of $50", "met": True}], [{"name": "log_verification"}], 1)
    assert_eq(e["turns_until_first_tool_call"], 1, "first tool call was turn 1")
    assert_eq(e["turns_after_last_tool_call"], 1, "1 text turn after last tool call")
    assert_eq(e["communicate_missed"], [], "no missed")
    assert_eq(e["action_completeness"], 1.0, "action_completeness = 1.0")


def test_analyze_execution_missed_communicate():
    section("test_analyze_execution — communicate miss")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"name": "submit_dispute_0001", "arguments": {}}
        ]},
        {"role": "assistant", "content": "Done."},
    ]
    e = analyze_execution(
        messages,
        [{"info": "refund of $50", "met": False}, {"info": "5-7 business days", "met": False}],
        [],
        0,
    )
    assert_eq(e["communicate_missed"], ["refund of $50", "5-7 business days"], "both missed")


# ── test 17: classify_primary_failure ────────────────────────────────────────

def test_classify_passed():
    section("test_classify_primary_failure — passed")
    trace = {"passed": True}
    assert_eq(classify_primary_failure(trace), "passed", "passed")


def test_classify_p1_missing_unlock():
    section("test_classify_primary_failure — P1 missing unlock")
    trace = {
        "passed": False,
        "discoverable_tool_analysis": {"missing_unlocks": ["submit_dispute_0001"], "called_without_unlock": []},
    }
    assert_eq(classify_primary_failure(trace), "priority_1_verification_or_unlock", "P1 missing unlock")


def test_classify_p1_mutation_before_verify():
    section("test_classify_primary_failure — P1 mutation before verify")
    trace = {
        "passed": False,
        "discoverable_tool_analysis": {"missing_unlocks": [], "called_without_unlock": []},
        "verification_analysis": {"mutation_calls_before_verify": 2},
    }
    assert_eq(classify_primary_failure(trace), "priority_1_verification_or_unlock", "P1 mutation before verify")


def test_classify_p2_wrong_args():
    section("test_classify_primary_failure — P2 wrong arguments")
    trace = {
        "passed": False,
        "discoverable_tool_analysis": {"missing_unlocks": [], "called_without_unlock": []},
        "verification_analysis": {"mutation_calls_before_verify": 0},
        "argument_analysis": {"correct_tool_wrong_args": 1},
    }
    assert_eq(classify_primary_failure(trace), "priority_2_wrong_arguments", "P2 wrong args")


def test_classify_p3_retrieval():
    section("test_classify_primary_failure — P3 retrieval miss")
    trace = {
        "passed": False,
        "discoverable_tool_analysis": {"missing_unlocks": [], "called_without_unlock": []},
        "verification_analysis": {"mutation_calls_before_verify": 0},
        "argument_analysis": {"correct_tool_wrong_args": 0},
        "retrieval_analysis": {"kb_query_count": 5, "kb_queries_yielding_tool_names": 0},
    }
    assert_eq(classify_primary_failure(trace), "priority_3_retrieval_miss", "P3 retrieval miss")


def test_classify_p4_max_steps():
    section("test_classify_primary_failure — P4 max_steps")
    trace = {
        "passed": False,
        "termination_reason": "max_steps",
        "discoverable_tool_analysis": {"missing_unlocks": [], "called_without_unlock": []},
        "verification_analysis": {"mutation_calls_before_verify": 0},
        "argument_analysis": {"correct_tool_wrong_args": 0},
        "retrieval_analysis": {"kb_query_count": 1, "kb_queries_yielding_tool_names": 0},
    }
    assert_eq(classify_primary_failure(trace), "priority_4_execution_discipline", "P4 max_steps")


def test_classify_p4_communicate_miss():
    section("test_classify_primary_failure — P4 communicate miss")
    trace = {
        "passed": False,
        "termination_reason": "agent_stop",
        "discoverable_tool_analysis": {"missing_unlocks": [], "called_without_unlock": []},
        "verification_analysis": {"mutation_calls_before_verify": 0},
        "argument_analysis": {"correct_tool_wrong_args": 0},
        "retrieval_analysis": {"kb_query_count": 1, "kb_queries_yielding_tool_names": 1},
        "communicate_checks": [{"info": "refund", "met": False}],
    }
    assert_eq(classify_primary_failure(trace), "priority_4_execution_discipline", "P4 communicate miss")


def test_classify_precedence():
    section("test_classify_primary_failure — P1 takes precedence over P2")
    # Trace has both a missing unlock (P1) AND wrong args (P2)
    # P1 should win because deterministic blockers mask downstream failures
    trace = {
        "passed": False,
        "discoverable_tool_analysis": {"missing_unlocks": ["submit_dispute_0001"], "called_without_unlock": []},
        "verification_analysis": {"mutation_calls_before_verify": 0},
        "argument_analysis": {"correct_tool_wrong_args": 1},
    }
    assert_eq(
        classify_primary_failure(trace),
        "priority_1_verification_or_unlock",
        "P1 wins over P2 when both present",
    )


# ── runner ───────────────────────────────────────────────────────────────────

def main():
    test_happy_path()
    test_missing_unlock()
    test_called_without_unlock()
    test_wasted_unlock()
    test_unlocked_without_mention()
    test_give_to_user()
    test_multiple_tools()
    test_v1_param_names()
    test_empty_messages()
    test_regex_patterns()
    test_ground_truth()
    test_end_to_end_trace()
    test_extract_conversation_truncation()

    # New priority framework tests
    test_analyze_verification_clean()
    test_analyze_verification_gate_violation()
    test_analyze_verification_no_identity()
    test_analyze_arguments_match()
    test_analyze_arguments_wrong()
    test_analyze_arguments_tool_not_called()
    test_analyze_retrieval_productive()
    test_analyze_retrieval_duplicate_queries()
    test_normalize_query()
    test_analyze_execution_complete()
    test_analyze_execution_missed_communicate()
    test_classify_passed()
    test_classify_p1_missing_unlock()
    test_classify_p1_mutation_before_verify()
    test_classify_p2_wrong_args()
    test_classify_p3_retrieval()
    test_classify_p4_max_steps()
    test_classify_p4_communicate_miss()
    test_classify_precedence()

    print(f"\n{'='*60}")
    print(f"  {PASSED} passed, {FAILED} failed")
    print(f"{'='*60}")
    sys.exit(0 if FAILED == 0 else 1)


if __name__ == "__main__":
    main()
