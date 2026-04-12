"""Unit tests for Intervention K (verify-before-mutate).

Uses a stub ToolCall with attribute access so the test doesn't require
tau2 to be importable.

Run: python3 -m pytest eval/test_intervention_verify_before_mutate.py -q
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interventions_verify_before_mutate as modk  # noqa: E402
from interventions import HookContext, REGISTRY  # noqa: E402


class _StubToolCall(SimpleNamespace):
    """Stand-in for tau2.data_model.message.ToolCall with attr access."""

    def __init__(self, name, arguments=None, id="tc-1", requestor="assistant"):
        super().__init__(
            name=name, arguments=arguments, id=id, requestor=requestor
        )


# --- Case 1: wrapped mutation before verification → DROP ---------------------

def test_wrapped_mutation_blocked_when_unverified():
    inner = {"agent_tool_name": "change_user_email_1234", "arguments": "{}"}
    tc = _StubToolCall(
        name="call_discoverable_agent_tool",
        arguments=json.dumps(inner),
    )
    ctx = HookContext(tool_call=tc, state={"verified_user_ids": set()})
    result = modk.verify_before_mutate(ctx)
    assert result is not None, "should have intervened"
    assert result.drop is True
    assert "verify identity" in (result.drop_note or "").lower()
    assert result.log["reason"] == "blocked_mutation_before_verify"
    assert result.log["target"] == "change_user_email_1234"


# --- Case 2: wrapped mutation after verification → pass-through --------------

def test_wrapped_mutation_allowed_when_verified():
    inner = {"agent_tool_name": "update_transaction_rewards_3847"}
    tc = _StubToolCall(
        name="call_discoverable_agent_tool",
        arguments=json.dumps(inner),
    )
    ctx = HookContext(
        tool_call=tc,
        state={"verified_user_ids": {"user_1"}},
    )
    assert modk.verify_before_mutate(ctx) is None


# --- Case 3: read-only discoverable call → pass-through (not a mutation) -----

def test_read_only_wrapped_call_passes_through():
    inner = {"agent_tool_name": "get_credit_card_transactions_by_user"}
    tc = _StubToolCall(
        name="call_discoverable_agent_tool",
        arguments=json.dumps(inner),
    )
    ctx = HookContext(tool_call=tc, state={"verified_user_ids": set()})
    assert modk.verify_before_mutate(ctx) is None


# --- Case 4: direct base-tool mutation without verification → DROP -----------

def test_direct_base_mutation_blocked_when_unverified():
    tc = _StubToolCall(
        name="change_user_email",
        arguments={"user_id": "u1", "email": "a@b.c"},
    )
    ctx = HookContext(tool_call=tc, state={"verified_user_ids": set()})
    result = modk.verify_before_mutate(ctx)
    assert result is not None
    assert result.drop is True


# --- Registration test -------------------------------------------------------

def test_intervention_registered_as_experimental():
    intv = REGISTRY.get("K")
    assert intv is not None
    assert intv.name == "verify-before-mutate"
    assert intv.hook == "gate_pre"
    assert intv.status == "experimental"
    assert intv.author == "charlie"
    assert intv.target_cluster == "verification"
