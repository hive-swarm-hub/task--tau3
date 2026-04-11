"""Tests for compass.py — the shared Tool Compass library.

Standalone tests for the compass module. Covers:
- Catalog parsing (all 48 tools present)
- Hallucination validation + fuzzy suggestion
- Variant family detection + disambiguation hints
- Scenario-based tool suggestion
- Enum constraint extraction
- Procedure doc cross-reference
- Canonical query generation
- Graceful degradation on missing source

Run:
    bash prepare.sh  # clones tau2-bench
    python eval/test_compass.py
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from compass import (
        COMPASS,
        ToolCompass,
        get_catalog,
        validate_tool_name,
        suggest_tools,
        render_prompt_section,
        canonicalize_log_verification_args,
        canonicalize_json_args,
        SCENARIO_PLAYBOOKS,
        match_scenario_playbook,
        render_playbook_for_prompt,
    )
except ImportError as e:
    print(f"ERROR: cannot import compass: {e}")
    sys.exit(1)


PASSED = 0
FAILED = 0


def assert_eq(actual, expected, label):
    global PASSED, FAILED
    if actual == expected:
        PASSED += 1
        print(f"  ✓ {label}")
    else:
        FAILED += 1
        print(f"  ✗ {label}: expected {expected!r}, got {actual!r}")


def assert_in(needle, container, label):
    global PASSED, FAILED
    if needle in container:
        PASSED += 1
        print(f"  ✓ {label}")
    else:
        FAILED += 1
        print(f"  ✗ {label}: {needle!r} not found")


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


# ── catalog tests ────────────────────────────────────────────────────────────

def test_catalog_counts():
    section("catalog counts")
    assert_eq(len(COMPASS.agent_tools), 44, "44 agent-side tools")
    assert_eq(len(COMPASS.user_tools), 4, "4 user-side tools")
    assert_eq(len(COMPASS.valid_names), 48, "48 valid names total")


def test_catalog_has_known_tools():
    section("catalog contains known tools")
    for name in (
        "update_transaction_rewards_3847",
        "submit_cash_back_dispute_0589",
        "activate_debit_card_8291",
        "initial_transfer_to_human_agent_0218",
        "initial_transfer_to_human_agent_1822",
        "emergency_credit_bureau_incident_transfer_1114",
        "get_all_user_accounts_by_user_id_3847",
        "apply_statement_credit_8472",
    ):
        assert_in(name, COMPASS.valid_names, name)


def test_entry_fields():
    section("entry fields complete")
    e = COMPASS.get("update_transaction_rewards_3847")
    assert_true(e is not None, "entry returned")
    assert_eq(e["name"], "update_transaction_rewards_3847", "name")
    assert_eq(e["type"], "WRITE", "type")
    assert_eq(e["side"], "agent", "side=agent")
    assert_true("transaction_id" in e["params"], "transaction_id in params")
    assert_true("new_rewards_earned" in e["params"], "new_rewards_earned in params")
    assert_true(len(e["doc"]) > 50, "docstring non-empty")

    u = COMPASS.get("submit_cash_back_dispute_0589")
    assert_eq(u["side"], "user", "user-side tool marked correctly")


# ── validation tests ────────────────────────────────────────────────────────

def test_validate_known_name():
    section("validate known name")
    ok, reason = COMPASS.validate("update_transaction_rewards_3847")
    assert_eq(ok, True, "known name valid")
    assert_eq(reason, "ok", "reason=ok")


def test_validate_hallucinations():
    section("validate rejects hallucinations")
    for fake in (
        "change_user_email_9921",
        "submit_business_checking_account_referral_lime_green_003",
        "completely_made_up_0001",
    ):
        ok, reason = COMPASS.validate(fake)
        assert_eq(ok, False, f"{fake} rejected")
        assert_in("not in the discoverable tool catalog", reason, f"{fake} reason explains")


def test_validate_fuzzy_suggestion():
    section("validate suggests a near name")
    # update_transaction_rewards_3847 with a typo
    ok, reason = COMPASS.validate("update_transaction_reward_3847")
    assert_eq(ok, False, "typo rejected")
    assert_in("Did you mean", reason, "suggestion present")
    assert_in("update_transaction_rewards_3847", reason, "correct name suggested")


def test_validate_empty():
    section("validate rejects empty")
    ok, _ = COMPASS.validate("")
    assert_eq(ok, False, "empty string rejected")


# ── variant family ──────────────────────────────────────────────────────────

def test_variant_family_activate_debit_card():
    section("variant family: activate_debit_card_*")
    family = COMPASS.variant_family("activate_debit_card_8291")
    names = [e["name"] for e in family]
    assert_eq(len(family), 3, "3 variants")
    assert_in("activate_debit_card_8291", names, "8291")
    assert_in("activate_debit_card_8292", names, "8292")
    assert_in("activate_debit_card_8293", names, "8293")


def test_variant_family_transfer():
    section("variant family: initial_transfer_to_human_agent_*")
    family = COMPASS.variant_family("initial_transfer_to_human_agent_0218")
    names = [e["name"] for e in family]
    assert_eq(len(family), 2, "2 variants (the trap pair)")
    assert_in("initial_transfer_to_human_agent_0218", names, "0218 (real transfer)")
    assert_in("initial_transfer_to_human_agent_1822", names, "1822 (probe)")


def test_variant_family_solo_tool():
    section("variant family: solo tool returns empty")
    family = COMPASS.variant_family("update_transaction_rewards_3847")
    assert_eq(family, [], "no siblings")


def test_variant_hint():
    section("variant hint extraction")
    hint = COMPASS.variant_hint("activate_debit_card_8291")
    assert_true(hint is not None, "hint present")
    assert_in("new_account", hint, "mentions new_account")

    hint2 = COMPASS.variant_hint("activate_debit_card_8292")
    assert_in("lost", hint2, "mentions lost")


# ── scenario dispatch ───────────────────────────────────────────────────────

def test_scenario_dispute_cash_back():
    section("scenario: dispute cash back reward")
    matches = COMPASS.suggest_tools("I want to dispute a cash back reward on my credit card")
    names = [m["name"] for m in matches]
    # Should surface the dispute tool (user-side) AND the rewards update tool
    assert_in("submit_cash_back_dispute_0589", names, "user-side dispute tool surfaced")


def test_scenario_payment_not_reflecting():
    section("scenario: payment not reflecting")
    matches = COMPASS.suggest_tools("my payment is not reflecting on my credit card statement")
    names = [m["name"] for m in matches]
    # Should surface the trap-tool transfer family
    assert_in("initial_transfer_to_human_agent_0218", names, "trap tool 0218 surfaced")


def test_scenario_empty_input():
    section("scenario: empty input")
    matches = COMPASS.suggest_tools("")
    assert_eq(matches, [], "no matches for empty input")


def test_scenario_gibberish():
    section("scenario: gibberish input")
    matches = COMPASS.suggest_tools("xkcd zzz qqq")
    assert_eq(matches, [], "no matches for gibberish")


# ── procedure docs ──────────────────────────────────────────────────────────

def test_procedure_docs_known_tool():
    section("procedure_docs: known tool")
    docs = COMPASS.procedure_docs("update_transaction_rewards_3847")
    assert_true(len(docs) >= 1, "at least 1 procedure doc")
    titles = [d["title"] for d in docs]
    assert_true(any("Cash Back" in t for t in titles), "title mentions Cash Back")


def test_procedure_docs_unknown_tool():
    section("procedure_docs: unknown tool")
    docs = COMPASS.procedure_docs("nonexistent_0000")
    assert_eq(docs, [], "empty list")


# ── canonical query ─────────────────────────────────────────────────────────

def test_canonical_query_known():
    section("canonical_query: known tool")
    q = COMPASS.canonical_query("submit_cash_back_dispute_0589")
    assert_true(q is not None and len(q) > 0, "query returned")
    assert_in("Cash Back", q, "mentions Cash Back")


def test_canonical_query_unknown_but_valid():
    section("canonical_query: no doc → name-derived fallback")
    # example_agent_tool_0000 exists in catalog but no doc mentions it
    q = COMPASS.canonical_query("example_agent_tool_0000")
    assert_true(q is not None, "fallback query generated")


# ── enum constraints ────────────────────────────────────────────────────────

def test_enum_constraints_apply_statement_credit():
    section("enum_constraints: apply_statement_credit_8472")
    ec = COMPASS.enum_constraints("apply_statement_credit_8472")
    assert_in("reason", ec, "reason param has constraints")
    assert_in("goodwill_adjustment", ec.get("reason", []), "contains goodwill_adjustment")
    # user_id should NOT have enum constraints (it's just a string ID)
    assert_true("user_id" not in ec, "user_id has no enum constraints")


def test_enum_constraints_dispute():
    section("enum_constraints: file_credit_card_transaction_dispute_4829")
    ec = COMPASS.enum_constraints("file_credit_card_transaction_dispute_4829")
    assert_in("dispute_reason", ec, "dispute_reason constrained")
    assert_true(
        "unauthorized_fraudulent_charge" in ec.get("dispute_reason", []),
        "includes unauthorized_fraudulent_charge",
    )
    assert_in("card_action", ec, "card_action constrained")


def test_enum_constraints_none():
    section("enum_constraints: no constraints")
    ec = COMPASS.enum_constraints("get_all_user_accounts_by_user_id_3847")
    assert_eq(ec, {}, "read tool has no enum constraints")


# ── prompt rendering ────────────────────────────────────────────────────────

def test_render_prompt_section():
    section("render_prompt_section")
    s = COMPASS.render_prompt_section()
    assert_true(len(s) > 1000, "non-trivial length")
    assert_in("Discoverable tool catalog", s, "header present")
    assert_in("activate_debit_card_8291", s, "contains variant 1")
    assert_in("activate_debit_card_8292", s, "contains variant 2")
    assert_in("activate_debit_card_8293", s, "contains variant 3")
    assert_in("submit_cash_back_dispute_0589", s, "user tool present")
    assert_in("User-side tools", s, "user section header")


# ── graceful degradation ────────────────────────────────────────────────────

def test_missing_source_path():
    section("missing source path → empty catalog")
    bad = ToolCompass(tools_path=Path("/does/not/exist.py"), docs_dir=Path("/nope"))
    assert_eq(bad.catalog["agent"], [], "agent empty")
    assert_eq(bad.catalog["user"], [], "user empty")
    assert_eq(bad.valid_names, set(), "no valid names")
    ok, _ = bad.validate("anything")
    # With empty catalog, validate rejects everything as absent
    assert_eq(ok, False, "everything rejected")


# ── Commit 2: argument canonicalization ─────────────────────────────────────

def test_canonicalize_lv_time():
    section("canonicalize_log_verification_args — time_verified pinned to oracle")
    out = canonicalize_log_verification_args({"time_verified": "2024-06-15 12:00:00 UTC"})
    assert_eq(out["time_verified"], "2025-11-14 03:40:00 EST", "stale time pinned to oracle")


def test_canonicalize_lv_time_already_canonical():
    section("canonicalize_log_verification_args — already-canonical time untouched")
    out = canonicalize_log_verification_args({"time_verified": "2025-11-14 03:40:00 EST"})
    assert_eq(out["time_verified"], "2025-11-14 03:40:00 EST", "unchanged")


def test_canonicalize_lv_dob_formats():
    section("canonicalize_log_verification_args — DOB normalization")
    cases = [
        ("8/11/1997", "08/11/1997"),
        ("08/11/1997", "08/11/1997"),
        ("1997-08-11", "08/11/1997"),
        ("08-11-1997", "08/11/1997"),
    ]
    for inp, expected in cases:
        out = canonicalize_log_verification_args({"date_of_birth": inp})
        assert_eq(out["date_of_birth"], expected, f"{inp!r} → {expected!r}")


def test_canonicalize_lv_phone_formats():
    section("canonicalize_log_verification_args — phone normalization")
    cases = [
        ("713-555-0963", "713-555-0963"),
        ("(713) 555-0963", "713-555-0963"),
        ("+1 713 555 0963", "713-555-0963"),
        ("17135550963", "713-555-0963"),
        ("7135550963", "713-555-0963"),
    ]
    for inp, expected in cases:
        out = canonicalize_log_verification_args({"phone_number": inp})
        assert_eq(out["phone_number"], expected, f"{inp!r} → {expected!r}")


def test_canonicalize_lv_passes_through_other_fields():
    section("canonicalize_log_verification_args — non-target fields unchanged")
    args = {
        "name": "Amara Okonkwo",
        "user_id": "755bcb4d5d",
        "address": "305 Magnolia Street, Houston, TX 77002",
        "email": "x@y.z",
        "time_verified": "2025-11-14 03:40:00 EST",
        "date_of_birth": "08/11/1997",
        "phone_number": "713-555-0963",
    }
    out = canonicalize_log_verification_args(args)
    for k in ("name", "user_id", "address", "email"):
        assert_eq(out[k], args[k], f"{k} unchanged")


def test_canonicalize_lv_idempotent():
    section("canonicalize_log_verification_args — idempotent")
    args = {
        "time_verified": "2024-06-15 12:00:00 UTC",
        "date_of_birth": "8/11/1997",
        "phone_number": "+1 713 555 0963",
    }
    once = canonicalize_log_verification_args(args)
    twice = canonicalize_log_verification_args(once)
    assert_eq(once, twice, "second pass is a no-op")


def test_canonicalize_json_args_dict():
    section("canonicalize_json_args — dict → sorted compact string")
    s = canonicalize_json_args({"b": 2, "a": 1, "c": [3, 2, 1]})
    assert_eq(s, '{"a":1,"b":2,"c":[3,2,1]}', "sorted keys + compact separators")


def test_canonicalize_json_args_string_canonicalizes_spacing():
    section("canonicalize_json_args — string with stray whitespace canonicalized")
    s = canonicalize_json_args('{"b":  2, "a":  1}')
    assert_eq(s, '{"a":1,"b":2}', "whitespace stripped, keys sorted")


def test_canonicalize_json_args_unparseable_string_passthrough():
    section("canonicalize_json_args — unparseable string returned as-is")
    s = canonicalize_json_args("not json")
    assert_eq(s, "not json", "unchanged when not parseable")


# ── Commit 2: scenario playbooks ────────────────────────────────────────────

def test_scenario_playbook_match_payment_not_reflected():
    section("match_scenario_playbook — task_033 11/13 incident")
    pb = match_scenario_playbook(
        "I paid my Bronze Rewards Card statement balance of $2,847.53 three days ago. "
        "The payment was successfully deducted from my checking account but the balance "
        "still shows the full statement balance as unpaid."
    )
    assert_true(pb is not None, "playbook matched")
    seq = pb.get("required_sequence", [])
    assert_true(len(seq) == 5, "5-step sequence")
    # Order matters
    names = [step[0] for step in seq]
    assert_in("unlock_discoverable_agent_tool", names, "unlock present")
    assert_in("call_discoverable_agent_tool", names, "call present")
    assert_in("transfer_to_human_agents", names, "transfer present")


def test_scenario_playbook_no_match():
    section("match_scenario_playbook — generic message returns None")
    pb = match_scenario_playbook("Hi, I want to change my email address")
    assert_eq(pb, None, "no playbook for unrelated message")


def test_scenario_playbook_match_min_keywords():
    section("match_scenario_playbook — single keyword insufficient (min=2)")
    pb = match_scenario_playbook("My statement is wrong")  # only "statement" matches
    assert_eq(pb, None, "below threshold returns None")


def test_render_playbook_for_prompt():
    section("render_playbook_for_prompt — formatted output")
    pb = SCENARIO_PLAYBOOKS["payment_not_reflected_incident"]
    text = render_playbook_for_prompt(pb)
    assert_in("SCENARIO PLAYBOOK MATCH", text, "header present")
    assert_in("initial_transfer_to_human_agent_1822", text, "step 1 mentioned")
    assert_in("initial_transfer_to_human_agent_0218", text, "step 2 mentioned")
    assert_in("transfer_to_human_agents", text, "final transfer mentioned")
    assert_in("EXACTLY in order", text, "ordering instruction present")


# ── convenience module-level helpers ────────────────────────────────────────

def test_convenience_helpers():
    section("module-level convenience helpers")
    cat = get_catalog()
    assert_true(len(cat["agent"]) == 44, "get_catalog returns 44 agent tools")

    ok, _ = validate_tool_name("update_transaction_rewards_3847")
    assert_eq(ok, True, "validate_tool_name works")

    matches = suggest_tools("cash back dispute")
    assert_true(len(matches) > 0, "suggest_tools returns matches")

    s = render_prompt_section()
    assert_true("Discoverable tool catalog" in s, "render_prompt_section works")


# ── runner ──────────────────────────────────────────────────────────────────

def main():
    test_catalog_counts()
    test_catalog_has_known_tools()
    test_entry_fields()
    test_validate_known_name()
    test_validate_hallucinations()
    test_validate_fuzzy_suggestion()
    test_validate_empty()
    test_variant_family_activate_debit_card()
    test_variant_family_transfer()
    test_variant_family_solo_tool()
    test_variant_hint()
    test_scenario_dispute_cash_back()
    test_scenario_payment_not_reflecting()
    test_scenario_empty_input()
    test_scenario_gibberish()
    test_procedure_docs_known_tool()
    test_procedure_docs_unknown_tool()
    test_canonical_query_known()
    test_canonical_query_unknown_but_valid()
    test_enum_constraints_apply_statement_credit()
    test_enum_constraints_dispute()
    test_enum_constraints_none()
    test_render_prompt_section()
    test_missing_source_path()
    test_convenience_helpers()
    # Commit 2: canonicalization
    test_canonicalize_lv_time()
    test_canonicalize_lv_time_already_canonical()
    test_canonicalize_lv_dob_formats()
    test_canonicalize_lv_phone_formats()
    test_canonicalize_lv_passes_through_other_fields()
    test_canonicalize_lv_idempotent()
    test_canonicalize_json_args_dict()
    test_canonicalize_json_args_string_canonicalizes_spacing()
    test_canonicalize_json_args_unparseable_string_passthrough()
    # Commit 2: scenario playbooks
    test_scenario_playbook_match_payment_not_reflected()
    test_scenario_playbook_no_match()
    test_scenario_playbook_match_min_keywords()
    test_render_playbook_for_prompt()

    print(f"\n{'='*60}")
    print(f"  {PASSED} passed, {FAILED} failed")
    print(f"{'='*60}")
    sys.exit(0 if FAILED == 0 else 1)


if __name__ == "__main__":
    main()
