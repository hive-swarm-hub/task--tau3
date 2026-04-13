"""Intervention K: verify-before-mutate.

Block any agent-side mutation tool call when no user identity has been
verified yet (``state["verified_user_ids"]`` is empty). This closes the
#1 failure mode called out in program.md:

    > priority_1_verification_or_unlock — the agent either called a mutation
    > tool before verifying identity, or referenced a discoverable tool it
    > never unlocked.

Author: charlie (fresh swarm agent, experimental)

-----------------------------------------------------------------
Guesses I had to make (fresh agent with only program.md + docs/ +
interventions.py):

  G1. "What counts as a mutation?" — program.md and the docs inventory
      never list mutation tools explicitly. I use a prefix-based
      heuristic on the *inner* agent tool name: any name starting with
      one of the prefixes in MUTATION_PREFIXES is considered a
      mutation. This is brittle (read-only tools like
      ``submit_support_question`` would false-positive). A registry of
      mutation-vs-read-only tool names belongs on the banking
      extension; docs don't mention one.

  G2. "How do I read the tool call / args?" — program.md's worked
      example uses ``tc.name`` and ``tc.model_copy(update=...)``
      (pydantic). The inventory uses ``args["arguments"]`` to mean the
      top-level ToolCall.arguments field, which is itself a JSON
      string containing the inner call. I mirror that pattern via
      ``getattr(tc, "arguments", None)`` and fall back to ``tc.args``.

  G3. "What's the ID scheme?" — existing IDs are A–I. program.md uses
      "K" in its worked example; I grab "K" here. No registry-level
      reservation protocol is documented.

  G4. "target_cluster" — docstring in interventions.py lists
      "verification | arguments | dispute | execution | discovery |
      any". This intervention spans verification (why we block) and
      execution (what we block), but since the failure priority is P1
      (verification), I use "verification".

  G5. Hook type — ``gate_pre`` (program.md's mapping table:
      "Verification / unlock (P1) → gate_pre: block mutations before
      log_verification lands").

  G6. Registration wiring — to activate this plug-in at agent startup,
      add ``from interventions import verify_before_mutate as _  # noqa``
      to agent.py's imports. Until then it only loads when pytest or
      ``scripts/list_interventions.py`` imports it directly.
-----------------------------------------------------------------
"""
from __future__ import annotations

import json
from typing import Optional

from interventions import (
    REGISTRY,
    HookContext,
    HookResult,
    Intervention,
)


# G1: heuristic list of prefixes that identify a mutating discoverable tool.
# All banking "action" tools I've seen in the inventory start with one of these.
MUTATION_PREFIXES: tuple[str, ...] = (
    "change_",
    "update_",
    "submit_",
    "open_",
    "close_",
    "transfer_",
    "apply_",
    "delete_",
    "cancel_",
    "create_",
    "set_",
)

# Base tools that are mutations even when called directly (not via
# call_discoverable_agent_tool). Extend as needed.
DIRECT_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        # Hypothetical / representative — no authoritative list in the docs.
        "change_user_email",
        "apply_for_credit_card",
    }
)


def _inner_agent_tool_name(tc) -> Optional[str]:
    """Extract the inner mutation target if this is a wrapped discoverable call.

    Returns None if this isn't a call_discoverable_agent_tool wrapper.
    """
    # G2: inventory says args["arguments"] is a JSON string for
    # call_discoverable_agent_tool; args["agent_tool_name"] is the inner name.
    raw_args = getattr(tc, "arguments", None)
    if raw_args is None:
        raw_args = getattr(tc, "args", None)
    if raw_args is None:
        return None

    # raw_args might be a dict OR a JSON string. Handle both.
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw_args, dict):
        return None

    # Inventory (E) says args["agent_tool_name"] is the inner tool name.
    return raw_args.get("agent_tool_name") or raw_args.get("tool_name")


def _looks_like_mutation(tool_name: Optional[str]) -> bool:
    if not tool_name:
        return False
    if tool_name in DIRECT_MUTATION_TOOLS:
        return True
    return any(tool_name.startswith(p) for p in MUTATION_PREFIXES)


def verify_before_mutate(ctx: HookContext) -> Optional[HookResult]:
    tc = ctx.tool_call
    if tc is None:
        return None

    name = getattr(tc, "name", None)
    if not name:
        return None

    # Case 1: direct base-tool mutation call.
    target: Optional[str] = None
    if _looks_like_mutation(name):
        target = name
    # Case 2: wrapped mutation via call_discoverable_agent_tool.
    elif name == "call_discoverable_agent_tool":
        inner = _inner_agent_tool_name(tc)
        if _looks_like_mutation(inner):
            target = inner

    if target is None:
        return None

    verified = ctx.state.get("verified_user_ids") or set()
    # Tolerate the scaffold using a list instead of a set.
    if isinstance(verified, (list, tuple)):
        verified = set(verified)
    if verified:
        # Already verified — no-op.
        return None

    drop_note = (
        "I need to verify identity first. I will call log_verification "
        f"before attempting {target}."
    )
    return HookResult(
        drop=True,
        drop_note=drop_note,
        log={
            "reason": "blocked_mutation_before_verify",
            "target": target,
            "via": name,
        },
    )


REGISTRY.register(
    Intervention(
        id="K",
        name="verify-before-mutate",
        hook="gate_pre",
        target_cluster="verification",
        author="charlie",
        description=(
            "Drop any agent-side mutation tool call (direct or via "
            "call_discoverable_agent_tool) while verified_user_ids is "
            "empty. Instructs the LLM to call log_verification first."
        ),
        status="experimental",
        apply=verify_before_mutate,
    )
)
