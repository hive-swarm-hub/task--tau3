"""Banking-domain interventions registered into the shared REGISTRY.

Each of the 9 interventions that previously lived inline in
``agent.py::_gate_tool_calls`` is extracted into a standalone ``apply``
function here, wrapped in an :class:`Intervention` dataclass, and registered
at import time. ``agent.py`` imports this module once so the registrations
fire; the gate then simply iterates ``REGISTRY.for_hook("gate_pre")`` /
``REGISTRY.for_hook("gate_post")`` instead of the old elif-cascade.

The original interventions are labeled A–H in the source comments. We keep
those IDs and add J for the post-give "tell-the-customer" reminder which
fires on gate_post rather than gate_pre. "I" is skipped to avoid confusion
with the digit 1.

Behavioral contract (preserved exactly):

- gate_pre interventions fire per ToolCall in order. An intervention that
  returns ``HookResult(drop=True, drop_note=..., log=...)`` removes the call
  from the kept list; the drop_note is appended to the assistant content.
  An intervention that returns ``HookResult(replace_with=new_tc, log=...)``
  rewrites the ToolCall; the next intervention sees the rewrite.
- gate_post interventions fire once per kept ToolCall. They return
  ``HookResult(annotation=...)`` whose text is appended to the assistant
  content after any drop-notes.

All pre-existing log entries and drop-note strings are preserved verbatim.
"""

from __future__ import annotations

import json
from typing import Optional

from tau2.data_model.message import ToolCall

from compass import (
    COMPASS,
    canonicalize_log_verification_args,
    canonicalize_json_args,
)
from interventions import HookContext, HookResult, Intervention, REGISTRY


# ── banking extension handle ─────────────────────────────────────────────────
# Interventions E, F, H delegate to the banking extension's domain-specific
# helpers. We resolve it lazily so tests that swap extensions still work.

def _banking_ext():
    if COMPASS.has_extension("banking"):
        return COMPASS.get_extension("banking")
    return None


# ── gate_pre interventions ───────────────────────────────────────────────────


def _apply_G_canonicalize_log_verification(ctx: HookContext) -> Optional[HookResult]:
    """G — canonicalize log_verification arguments (time, DOB, phone format)."""
    tc = ctx.tool_call
    args = tc.arguments if isinstance(tc.arguments, dict) else {}
    if tc.name != "log_verification" or not isinstance(args, dict):
        return None
    fixed = canonicalize_log_verification_args(args)
    if fixed == args:
        return None
    log = {
        "turn": ctx.state.get("turn_count", 0),
        "reason": "canonicalized_log_verification",
        "changed_keys": [k for k in fixed if fixed.get(k) != args.get(k)],
    }
    return HookResult(
        replace_with=ToolCall(id=tc.id, name=tc.name, arguments=fixed),
        log=log,
    )


def _apply_D_hallucination_guard(ctx: HookContext) -> Optional[HookResult]:
    """D — drop unlock/give of discoverable names not in the parsed catalog."""
    tc = ctx.tool_call
    valid_names = COMPASS.valid_names
    if not valid_names:
        return None
    if tc.name not in ("unlock_discoverable_agent_tool", "give_discoverable_user_tool"):
        return None
    args = tc.arguments if isinstance(tc.arguments, dict) else {}
    target = (
        args.get("agent_tool_name")
        or args.get("discoverable_tool_name")
        or args.get("tool_name")
    )
    if not target or target in valid_names:
        return None
    return HookResult(
        drop=True,
        drop_note=(
            f"The tool name '{target}' does not exist in the discoverable "
            f"tool catalog. I should consult the catalog in my system prompt "
            f"for the correct name, or use a base tool if appropriate."
        ),
        log={
            "turn": ctx.state.get("turn_count", 0),
            "reason": "dropped_hallucinated_tool_name",
            "target": target,
        },
    )


