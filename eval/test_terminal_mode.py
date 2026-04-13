"""Tests for RETRIEVAL_VARIANT=terminal_use support in agent.py.

Companion to test_annotator.py which covers BM25 behavior. These tests lock
in the cross-mode invariants: system-prompt augmentation, state plumbing,
annotator tool-name-agnosticism, gate-registry independence, and backwards
compatibility when the env var is absent.

IMPORTANT — module-level caching: ``RETRIEVAL_VARIANT`` is read at import
time in agent.py (``RETRIEVAL_VARIANT = os.environ.get(...)``). Setting the
env var AFTER import has no effect on already-captured module state. Tests
that need to flip the mode therefore ``importlib.reload(agent)`` inside the
test — the reload re-reads the env var. ``monkeypatch.setenv`` pairs with
``reload`` so the change is automatically reverted after the test.

Run: python3 -m pytest eval/test_terminal_mode.py -q
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent as agent_mod  # noqa: E402
from tau2.data_model.message import ToolMessage  # noqa: E402


# ── fixture content (spec-provided samples) ──────────────────────────────────

_SAMPLE_GREP_OUTPUT = """\
doc_credit_cards_dispute_001.json:14:To file a cash back dispute, the customer should call submit_cash_back_dispute_0589.
doc_credit_cards_dispute_001.json:22:dispute_reason must be one of: 'fraud', 'unauthorized_charge', 'duplicate_charge'.
"""

_SAMPLE_CAT_OUTPUT = """\
{"doc_id": "dispute_001", "title": "Cash Back Dispute", "content": "To file a cash back dispute the customer must verify identity first via log_verification. Then the agent calls give_discoverable_user_tool with submit_cash_back_dispute_0589. Arguments: dispute_reason must be one of: 'fraud', 'unauthorized_charge', 'duplicate_charge'."}
"""

_SAMPLE_KB_SEARCH_OUTPUT = """\
Document: Cash Back Dispute
To file a cash back dispute the customer must verify identity first via log_verification.
Then the agent calls give_discoverable_user_tool with submit_cash_back_dispute_0589.
Arguments: dispute_reason must be one of: 'fraud', 'unauthorized_charge', 'duplicate_charge'.
"""


def _reload_agent_with_env(monkeypatch, variant: str | None):
    """Set (or unset) RETRIEVAL_VARIANT and reload agent.py so the module-level
    constant is re-read. Returns the freshly-reloaded module."""
    if variant is None:
        monkeypatch.delenv("RETRIEVAL_VARIANT", raising=False)
    else:
        monkeypatch.setenv("RETRIEVAL_VARIANT", variant)
    return importlib.reload(agent_mod)


# ── 1. System prompt augmentation ────────────────────────────────────────────

def test_bm25_prompt_has_no_terminal_section(monkeypatch):
    """Default/BM25 mode system prompt does NOT contain the terminal section."""
    mod = _reload_agent_with_env(monkeypatch, "bm25")
    a = mod.create_custom_agent(tools=[], domain_policy="test")
    prompt = a.system_prompt
    # Header of the terminal section must be absent.
    assert "Terminal-mode retrieval" not in prompt
    # The shell-tool coaching phrases should not appear as instructions.
    assert "`shell` tool instead of `KB_search`" not in prompt


def test_terminal_prompt_has_shell_instructions(monkeypatch):
    """terminal_use mode: prompt teaches shell/grep/cat usage."""
    mod = _reload_agent_with_env(monkeypatch, "terminal_use")
    a = mod.create_custom_agent(tools=[], domain_policy="test")
    prompt = a.system_prompt
    assert "Terminal-mode retrieval" in prompt
    # At least one of the shell verbs should be taught.
    assert any(tok in prompt for tok in ("grep", "cat ", "ls"))
    # The shell tool itself must be named.
    assert "shell" in prompt.lower()


# ── 2. State plumbing ────────────────────────────────────────────────────────

def test_task_state_exposes_retrieval_variant(monkeypatch):
    """agent._task_state['retrieval_variant'] matches the detected mode and
    survives _reset_task_state."""
    mod = _reload_agent_with_env(monkeypatch, "terminal_use")
    a = mod.create_custom_agent(tools=[], domain_policy="test")
    assert a._task_state.get("retrieval_variant") == "terminal_use"
    # Reset should preserve the mode (agent-lifetime value, not per-task).
    a._reset_task_state()
    assert a._task_state.get("retrieval_variant") == "terminal_use"


# ── 3. Annotator is tool-name-agnostic ───────────────────────────────────────

def test_annotator_fires_on_shell_output(monkeypatch):
    """annotate_banking surfaces the same signals on shell grep/cat output as
    on KB_search output — annotations scan content strings, not tool names."""
    mod = _reload_agent_with_env(monkeypatch, "terminal_use")
    # Use a state dict with none of the target tool unlocked so the STILL TO
    # UNLOCK branch fires (matches test_annotator.py conventions).
    state = {"unlocked_for_agent": set(), "unlocked_for_user": set(), "verified_user_ids": set()}
    grep_out = mod.annotate_banking(_SAMPLE_GREP_OUTPUT, state=state)
    cat_out = mod.annotate_banking(_SAMPLE_CAT_OUTPUT, state=state)
    for result in (grep_out, cat_out):
        assert "submit_cash_back_dispute_0589" in result
        assert "ENUM CONSTRAINT" in result
        # Enum values must be surfaced verbatim.
        assert "fraud" in result and "duplicate_charge" in result


def test_annotator_fires_on_kb_search_output(monkeypatch):
    """Baseline: same signals fire on KB_search-formatted text (no env var
    change — annotator is stateless w.r.t. retrieval mode)."""
    mod = _reload_agent_with_env(monkeypatch, "bm25")
    state = {"unlocked_for_agent": set(), "unlocked_for_user": set(), "verified_user_ids": set()}
    result = mod.annotate_banking(_SAMPLE_KB_SEARCH_OUTPUT, state=state)
    assert "submit_cash_back_dispute_0589" in result
    assert "ENUM CONSTRAINT" in result
    assert "duplicate_charge" in result


def test_annotator_accepts_tool_message_content_from_any_source(monkeypatch):
    """Regardless of whether the ToolMessage came from KB_search or shell, the
    annotator operates on the .content string. Construct both and confirm
    identical-looking chunks produce equivalent annotations."""
    mod = _reload_agent_with_env(monkeypatch, "terminal_use")
    shared_content = (
        "Use submit_cash_back_dispute_0589 to file the dispute. "
        "dispute_reason must be one of: 'fraud', 'duplicate_charge'."
    )
    kb_msg = ToolMessage(role="tool", id="t1", content=shared_content)
    shell_msg = ToolMessage(role="tool", id="t2", content=shared_content)
    state = {"unlocked_for_agent": set(), "unlocked_for_user": set(), "verified_user_ids": set()}
    a = mod.annotate_banking(kb_msg.content, state=state)
    b = mod.annotate_banking(shell_msg.content, state=state)
    assert a == b
    assert "ENUM CONSTRAINT" in a
    assert "STILL TO UNLOCK" in a or "ALREADY" in a


# ── 4. Gate interventions dispatch under terminal mode ───────────────────────

def test_gate_interventions_run_under_terminal_mode(monkeypatch):
    """Intervention registry is global — gate_pre hooks fire regardless of
    retrieval mode."""
    mod = _reload_agent_with_env(monkeypatch, "terminal_use")
    from interventions import REGISTRY  # noqa: WPS433  (import inside test is fine)
    gate_pre = REGISTRY.for_hook("gate_pre")
    assert isinstance(gate_pre, list)
    assert len(gate_pre) > 0, "no gate_pre interventions registered"
    # Agent construction under terminal_use must still succeed.
    a = mod.create_custom_agent(tools=[], domain_policy="test")
    assert a is not None
    assert a._task_state.get("retrieval_variant") == "terminal_use"


def test_gate_hallucination_guard_fires_under_terminal_mode(monkeypatch):
    """Spot check: a representative gate_pre intervention (hallucination
    guard) still intercepts calls when retrieval_variant is terminal_use."""
    mod = _reload_agent_with_env(monkeypatch, "terminal_use")
    from tau2.data_model.message import AssistantMessage, ToolCall  # noqa: WPS433
    a = mod.create_custom_agent(tools=[], domain_policy="test")
    msg = AssistantMessage(role="assistant", content="", tool_calls=[
        ToolCall(id="1", name="unlock_discoverable_agent_tool",
                 arguments={"agent_tool_name": "totally_fake_tool_9999"}),
    ])
    out = a._gate_tool_calls(msg)
    assert out.tool_calls is None, "hallucinated unlock should be dropped"


# ── 5. Backwards compat ──────────────────────────────────────────────────────

def test_no_retrieval_variant_env_defaults_to_bm25(monkeypatch):
    """Missing env var → bm25 default everywhere."""
    mod = _reload_agent_with_env(monkeypatch, None)
    assert mod.RETRIEVAL_VARIANT == "bm25"
    a = mod.create_custom_agent(tools=[], domain_policy="test")
    assert a._retrieval_variant == "bm25"
    assert a._task_state.get("retrieval_variant") == "bm25"
    # System prompt should not contain the terminal section.
    assert "Terminal-mode retrieval" not in a.system_prompt


def test_unknown_variant_does_not_inject_terminal_section(monkeypatch):
    """An unrecognized RETRIEVAL_VARIANT value (e.g. golden_retrieval) must
    NOT trigger the terminal_use prompt section — only the exact 'terminal_use'
    string flips the section on."""
    mod = _reload_agent_with_env(monkeypatch, "golden_retrieval")
    a = mod.create_custom_agent(tools=[], domain_policy="test")
    assert a._retrieval_variant == "golden_retrieval"
    assert "Terminal-mode retrieval" not in a.system_prompt


# ── teardown: restore bm25 for subsequent test files ─────────────────────────

@pytest.fixture(autouse=True, scope="module")
def _restore_agent_module():
    yield
    # Ensure a final reload with a clean (bm25) env so other test files that
    # `from agent import ...` see the default state.
    os.environ.pop("RETRIEVAL_VARIANT", None)
    importlib.reload(agent_mod)
