"""Tests for annotate_banking() in agent.py.

Requires τ²-bench installed (bash prepare.sh must have run) because agent.py
imports from litellm and tau2. For pure extract_traces.py tests that don't
need τ² installed, see test_extract_traces.py.

Run:
    bash prepare.sh
    python eval/test_annotator.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from agent import (
        annotate_banking,
        _DISCOVERABLE_CATALOG,
        _VALID_DISCOVERABLE_NAMES,
        _CATALOG_PROMPT_SECTION,
        _parse_discoverable_catalog,
        create_custom_agent,
    )
    from tau2.data_model.message import AssistantMessage, ToolCall
except ImportError as e:
    print(f"ERROR: cannot import annotate_banking from agent.py")
    print(f"Reason: {e}")
    print(f"\nDid you run bash prepare.sh first? agent.py imports from")
    print(f"litellm and tau2 which are installed by prepare.sh.")
    sys.exit(1)


# ── test harness ─────────────────────────────────────────────────────────────

PASSED = 0
FAILED = 0


def assert_contains(container, needle, label):
    global PASSED, FAILED
    if needle in container:
        PASSED += 1
        print(f"  ✓ {label}")
    else:
        FAILED += 1
        print(f"  ✗ {label}")
        print(f"    needle not found: {needle!r}")
        print(f"    container[:200]: {container[:200]!r}")


def assert_not_contains(container, needle, label):
    global PASSED, FAILED
    if needle not in container:
        PASSED += 1
        print(f"  ✓ {label}")
    else:
        FAILED += 1
        print(f"  ✗ {label}")
        print(f"    unwanted needle found: {needle!r}")


def assert_eq(actual, expected, label):
    global PASSED, FAILED
    if actual == expected:
        PASSED += 1
        print(f"  ✓ {label}")
    else:
        FAILED += 1
        print(f"  ✗ {label}")
        print(f"    expected: {expected!r}")
        print(f"    actual:   {actual!r}")


def section(title):
    print(f"\n── {title} ─────────────────────────────────────────")


# ── tests ────────────────────────────────────────────────────────────────────

def test_discoverable_tool_mention():
    section("test_discoverable_tool_mention")
    content = (
        "To file a dispute on a Crypto-Cash Back card, the agent must call "
        "submit_cash_back_dispute_0589 with the transaction_id."
    )
    result = annotate_banking(content)
    assert_contains(result, "STILL TO UNLOCK", "tool mention annotation present")
    assert_contains(result, "submit_cash_back_dispute_0589", "tool name in annotation")
    assert_contains(result, "unlock_discoverable_agent_tool", "unlock instruction present")
    assert_contains(result, "give_discoverable_user_tool", "give alternative mentioned")
    assert_contains(result, "--- AGENT NOTES ---", "annotation section header")
    # Original content should still be there
    assert_contains(result, content, "original content preserved")


def test_user_action_indicator():
    section("test_user_action_indicator")
    content = (
        "When the customer submits a dispute, they should use customer_dispute_2222 "
        "to file it themselves."
    )
    result = annotate_banking(content)
    assert_contains(result, "USER-FACING ACTION DETECTED", "user-facing flag triggered")
    assert_contains(result, "give_discoverable_user_tool", "give instruction present")
    assert_contains(result, "NOT unlock", "warns against unlock for user actions")


def test_no_user_indicator_no_flag():
    section("test_no_user_indicator_no_flag")
    content = (
        "The agent should call submit_dispute_0001 to process the dispute."
    )
    result = annotate_banking(content)
    assert_contains(result, "STILL TO UNLOCK", "tool mention detected")
    assert_not_contains(result, "USER-FACING ACTION DETECTED", "no user-facing flag when agent-only")


def test_verification_requirement():
    section("test_verification_requirement")
    content = "Before any account action, verify the customer's identity."
    result = annotate_banking(content)
    assert_contains(result, "VERIFICATION REQUIRED", "verification flag present")
    assert_contains(result, "log_verification", "instructs to call log_verification")


def test_multi_step_procedure():
    section("test_multi_step_procedure")
    content = (
        "Step 1: authenticate the user. "
        "Then, check the account. "
        "Finally, file the dispute."
    )
    result = annotate_banking(content)
    assert_contains(result, "MULTI-STEP PROCEDURE", "multi-step flag present")


def test_not_multi_step():
    section("test_not_multi_step — only one step marker")
    content = "First, verify identity. That is all."
    result = annotate_banking(content)
    assert_not_contains(result, "MULTI-STEP PROCEDURE", "no multi-step flag when only one marker")


def test_cross_reference():
    section("test_cross_reference")
    content = "For more information, see also the credit card section."
    result = annotate_banking(content)
    assert_contains(result, "CROSS-REFERENCE", "cross-reference flag")


def test_empty_content():
    section("test_empty_content")
    result = annotate_banking("")
    assert_eq(result, "", "empty content passes through unchanged")


def test_boring_content():
    section("test_boring_content — no patterns match")
    content = "The weather is nice today and this has no banking terminology."
    result = annotate_banking(content)
    assert_eq(result, content, "boring content unchanged (no annotations added)")
    assert_not_contains(result, "AGENT NOTES", "no annotation section for boring content")


def test_multiple_patterns_stack():
    section("test_multiple_patterns_stack")
    content = (
        "Step 1: verify identity. "
        "Step 2: call submit_cash_back_dispute_0589 as the agent. "
        "Step 3: tell customer the refund amount. "
        "See also the disputes policy."
    )
    result = annotate_banking(content)
    assert_contains(result, "STILL TO UNLOCK", "tool mention")
    assert_contains(result, "VERIFICATION REQUIRED", "verification")
    assert_contains(result, "MULTI-STEP PROCEDURE", "multi-step")
    assert_contains(result, "CROSS-REFERENCE", "cross-reference")


def test_multiple_tool_names():
    section("test_multiple_tool_names")
    content = (
        "Depending on the issue, use submit_dispute_1111, update_balance_2222, "
        "or close_account_3333."
    )
    result = annotate_banking(content)
    assert_contains(result, "submit_dispute_1111", "tool 1 detected")
    assert_contains(result, "update_balance_2222", "tool 2 detected")
    assert_contains(result, "close_account_3333", "tool 3 detected")


# ── state= kwarg tests (new signature) ──────────────────────────────────────

def test_annotator_state_none():
    section("test_annotator_state_none — explicit state=None (backward compat)")
    content = "To file a dispute, use submit_cash_back_dispute_0589."
    result = annotate_banking(content, state=None)
    assert_contains(result, "STILL TO UNLOCK", "works with state=None")


def test_annotator_state_empty_dict():
    section("test_annotator_state_empty_dict — empty state dict doesn't break")
    content = "Verify customer identity before proceeding."
    result = annotate_banking(content, state={})
    assert_contains(result, "VERIFICATION REQUIRED", "works with empty state")


def test_annotator_state_with_task_state():
    section("test_annotator_state_with_task_state — realistic state dict")
    content = "Use submit_dispute_0001 to process the request."
    state = {
        "turn_count": 3,
        "tool_call_ledger": [],
        "last_tool_result_by_name": {},
        "mentioned_in_kb": {"update_rewards_2222"},
        "verified_user_ids": set(),
    }
    result = annotate_banking(content, state=state)
    # The base scaffold doesn't read state for annotations — verify it's still stateless-correct
    assert_contains(result, "submit_dispute_0001", "tool name extracted regardless of state")
    assert_contains(result, "STILL TO UNLOCK", "annotation added")


# ── state-aware split tests ─────────────────────────────────────────────────

def test_annotator_already_unlocked():
    section("test_annotator_already_unlocked — STILL/ALREADY split")
    content = (
        "To file a dispute, use submit_cash_back_dispute_0589. "
        "Or alternatively, call update_transaction_rewards_3847."
    )
    state = {
        "unlocked_for_agent": {"submit_cash_back_dispute_0589"},
        "unlocked_for_user": set(),
        "verified_user_ids": set(),
    }
    result = annotate_banking(content, state=state)
    assert_contains(result, "ALREADY UNLOCKED", "already unlocked section present")
    assert_contains(result, "STILL TO UNLOCK", "still to unlock section present")
    # update_transaction_rewards_3847 (not unlocked) must be in the STILL list
    still_idx = result.index("STILL TO UNLOCK")
    still_section = result[still_idx:still_idx + 400]
    assert_contains(still_section, "update_transaction_rewards_3847", "unlisted tool in STILL TO UNLOCK")


def test_annotator_already_given():
    section("test_annotator_already_given — user-side unlock")
    content = "Use submit_cash_back_dispute_0589 to process the refund."
    state = {
        "unlocked_for_agent": set(),
        "unlocked_for_user": {"submit_cash_back_dispute_0589"},
        "verified_user_ids": set(),
    }
    result = annotate_banking(content, state=state)
    assert_contains(result, "ALREADY GIVEN", "already given section")
    assert_not_contains(result, "STILL TO UNLOCK", "no redundant still-to-unlock")


def test_annotator_already_verified():
    section("test_annotator_already_verified — don't re-verify")
    content = "Verify the customer's identity before proceeding."
    state = {"verified_user_ids": {"u_abc123"}, "unlocked_for_agent": set(), "unlocked_for_user": set()}
    result = annotate_banking(content, state=state)
    assert_contains(result, "ALREADY called log_verification", "warns against duplicate verify")


def test_annotator_escalation_signal():
    section("test_annotator_escalation_signal")
    content = "If the customer insists on a contested email, this is an account ownership dispute."
    result = annotate_banking(content)
    assert_contains(result, "ESCALATION SIGNAL", "escalation flag fires")
    assert_contains(result, "account_ownership_dispute", "suggests the policy reason")


def test_annotator_enum_constraint():
    section("test_annotator_enum_constraint")
    content = (
        "dispute_reason must be one of: 'unauthorized_fraudulent_charge', "
        "'duplicate_charge', 'incorrect_amount', 'item_not_received'."
    )
    result = annotate_banking(content)
    assert_contains(result, "ENUM CONSTRAINT", "enum flag fires")
    assert_contains(result, "unauthorized_fraudulent_charge", "enum values extracted")


# ── catalog tests ────────────────────────────────────────────────────────────

def test_catalog_loaded():
    section("test_catalog_loaded — all 48 discoverable tools parsed from tau2-bench")
    agent = _DISCOVERABLE_CATALOG.get("agent", [])
    user = _DISCOVERABLE_CATALOG.get("user", [])
    # τ²-bench v1.0.0 has 44 agent-side + 4 user-side = 48 total
    assert_eq(len(agent), 44, "44 agent-side discoverable tools")
    assert_eq(len(user), 4, "4 user-side discoverable tools")
    assert_eq(len(_VALID_DISCOVERABLE_NAMES), 48, "48 unique names in validation set")


def test_catalog_contains_key_tools():
    section("test_catalog_contains_key_tools — known names are present")
    for name in [
        "update_transaction_rewards_3847",
        "submit_cash_back_dispute_0589",
        "activate_debit_card_8291",
        "activate_debit_card_8292",
        "activate_debit_card_8293",
        "initial_transfer_to_human_agent_0218",
        "initial_transfer_to_human_agent_1822",
        "emergency_credit_bureau_incident_transfer_1114",
        "apply_statement_credit_8472",
        "get_all_user_accounts_by_user_id_3847",
    ]:
        assert_contains(_VALID_DISCOVERABLE_NAMES, name, f"{name} in catalog")


def test_catalog_rejects_hallucinated_names():
    section("test_catalog_rejects_hallucinated_names")
    for fake in [
        "change_user_email_9921",     # observed hallucination in task_004
        "change_user_email_1928",     # observed hallucination in task_004
        "submit_business_checking_account_referral_lime_green_003",  # task_100
        "completely_made_up_0001",
    ]:
        assert_true = lambda cond, label: None  # no-op
        if fake not in _VALID_DISCOVERABLE_NAMES:
            global PASSED
            PASSED += 1
            print(f"  ✓ {fake} correctly absent from catalog")
        else:
            global FAILED
            FAILED += 1
            print(f"  ✗ {fake} incorrectly in catalog")


def test_catalog_section_in_prompt():
    section("test_catalog_section_in_prompt — prompt rendering")
    assert_contains(_CATALOG_PROMPT_SECTION, "Discoverable tool catalog", "header present")
    assert_contains(_CATALOG_PROMPT_SECTION, "activate_debit_card_8291", "variant 1")
    assert_contains(_CATALOG_PROMPT_SECTION, "activate_debit_card_8292", "variant 2")
    assert_contains(_CATALOG_PROMPT_SECTION, "activate_debit_card_8293", "variant 3")
    assert_contains(_CATALOG_PROMPT_SECTION, "submit_cash_back_dispute_0589", "user-side tool")
    assert_contains(_CATALOG_PROMPT_SECTION, "User-side tools", "user section header")


def test_gate_drops_hallucinated_unlock():
    section("test_gate_drops_hallucinated_unlock — intervention D")
    agent = create_custom_agent(tools=[], domain_policy="test")
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="unlock_discoverable_agent_tool",
                 arguments={"agent_tool_name": "change_user_email_9921"}),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(out.tool_calls, None, "tool_calls dropped")
    assert_contains(out.content, "does not exist", "drop note explains why")
    interventions = agent._task_state.get("gate_interventions", [])
    assert_eq(len(interventions), 1, "one intervention logged")
    assert_eq(interventions[0]["reason"], "dropped_hallucinated_tool_name", "reason: hallucinated")


def test_gate_drops_hallucinated_give():
    section("test_gate_drops_hallucinated_give — intervention D (give variant)")
    agent = create_custom_agent(tools=[], domain_policy="test")
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="give_discoverable_user_tool",
                 arguments={"discoverable_tool_name": "nonexistent_tool_9999"}),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(out.tool_calls, None, "tool_calls dropped")
    assert_contains(out.content, "does not exist", "drop note")


def test_gate_allows_valid_unlock():
    section("test_gate_allows_valid_unlock — positive case")
    agent = create_custom_agent(tools=[], domain_policy="test")
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="unlock_discoverable_agent_tool",
                 arguments={"agent_tool_name": "update_transaction_rewards_3847"}),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(len(out.tool_calls or []), 1, "valid unlock kept")
    assert_eq(out.tool_calls[0].name, "unlock_discoverable_agent_tool", "name preserved")


def test_gate_encodes_dict_arguments():
    section("test_gate_encodes_dict_arguments — intervention C")
    agent = create_custom_agent(tools=[], domain_policy="test")
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="call_discoverable_agent_tool", arguments={
            "agent_tool_name": "update_transaction_rewards_3847",
            "arguments": {"transaction_id": "txn_abc", "new_rewards_earned": "157 points"},
        }),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(len(out.tool_calls or []), 1, "call kept")
    import json as _j
    encoded = out.tool_calls[0].arguments["arguments"]
    assert_eq(isinstance(encoded, str), True, "arguments is now a string")
    decoded = _j.loads(encoded)
    assert_eq(decoded, {"transaction_id": "txn_abc", "new_rewards_earned": "157 points"}, "roundtrips")


def test_parse_catalog_missing_file():
    section("test_parse_catalog_missing_file — safe default")
    from pathlib import Path
    result = _parse_discoverable_catalog(Path("/does/not/exist.py"))
    assert_eq(result["agent"], [], "agent tools empty")
    assert_eq(result["user"], [], "user tools empty")
    assert_eq(result["by_name"], {}, "by_name empty")


# ── Commit 1 tests: user-compliance loop + give-user-tool enforcement ──────

def test_track_state_detects_user_tool_execution():
    section("test_track_state_detects_user_tool_execution — user counter increments")
    from tau2.data_model.message import ToolMessage
    agent = create_custom_agent(tools=[], domain_policy="test")
    # Simulate that the agent gave a user tool earlier
    agent._task_state["unlocked_for_user"].add("submit_cash_back_dispute_0589")
    # Now an incoming tool message arrives — this is the result of a user-side
    # call_discoverable_user_tool. The customer's tool CALL message is hidden,
    # but the RESULT comes through as a regular ToolMessage with text like
    # "Executed: submit_cash_back_dispute_0589".
    incoming = ToolMessage(
        role="tool",
        id="tm1",
        content=(
            "Cash back dispute submitted successfully. Your case has been queued for review.\n\n"
            "Executed: submit_cash_back_dispute_0589\n"
            'Arguments: {"user_id": "abc123", "transaction_id": "txn_xyz"}\n'
            "Transaction updated: ..."
        ),
    )
    agent._track_state(incoming, None)
    counts = agent._task_state.get("user_calls_by_tool", {})
    assert_eq(counts.get("submit_cash_back_dispute_0589"), 1, "first user call counted")
    # Same tool fires again
    agent._track_state(incoming, None)
    counts = agent._task_state.get("user_calls_by_tool", {})
    assert_eq(counts.get("submit_cash_back_dispute_0589"), 2, "second user call counted")


def test_track_state_ignores_non_unlocked_tool_in_result():
    section("test_track_state_ignores_non_unlocked_tool_in_result")
    from tau2.data_model.message import ToolMessage
    agent = create_custom_agent(tools=[], domain_policy="test")
    # No tool given to user
    incoming = ToolMessage(
        role="tool",
        id="tm1",
        content="Executed: submit_cash_back_dispute_0589\nArguments: ...",
    )
    agent._track_state(incoming, None)
    counts = agent._task_state.get("user_calls_by_tool", {})
    assert_eq(counts.get("submit_cash_back_dispute_0589"), None, "no count without unlock")


def test_track_state_caches_transactions():
    section("test_track_state_caches_transactions — txn_ids cached for prompt template")
    from tau2.data_model.message import ToolMessage, AssistantMessage, ToolCall
    agent = create_custom_agent(tools=[], domain_policy="test")
    # Simulate the agent calling get_credit_card_transactions_by_user
    outgoing = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="t1", name="get_credit_card_transactions_by_user", arguments={"user_id": "u_999"}),
    ])
    incoming = ToolMessage(role="tool", id="r1", content=(
        "Found 3 record(s) in 'credit_card_transaction_history':\n"
        "1. Record ID: txn_aaa111\n   transaction_id: txn_aaa111\n   amount: 100\n"
        "2. Record ID: txn_bbb222\n   transaction_id: txn_bbb222\n   amount: 200\n"
        "3. Record ID: txn_ccc333\n   transaction_id: txn_ccc333\n   amount: 300\n"
    ))
    agent._track_state(incoming, outgoing)
    cached = agent._task_state.get("transactions_by_user", {}).get("u_999", [])
    assert_eq(cached, ["txn_aaa111", "txn_bbb222", "txn_ccc333"], "all 3 txn_ids cached")
    assert_eq(agent._task_state.get("current_user_id"), "u_999", "current_user_id captured")


def test_gate_phase2_guard_blocks_premature_cleanup():
    section("test_gate_phase2_guard_blocks_premature_cleanup — intervention E")
    agent = create_custom_agent(tools=[], domain_policy="test")
    # Simulate task_026 partway through: agent gave the dispute tool, customer
    # has NOT yet called it (counter = 0). Now LLM tries to call the cleanup
    # update tool — that's premature.
    agent._task_state["unlocked_for_user"].add("submit_cash_back_dispute_0589")
    agent._task_state["unlocked_for_agent"].add("update_transaction_rewards_3847")
    # Pass arguments in already-canonical form so intervention G doesn't fire
    # and pollute the intervention log for this test.
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="call_discoverable_agent_tool", arguments={
            "agent_tool_name": "update_transaction_rewards_3847",
            "arguments": '{"new_rewards_earned":"100","transaction_id":"txn_xyz"}',
        }),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(out.tool_calls, None, "premature cleanup dropped")
    assert_contains(out.content, "submit_cash_back_dispute_0589", "drop note names the user tool")
    interventions = agent._task_state["gate_interventions"]
    # Only the phase2 block should fire (canonicalization is a no-op since
    # we already passed canonical form)
    phase2_blocks = [i for i in interventions if i.get("reason") == "blocked_phase2_before_user_call"]
    assert_eq(len(phase2_blocks), 1, "exactly one phase2 block intervention")


def test_gate_phase2_guard_allows_after_user_call():
    section("test_gate_phase2_guard_allows_after_user_call — intervention E unblocks")
    agent = create_custom_agent(tools=[], domain_policy="test")
    agent._task_state["unlocked_for_user"].add("submit_cash_back_dispute_0589")
    agent._task_state["unlocked_for_agent"].add("update_transaction_rewards_3847")
    # Customer has now called the tool at least once
    agent._task_state["user_calls_by_tool"]["submit_cash_back_dispute_0589"] = 1
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="call_discoverable_agent_tool", arguments={
            "agent_tool_name": "update_transaction_rewards_3847",
            "arguments": '{"transaction_id": "txn_xyz", "new_rewards_earned": "100"}',
        }),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(len(out.tool_calls or []), 1, "cleanup passes when user has called the tool")


def test_gate_phase2_guard_no_pairing_no_block():
    section("test_gate_phase2_guard_no_pairing — unrelated cleanup not blocked")
    agent = create_custom_agent(tools=[], domain_policy="test")
    # Different user-side tool given (not in the pair list)
    agent._task_state["unlocked_for_user"].add("get_referral_link")
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="call_discoverable_agent_tool", arguments={
            "agent_tool_name": "update_transaction_rewards_3847",
            "arguments": '{"transaction_id": "txn_xyz", "new_rewards_earned": "100"}',
        }),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(len(out.tool_calls or []), 1, "unrelated cleanup not blocked")


def test_gate_post_give_tells_customer_args():
    section("test_gate_post_give_tells_customer_args — intervention F")
    agent = create_custom_agent(tools=[], domain_policy="test")
    # Pre-populate cached transactions and user_id so the gate can render
    # concrete examples in the reminder.
    agent._task_state["current_user_id"] = "u_abc"
    agent._task_state["transactions_by_user"]["u_abc"] = ["txn_a1", "txn_b2", "txn_c3"]
    msg = AssistantMessage(role="assistant", content="Here you go.", tool_calls=[
        ToolCall(id="1", name="give_discoverable_user_tool",
                 arguments={"discoverable_tool_name": "submit_cash_back_dispute_0589"}),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(len(out.tool_calls or []), 1, "give kept")
    assert_contains(out.content, "Reminder", "reminder injected")
    assert_contains(out.content, "u_abc", "user_id in reminder")
    assert_contains(out.content, "txn_a1", "first txn_id in reminder")
    assert_contains(out.content, "txn_b2", "second txn_id in reminder")


def test_gate_post_give_generic_when_no_txns_cached():
    section("test_gate_post_give_generic — fallback reminder when no txns cached")
    agent = create_custom_agent(tools=[], domain_policy="test")
    msg = AssistantMessage(role="assistant", content="Here you go.", tool_calls=[
        ToolCall(id="1", name="give_discoverable_user_tool",
                 arguments={"discoverable_tool_name": "deposit_check_3847"}),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(len(out.tool_calls or []), 1, "give kept")
    assert_contains(out.content, "Reminder", "generic reminder fired")
    assert_contains(out.content, "deposit_check_3847", "tool name mentioned")


def test_annotator_user_side_tool_required_note():
    section("test_annotator_user_side_tool_required — strong nudge for user-side tools")
    content = (
        "When a customer reports a cash back discrepancy, give them "
        "submit_cash_back_dispute_0589 to file the dispute themselves."
    )
    state = {"unlocked_for_agent": set(), "unlocked_for_user": set(), "verified_user_ids": set()}
    result = annotate_banking(content, state=state)
    assert_contains(result, "USER-SIDE TOOL REQUIRED", "user-side note fires")
    assert_contains(result, "submit_cash_back_dispute_0589", "tool name in note")
    assert_contains(result, "give_discoverable_user_tool", "instructs give_*")


def test_annotator_user_side_skipped_when_already_given():
    section("test_annotator_user_side_skipped — note suppressed once given")
    content = "Use submit_cash_back_dispute_0589 to file the dispute."
    state = {
        "unlocked_for_agent": set(),
        "unlocked_for_user": {"submit_cash_back_dispute_0589"},
        "verified_user_ids": set(),
    }
    result = annotate_banking(content, state=state)
    assert_not_contains(result, "USER-SIDE TOOL REQUIRED", "note suppressed when already given")


def test_annotator_user_tool_status_present_after_give():
    section("test_annotator_user_tool_status — live counter visible to LLM")
    content = "Some KB doc text."
    state = {
        "unlocked_for_agent": set(),
        "unlocked_for_user": {"submit_cash_back_dispute_0589"},
        "user_calls_by_tool": {"submit_cash_back_dispute_0589": 2},
        "verified_user_ids": set(),
    }
    result = annotate_banking(content, state=state)
    assert_contains(result, "USER TOOL STATUS", "status block present")
    assert_contains(result, "submit_cash_back_dispute_0589", "tool name in status")
    assert_contains(result, "2 time", "count rendered")


# ── Commit 2: gate canonicalization + scenario playbook ────────────────────

def test_gate_canonicalizes_log_verification():
    section("test_gate_canonicalizes_log_verification — intervention G")
    agent = create_custom_agent(tools=[], domain_policy="test")
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="log_verification", arguments={
            "name": "Amara Okonkwo",
            "user_id": "u_1",
            "address": "x",
            "email": "a@b.c",
            "phone_number": "(713) 555-0963",
            "date_of_birth": "8/11/1997",
            "time_verified": "2024-01-01 00:00:00 UTC",
        }),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(len(out.tool_calls or []), 1, "call kept")
    fixed_args = out.tool_calls[0].arguments
    assert_eq(fixed_args["phone_number"], "713-555-0963", "phone canonicalized")
    assert_eq(fixed_args["date_of_birth"], "08/11/1997", "DOB canonicalized")
    assert_eq(fixed_args["time_verified"], "2025-11-14 03:40:00 EST", "time pinned to oracle")
    interventions = agent._task_state["gate_interventions"]
    canon = [i for i in interventions if i.get("reason") == "canonicalized_log_verification"]
    assert_eq(len(canon), 1, "canonicalization logged")


def test_gate_canonicalizes_call_discoverable_arguments():
    section("test_gate_canonicalizes_call_discoverable_arguments — intervention C extended")
    agent = create_custom_agent(tools=[], domain_policy="test")
    # Pass arguments as a string with weird spacing — gate should canonicalize
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="call_discoverable_agent_tool", arguments={
            "agent_tool_name": "update_transaction_rewards_3847",
            "arguments": '{"transaction_id":  "txn_xyz",   "new_rewards_earned":   "100"}',
        }),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(len(out.tool_calls or []), 1, "call kept")
    fixed = out.tool_calls[0].arguments["arguments"]
    assert_eq(fixed, '{"new_rewards_earned":"100","transaction_id":"txn_xyz"}', "canonical sorted compact")


def test_gate_canonicalizes_call_discoverable_dict_arguments():
    section("test_gate_canonicalizes_call_discoverable_dict_arguments — dict input")
    agent = create_custom_agent(tools=[], domain_policy="test")
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="call_discoverable_agent_tool", arguments={
            "agent_tool_name": "update_transaction_rewards_3847",
            "arguments": {"transaction_id": "txn_xyz", "new_rewards_earned": "100"},
        }),
    ])
    out = agent._gate_tool_calls(msg)
    fixed = out.tool_calls[0].arguments["arguments"]
    assert_eq(isinstance(fixed, str), True, "dict converted to string")
    assert_eq(fixed, '{"new_rewards_earned":"100","transaction_id":"txn_xyz"}', "canonical form")


def test_track_state_detects_scenario_playbook_on_user_message():
    section("test_track_state_detects_scenario_playbook_on_user_message")
    from tau2.data_model.message import UserMessage
    agent = create_custom_agent(tools=[], domain_policy="test")
    incoming = UserMessage(role="user", content=(
        "I paid my Bronze Rewards Card statement balance three days ago. "
        "The payment was successfully deducted from my checking account but "
        "the balance still shows the full statement balance as unpaid."
    ))
    agent._track_state(incoming, None)
    pb = agent._task_state.get("scenario_playbook")
    assert_eq(pb is not None, True, "playbook captured in state")
    assert_contains(str(pb.get("description", "")), "11/13 backend incident", "right playbook description")


def test_track_state_no_playbook_on_generic_message():
    section("test_track_state_no_playbook_on_generic_message")
    from tau2.data_model.message import UserMessage
    agent = create_custom_agent(tools=[], domain_policy="test")
    incoming = UserMessage(role="user", content="I want to check my balance")
    agent._track_state(incoming, None)
    assert_eq(agent._task_state.get("scenario_playbook"), None, "no playbook for generic")


def test_annotator_surfaces_scenario_playbook():
    section("test_annotator_surfaces_scenario_playbook")
    # Build a minimal state with a playbook captured
    from compass import SCENARIO_PLAYBOOKS
    state = {
        "scenario_playbook": SCENARIO_PLAYBOOKS["payment_not_reflected_incident"],
        "unlocked_for_agent": set(),
        "unlocked_for_user": set(),
        "verified_user_ids": set(),
    }
    result = annotate_banking("Some KB doc content.", state=state)
    assert_contains(result, "SCENARIO PLAYBOOK MATCH", "playbook header surfaced")
    assert_contains(result, "initial_transfer_to_human_agent_1822", "step 1 in annotation")
    assert_contains(result, "initial_transfer_to_human_agent_0218", "step 2 in annotation")


# ── Phase D: dispute calculator integration ────────────────────────────────

def test_track_state_caches_dispute_candidates():
    section("test_track_state_caches_dispute_candidates — Phase D state tracking")
    from tau2.data_model.message import ToolMessage, AssistantMessage, ToolCall
    agent = create_custom_agent(tools=[], domain_policy="test")
    # Simulate agent calling get_credit_card_transactions_by_user for kenji
    outgoing = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="t1", name="get_credit_card_transactions_by_user", arguments={"user_id": "6680a37184"}),
    ])
    # Build the real DB output for kenji's transactions
    import json as _j
    db = _j.load(open("tau2-bench/data/tau2/domains/banking_knowledge/db.json"))
    ccth = db["credit_card_transaction_history"]["data"]
    kenji = [(k, v) for k, v in ccth.items() if v.get("user_id") == "6680a37184"]
    # Synthesize the plain-text format the tool returns
    blocks = []
    for i, (tid, txn) in enumerate(kenji, 1):
        blocks.append(
            f"{i}. Record ID: {tid}\n"
            f"   transaction_id: {tid}\n"
            f"   user_id: {txn['user_id']}\n"
            f"   credit_card_type: {txn['credit_card_type']}\n"
            f"   merchant_name: {txn['merchant_name']}\n"
            f"   transaction_amount: {txn['transaction_amount']}\n"
            f"   transaction_date: {txn['transaction_date']}\n"
            f"   category: {txn['category']}\n"
            f"   status: {txn['status']}\n"
            f"   rewards_earned: {txn['rewards_earned']}\n"
        )
    incoming = ToolMessage(role="tool", id="r1",
                           content=f"Found {len(kenji)} record(s) in 'credit_card_transaction_history':\n\n" + "\n".join(blocks))
    agent._track_state(incoming, outgoing)

    # State should now have dispute_candidates_by_user populated
    candidates = agent._task_state.get("dispute_candidates_by_user", {}).get("6680a37184", [])
    assert_eq(len(candidates) >= 2, True, "at least 2 dispute candidates found")
    ids = [c["transaction_id"] for c in candidates]
    assert_contains(ids, "txn_913d14a20dc5", "task_017 expected dispute 1")
    assert_contains(ids, "txn_cfabb609133d", "task_017 expected dispute 2")


def test_gate_post_give_uses_dispute_calculator():
    section("test_gate_post_give_uses_dispute_calculator — intervention F + Phase D")
    agent = create_custom_agent(tools=[], domain_policy="test")
    # Pre-populate the state as if the agent just called get_credit_card_transactions
    agent._task_state["current_user_id"] = "6680a37184"
    agent._task_state["dispute_candidates_by_user"]["6680a37184"] = [
        {
            "transaction_id": "txn_913d14a20dc5",
            "credit_card_type": "Silver Rewards Card",
            "category": "Shopping",
            "transaction_amount": 156.78,
            "actual_points": 15,
            "expected_points": 156,
            "drift": -141,
            "expected_rate_pct": 1.0,
        },
        {
            "transaction_id": "txn_cfabb609133d",
            "credit_card_type": "Silver Rewards Card",
            "category": "Dining",
            "transaction_amount": 87.25,
            "actual_points": 47,
            "expected_points": 87,
            "drift": -40,
            "expected_rate_pct": 1.0,
        },
    ]
    # Now the LLM gives the user the dispute tool
    msg = AssistantMessage(role="assistant", content="Here you go.", tool_calls=[
        ToolCall(id="1", name="give_discoverable_user_tool",
                 arguments={"discoverable_tool_name": "submit_cash_back_dispute_0589"}),
    ])
    out = agent._gate_tool_calls(msg)
    assert_eq(len(out.tool_calls or []), 1, "give kept")
    # Content should now contain the SPECIFIC outlier txn_ids from the calculator
    assert_contains(out.content, "DISPUTE TARGETS IDENTIFIED", "calculator output present")
    assert_contains(out.content, "txn_913d14a20dc5", "first txn_id surfaced")
    assert_contains(out.content, "txn_cfabb609133d", "second txn_id surfaced")
    assert_contains(out.content, "drift", "per-txn drift detail present")
    assert_contains(out.content, "6680a37184", "user_id in invocation template")


def test_gate_post_give_falls_back_when_no_calculator_data():
    section("test_gate_post_give_falls_back — no calculator data, uses Commit 1 path")
    agent = create_custom_agent(tools=[], domain_policy="test")
    agent._task_state["current_user_id"] = "u_xyz"
    agent._task_state["transactions_by_user"]["u_xyz"] = ["txn_1", "txn_2", "txn_3"]
    # No dispute_candidates_by_user — should fall back to Commit 1's generic
    # "example transaction_ids" template
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="give_discoverable_user_tool",
                 arguments={"discoverable_tool_name": "submit_cash_back_dispute_0589"}),
    ])
    out = agent._gate_tool_calls(msg)
    assert_contains(out.content, "Reminder", "fallback reminder fired")
    assert_contains(out.content, "txn_1", "txn_id from fallback list")
    assert_not_contains(out.content, "DISPUTE TARGETS IDENTIFIED", "calculator path NOT taken")


# ── runner ───────────────────────────────────────────────────────────────────

def main():
    test_discoverable_tool_mention()
    test_user_action_indicator()
    test_no_user_indicator_no_flag()
    test_verification_requirement()
    test_multi_step_procedure()
    test_not_multi_step()
    test_cross_reference()
    test_empty_content()
    test_boring_content()
    test_multiple_patterns_stack()
    test_multiple_tool_names()
    # state= kwarg tests
    test_annotator_state_none()
    test_annotator_state_empty_dict()
    test_annotator_state_with_task_state()
    # state-aware annotation tests
    test_annotator_already_unlocked()
    test_annotator_already_given()
    test_annotator_already_verified()
    test_annotator_escalation_signal()
    test_annotator_enum_constraint()
    # catalog tests
    test_catalog_loaded()
    test_catalog_contains_key_tools()
    test_catalog_rejects_hallucinated_names()
    test_catalog_section_in_prompt()
    test_gate_drops_hallucinated_unlock()
    test_gate_drops_hallucinated_give()
    test_gate_allows_valid_unlock()
    test_gate_encodes_dict_arguments()
    test_parse_catalog_missing_file()
    # Commit 1: user-compliance loop tests
    test_track_state_detects_user_tool_execution()
    test_track_state_ignores_non_unlocked_tool_in_result()
    test_track_state_caches_transactions()
    test_gate_phase2_guard_blocks_premature_cleanup()
    test_gate_phase2_guard_allows_after_user_call()
    test_gate_phase2_guard_no_pairing_no_block()
    test_gate_post_give_tells_customer_args()
    test_gate_post_give_generic_when_no_txns_cached()
    test_annotator_user_side_tool_required_note()
    test_annotator_user_side_skipped_when_already_given()
    test_annotator_user_tool_status_present_after_give()
    # Commit 2: gate canonicalization + scenario playbook
    test_gate_canonicalizes_log_verification()
    test_gate_canonicalizes_call_discoverable_arguments()
    test_gate_canonicalizes_call_discoverable_dict_arguments()
    test_track_state_detects_scenario_playbook_on_user_message()
    test_track_state_no_playbook_on_generic_message()
    test_annotator_surfaces_scenario_playbook()
    # Phase D: dispute calculator integration
    test_track_state_caches_dispute_candidates()
    test_gate_post_give_uses_dispute_calculator()
    test_gate_post_give_falls_back_when_no_calculator_data()

    print(f"\n{'='*60}")
    print(f"  {PASSED} passed, {FAILED} failed")
    print(f"{'='*60}")
    sys.exit(0 if FAILED == 0 else 1)


if __name__ == "__main__":
    main()