def _apply_A_dedupe_unlock(ctx: HookContext) -> Optional[HookResult]:
    """A — drop redundant / mixed-mode ``unlock_discoverable_agent_tool``.

    Fires when the agent calls unlock for a tool that is already unlocked
    for agent, or already given to the user (wrong mode).
    """
    tc = ctx.tool_call
    if tc.name != "unlock_discoverable_agent_tool":
        return None
    args = tc.arguments if isinstance(tc.arguments, dict) else {}
    unlocked_agent = ctx.state.get("unlocked_for_agent", set()) or set()
    unlocked_user = ctx.state.get("unlocked_for_user", set()) or set()
    turn = ctx.state.get("turn_count", 0)
    target = args.get("agent_tool_name") or args.get("tool_name")
    if target and target in unlocked_user:
        return HookResult(
            drop=True,
            drop_note=(
                f"You already have access to {target} — I provided it earlier. "
                f"Please call it now with the required arguments."
            ),
            log={"turn": turn, "reason": "dropped_unlock_already_given_to_user", "target": target},
        )
    if target and target in unlocked_agent:
        return HookResult(
            drop=True,
            drop_note=f"I already have {target} unlocked and will proceed with the call.",
            log={"turn": turn, "reason": "dropped_redundant_unlock", "target": target},
        )
    return None


def _apply_B_dedupe_give(ctx: HookContext) -> Optional[HookResult]:
    """B — drop redundant / mixed-mode ``give_discoverable_user_tool``.

    Fires when the agent calls give for a tool already given to the user,
    or already unlocked for the agent (wrong mode).
    """
    tc = ctx.tool_call
    if tc.name != "give_discoverable_user_tool":
        return None
    args = tc.arguments if isinstance(tc.arguments, dict) else {}
    unlocked_agent = ctx.state.get("unlocked_for_agent", set()) or set()
    unlocked_user = ctx.state.get("unlocked_for_user", set()) or set()
    turn = ctx.state.get("turn_count", 0)
    target = args.get("discoverable_tool_name") or args.get("tool_name")
    if target and target in unlocked_agent:
        return HookResult(
            drop=True,
            drop_note=f"I will handle {target} on your behalf since I have it unlocked.",
            log={"turn": turn, "reason": "dropped_give_already_unlocked_for_agent", "target": target},
        )
    if target and target in unlocked_user:
        return HookResult(
            drop=True,
            drop_note=(
                f"You already have access to {target} — I provided it earlier. "
                f"Please call it now with the required arguments."
            ),
            log={"turn": turn, "reason": "dropped_redundant_give", "target": target},
        )
    return None


def _apply_C_json_encode_inner_arguments(ctx: HookContext) -> Optional[HookResult]:
    """C — JSON-encode and canonicalize call_discoverable_*_tool.arguments.

    τ²-bench requires the inner ``arguments`` field to be a JSON STRING and
    compares it literally against the oracle. We canonicalize with sorted
    keys and compact separators.
    """
    tc = ctx.tool_call
    if tc.name not in ("call_discoverable_agent_tool", "call_discoverable_user_tool"):
        return None
    args = tc.arguments if isinstance(tc.arguments, dict) else {}
    if not isinstance(args, dict):
        return None
    inner = args.get("arguments")
    if inner is None:
        return None
    canonical = canonicalize_json_args(inner)
    if canonical == inner:
        return None
    fixed = dict(args)
    fixed["arguments"] = canonical
    return HookResult(
        replace_with=ToolCall(id=tc.id, name=tc.name, arguments=fixed),
        log={
            "turn": ctx.state.get("turn_count", 0),
            "reason": "canonicalized_json_arguments",
            "target": args.get("agent_tool_name") or args.get("discoverable_tool_name") or "?",
        },
    )


