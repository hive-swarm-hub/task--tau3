"""Intervention J — prefer-discoverable-reads.

Insight (brian2, post #17): 49 failures have unmatched expected action
``call_discoverable_agent_tool(get_bank_account_transactions_9173)``. The agent
calls the base read tool ``get_credit_card_transactions_by_user`` (which
succeeds at the API level) instead of first unlocking the discoverable variant
the oracle expected. Similar mismatches for
``file_credit_card_transaction_dispute_4829`` (45 occurrences) and
``get_all_user_accounts_by_user_id_3847`` (31 occurrences) — 125 potential
action-match recoveries in total.

A prompt-only fix regressed lite by -1 because the added section competed
with junjie's Phase D annotator framing. So the fix has to be gate-level:
when the agent is about to call a base read tool for which a documented
discoverable variant exists, intercept the call and rewrite it to unlock
the variant instead. The LLM will then issue the correct discoverable call
on the next turn.

Status: experimental. Flip to active only after Stage-A validation.
"""

from __future__ import annotations

from typing import Optional

from interventions import REGISTRY, HookContext, HookResult, Intervention

try:
    # Import is deferred — tests mock ToolCall so the framework is importable
    # even without tau2 installed.
    from tau2.data_model.message import ToolCall
except Exception:  # pragma: no cover - import fallback for test environments
    ToolCall = None  # type: ignore[assignment]


# ── hand-curated base → discoverable mapping ─────────────────────────────────
# Pair 1: verified from tools.py:445 (base) and tools.py:2934 (discoverable).
# Pair 2/3: no base @is_tool(READ) equivalent exists in
#   tau2-bench/src/tau2/domains/banking_knowledge/tools.py — the write-dispute
#   flow and the all-accounts aggregator are only exposed as discoverable
#   tools. brian2's trace aggregate does not pin a single base tool; the
#   agent variously calls no WRITE tool at all or calls an unrelated WRITE
#   (e.g. ``submit_cash_back_dispute_0589`` user-tool) in place of the
#   discoverable. Leaving these as TODO — an honest partial mapping beats a
#   fabricated one.
BASE_TO_DISCOVERABLE: dict[str, str] = {
    "get_credit_card_transactions_by_user": "get_bank_account_transactions_9173",
    # TODO(junjie): find base for file_credit_card_transaction_dispute_4829
    #   (45 occurrences) — no @is_tool WRITE equivalent in tools.py; may need
    #   trace-level analysis to identify what the agent calls instead.
    # TODO(junjie): find base for get_all_user_accounts_by_user_id_3847
    #   (31 occurrences) — closest sibling is get_credit_card_accounts_by_user
    #   at tools.py:456 but that's a partial (credit-only) read, not a true
    #   equivalent. Could be a candidate once the Stage-A harness disambiguates.
}


def prefer_discoverable_reads(ctx: HookContext) -> Optional[HookResult]:
    """Rewrite base-read calls that have a known discoverable variant."""
    tc = ctx.tool_call
    if tc is None:
        return None
    variant = BASE_TO_DISCOVERABLE.get(getattr(tc, "name", ""))
    if variant is None:
        return None  # non-matching tool — pass through

    unlocked = ctx.state.get("unlocked_for_agent", set()) or set()
    mentioned = ctx.state.get("mentioned_in_kb", set()) or set()

    # Case 1: already unlocked but the LLM called the base anyway. Drop and
    # remind — we don't re-unlock (that would be a no-op at best, a loop risk
    # at worst).
    if variant in unlocked:
        return HookResult(
            drop=True,
            drop_note=(
                f"I already unlocked `{variant}` — use call_discoverable_agent_tool"
                f"(agent_tool_name='{variant}') instead of the base read."
            ),
            log={"intervention": "J", "case": "already_unlocked", "variant": variant},
        )

    # Case 2: the variant was surfaced in KB but not yet unlocked. Rewrite the
    # call to an unlock — next turn the LLM will dispatch the variant.
    if variant in mentioned and ToolCall is not None:
        unlock_call = ToolCall(
            id=getattr(tc, "id", "") or "",
            name="unlock_discoverable_agent_tool",
            arguments={"agent_tool_name": variant},
            requestor=getattr(tc, "requestor", "assistant"),
        )
        return HookResult(
            drop=True,
            replace_with=unlock_call,
            drop_note=(
                f"I need to use the discoverable variant — unlocking `{variant}` "
                f"now; next turn I'll call it via call_discoverable_agent_tool."
            ),
            log={"intervention": "J", "case": "rewrite_to_unlock", "variant": variant},
        )

    # Case 3: variant never surfaced in KB — we don't know if it applies to
    # this task. Let the base call through.
    return None


REGISTRY.register(Intervention(
    id="J",
    name="prefer-discoverable-reads",
    hook="gate_pre",
    target_cluster="discovery",  # root cause is a discovery-vs-execution split:
                                 # the agent has discovered the base read path
                                 # but not the discoverable one the oracle expects.
    author="junjie",
    description=(
        "When the agent calls a base read tool that has a documented discoverable "
        "variant, rewrite the call to first unlock_discoverable_agent_tool(the_variant) "
        "and defer the read — next turn the LLM will issue the correct discoverable call."
    ),
    status="experimental",  # flip to active after Stage-A validation
    apply=prefer_discoverable_reads,
))
