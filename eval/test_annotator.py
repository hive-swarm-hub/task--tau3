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
    from agent import annotate_banking
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

    print(f"\n{'='*60}")
    print(f"  {PASSED} passed, {FAILED} failed")
    print(f"{'='*60}")
    sys.exit(0 if FAILED == 0 else 1)


if __name__ == "__main__":
    main()