def _apply_H_enum_prevalidation(ctx: HookContext) -> Optional[HookResult]:
    """H — block call_discoverable_agent_tool with invalid enum values.

    Uses COMPASS.enum_constraints (docstring-parsed) plus any additional
    domain-specific constraints the banking extension injects via
    ``extra_enum_constraints``.
    """
    tc = ctx.tool_call
    if tc.name != "call_discoverable_agent_tool":
        return None
    args = tc.arguments if isinstance(tc.arguments, dict) else {}
    if not isinstance(args, dict):
        return None
    target_tool = args.get("agent_tool_name") or ""
    inner_str = args.get("arguments")
    if not target_tool or not isinstance(inner_str, str) or not inner_str:
        return None
    constraints = COMPASS.enum_constraints(target_tool)
    ext = _banking_ext()
    if ext is not None and hasattr(ext, "extra_enum_constraints"):
        extra = ext.extra_enum_constraints(target_tool, inner_str)
        if extra:
            constraints = {**constraints, **extra}
    if not constraints:
        return None
    try:
        inner_kwargs = json.loads(inner_str)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(inner_kwargs, dict):
        return None
    violations = []
    for param, valid in constraints.items():
        got = inner_kwargs.get(param)
        if got is not None and got not in valid:
            violations.append((param, got, valid))
    if not violations:
        return None
    details = "; ".join(
        f"{p} must be one of {v} but got {g!r}"
        for p, g, v in violations
    )
    return HookResult(
        drop=True,
        drop_note=(
            f"Blocked {target_tool}: {details}. "
            f"Retry with a valid value — the action matcher scores "
            f"the FIRST call attempt."
        ),
        log={
            "turn": ctx.state.get("turn_count", 0),
            "reason": "blocked_enum_violation",
            "target": target_tool,
            "violations": [{"param": p, "got": g} for p, g, _ in violations],
        },
    )


def _apply_E_phase2_guard(ctx: HookContext) -> Optional[HookResult]:
    """E — block agent-side cleanup tools that pair with a pending user-side tool.

    Pairing table is domain-specific (banking extension's ``phase2_pairs``).
    Fires only when zero user-side calls have been observed since the give.
    """
    tc = ctx.tool_call
    if tc.name != "call_discoverable_agent_tool":
        return None
    args = tc.arguments if isinstance(tc.arguments, dict) else {}
    if not isinstance(args, dict):
        return None
    ext = _banking_ext()
    phase2_pairs = ext.phase2_pairs if ext is not None else {}
    if not phase2_pairs:
        return None
    target_tool = args.get("agent_tool_name") or ""
    unlocked_user = ctx.state.get("unlocked_for_user", set()) or set()
    user_calls = ctx.state.get("user_calls_by_tool", {}) or {}
    for given_tool, agent_prefixes in phase2_pairs.items():
        if given_tool not in unlocked_user:
            continue
        if not any(target_tool.startswith(p) for p in agent_prefixes):
            continue
        if user_calls.get(given_tool, 0) == 0:
            return HookResult(
                drop=True,
                drop_note=(
                    f"I gave you the tool {given_tool} earlier — please call it "
                    f"with the specific transaction details first. I will only "
                    f"update the backend records after the customer has submitted."
                ),
                log={
                    "turn": ctx.state.get("turn_count", 0),
                    "reason": "blocked_phase2_before_user_call",
                    "target": target_tool,
                    "given_tool": given_tool,
                },
            )
    return None


# ── gate_post interventions ──────────────────────────────────────────────────


def _apply_F_post_give_reminder(ctx: HookContext) -> Optional[HookResult]:
    """F — post-give tell-the-customer reminder.

    For each give_discoverable_user_tool that was kept, emit a reminder:
    - Prefer the banking extension's targeted dispute-candidate formatter
      (Phase D upgrade — lists specific outlier transaction_ids).
    - Fall back to the extension's generic hint if available.
    - Fall back to a bare reminder if no extension is registered.
    """
    tc = ctx.tool_call
    if tc.name != "give_discoverable_user_tool":
        return None
    args = tc.arguments if isinstance(tc.arguments, dict) else {}
    target = args.get("discoverable_tool_name") or args.get("tool_name")
    if not target:
        return None
    uid = ctx.state.get("current_user_id") or "<user_id>"

    ext = _banking_ext()
    if ext is not None:
        candidates = ext.get_dispute_candidates(ctx.state, uid)
        if candidates:
            domain_note = ext.format_dispute_targets_message(target, candidates, uid)
            if domain_note:
                return HookResult(annotation=domain_note)
        fallback = ext.format_give_fallback_message(target, ctx.state, uid)
        if fallback:
            return HookResult(annotation=fallback)

    return HookResult(
        annotation=(
            f"Reminder: tell the customer the EXACT arguments to use with {target}. "
            f"Pull any IDs they need from a get_* tool result and include them in your "
            f"next message — the customer cannot guess the right values."
        )
    )


