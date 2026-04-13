"""Unit tests for Intervention J (prefer-discoverable-reads).

Covers the three rewrite cases + the pass-through case. Uses a stub ``ToolCall``
so the test doesn't require the tau2 package to be importable — we patch the
module-level symbol the intervention imports from tau2.

Run: python3 -m pytest eval/test_intervention_j.py -q
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interventions_prefer_discoverable_reads as modj  # noqa: E402
from interventions import HookContext  # noqa: E402


class _StubToolCall(SimpleNamespace):
    """Stand-in for tau2.data_model.message.ToolCall with attribute access."""

    def __init__(self, name, arguments=None, id="tc-1", requestor="assistant"):
        super().__init__(
            name=name, arguments=arguments or {}, id=id, requestor=requestor
        )


@pytest.fixture(autouse=True)
def patch_toolcall(monkeypatch):
    """Ensure the intervention can construct replacement calls in tests."""
    monkeypatch.setattr(modj, "ToolCall", _StubToolCall)


# ── the three cases ─────────────────────────────────────────────────────────

def test_base_call_when_variant_mentioned_rewrites_to_unlock():
    tc = _StubToolCall("get_credit_card_transactions_by_user", {"user_id": "u1"})
    ctx = HookContext(
        tool_call=tc,
        state={
            "mentioned_in_kb": {"get_bank_account_transactions_9173"},
            "unlocked_for_agent": set(),
        },
    )
    result = modj.prefer_discoverable_reads(ctx)
    assert result is not None
    assert result.drop is True
    assert result.replace_with is not None
    assert result.replace_with.name == "unlock_discoverable_agent_tool"
    assert result.replace_with.arguments == {
        "agent_tool_name": "get_bank_account_transactions_9173"
    }
    assert "unlocking" in (result.drop_note or "").lower()


def test_base_call_when_variant_already_unlocked_drops_with_reminder():
    tc = _StubToolCall("get_credit_card_transactions_by_user", {"user_id": "u1"})
    ctx = HookContext(
        tool_call=tc,
        state={
            "mentioned_in_kb": {"get_bank_account_transactions_9173"},
            "unlocked_for_agent": {"get_bank_account_transactions_9173"},
        },
    )
    result = modj.prefer_discoverable_reads(ctx)
    assert result is not None
    assert result.drop is True
    assert result.replace_with is None  # no rewrite — just drop + inject
    assert "already unlocked" in (result.drop_note or "").lower()


def test_base_call_when_variant_not_mentioned_passes_through():
    tc = _StubToolCall("get_credit_card_transactions_by_user", {"user_id": "u1"})
    ctx = HookContext(
        tool_call=tc,
        state={"mentioned_in_kb": set(), "unlocked_for_agent": set()},
    )
    assert modj.prefer_discoverable_reads(ctx) is None


def test_non_matching_tool_passes_through():
    tc = _StubToolCall("get_user_information_by_id", {"user_id": "u1"})
    ctx = HookContext(
        tool_call=tc,
        state={
            "mentioned_in_kb": {"get_bank_account_transactions_9173"},
            "unlocked_for_agent": set(),
        },
    )
    assert modj.prefer_discoverable_reads(ctx) is None


def test_intervention_registered_as_experimental():
    from interventions import REGISTRY
    intv = REGISTRY.get("J")
    assert intv is not None
    assert intv.name == "prefer-discoverable-reads"
    assert intv.hook == "gate_pre"
    assert intv.status == "experimental"
    assert intv.author == "junjie"