# ── registrations ────────────────────────────────────────────────────────────
# Order matters: gate_pre interventions fire top-to-bottom per ToolCall.
# We preserve the exact ordering from the original inline code:
#   G (canonicalize log_verification) → D (hallucination guard) →
#   A/B (dedupe) → C (JSON-encode inner args) → H (enum check) → E (Phase-2).

REGISTRY.register(Intervention(
    id="G",
    name="canonicalize-log-verification",
    hook="gate_pre",
    target_cluster="verification",
    author="junjie",
    description=(
        "Canonicalize log_verification arguments (time_verified, "
        "date_of_birth, phone_number) so they literal-compare against the "
        "oracle action JSON."
    ),
    apply=_apply_G_canonicalize_log_verification,
))

REGISTRY.register(Intervention(
    id="D",
    name="hallucination-guard",
    hook="gate_pre",
    target_cluster="discovery",
    author="junjie",
    description=(
        "Drop unlock_discoverable_agent_tool / give_discoverable_user_tool "
        "calls whose target name is not in the parsed discoverable catalog."
    ),
    apply=_apply_D_hallucination_guard,
))

REGISTRY.register(Intervention(
    id="A",
    name="dedupe-unlock",
    hook="gate_pre",
    target_cluster="discovery",
    author="junjie",
    description=(
        "Drop unlock_discoverable_agent_tool when the target is already "
        "unlocked for agent, or already given to the user (wrong mode)."
    ),
    apply=_apply_A_dedupe_unlock,
))

REGISTRY.register(Intervention(
    id="B",
    name="dedupe-give",
    hook="gate_pre",
    target_cluster="discovery",
    author="junjie",
    description=(
        "Drop give_discoverable_user_tool when the target is already "
        "given to the user, or already unlocked for the agent (wrong mode)."
    ),
    apply=_apply_B_dedupe_give,
))

REGISTRY.register(Intervention(
    id="C",
    name="json-encode-inner-arguments",
    hook="gate_pre",
    target_cluster="arguments",
    author="junjie",
    description=(
        "JSON-encode and canonicalize the inner `arguments` field on "
        "call_discoverable_agent_tool / call_discoverable_user_tool — "
        "τ²-bench requires it to be a string and compares it literally."
    ),
    apply=_apply_C_json_encode_inner_arguments,
))

REGISTRY.register(Intervention(
    id="H",
    name="enum-prevalidation",
    hook="gate_pre",
    target_cluster="arguments",
    author="brian2",
    description=(
        "Block call_discoverable_agent_tool when the inner arguments contain "
        "an out-of-set enum value (docstring-parsed + domain-injected constraints)."
    ),
    apply=_apply_H_enum_prevalidation,
))

REGISTRY.register(Intervention(
    id="E",
    name="phase2-guard",
    hook="gate_pre",
    target_cluster="dispute",
    author="junjie",
    description=(
        "Block agent-side cleanup tools (e.g. update_transaction_rewards_NNNN) "
        "that pair with a still-pending user-side tool whose user-call counter is zero."
    ),
    apply=_apply_E_phase2_guard,
))

REGISTRY.register(Intervention(
    id="F",
    name="post-give-tell-customer",
    hook="gate_post",
    target_cluster="dispute",
    author="junjie",
    description=(
        "After a give_discoverable_user_tool is kept, append a reminder to the "
        "assistant content naming the exact argument values the customer needs "
        "(using banking's offline dispute calculator when available)."
    ),
    apply=_apply_F_post_give_reminder,
))


# NOTE: annotator-level interventions (scenario playbook, tool mentions, enum
# constraints, etc.) currently live inline in agent.py::annotate_banking —
# registering metadata-only entries (apply=None) would provide false
# discoverability since they have no dispatch. Future work: extract each
# annotator signal into a callable and register it here as hook="annotator".


__all__ = [
    "REGISTRY",
    # re-export for convenience; callers may import from interventions directly
]
