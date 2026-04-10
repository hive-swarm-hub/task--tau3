"""τ³-bench banking_knowledge customer service agent — the artifact the swarm evolves.

This file is self-contained: all agent logic lives here. Modify anything.
The agent receives customer messages, domain tools (including KB_search), and
must follow the domain policy to resolve banking customer service tasks.

The PRIMARY optimization lever is `annotate_banking()` — it's the only code
path between τ²-bench's BM25 retriever and the LLM. Every KB_search result
passes through it before reaching the agent's context. Evolve it based on
what your failure traces show.
"""

import json
import os
import re
import time
from typing import Optional

from litellm import completion

# τ²-bench v1.0.0 restructured the agent API:
#   - LocalAgent → HalfDuplexAgent (now in tau2.agent.base_agent)
#   - LLMAgent subclass is no longer required; we extend HalfDuplexAgent directly
#   - ValidAgentInputMessage moved to tau2.agent.base_agent
from tau2.agent.base_agent import HalfDuplexAgent, ValidAgentInputMessage
from tau2.data_model.message import (
    APICompatibleMessage,
    AssistantMessage,
    Message,
    MultiToolMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import Tool

# ── PROMPTS ──────────────────────────────────────────────────────────────────

BASE_INSTRUCTIONS = """
You are a customer service agent for a bank. You MUST follow the <policy> exactly. The policy is your sole source of truth — never invent rules, procedures, or information not in the policy or provided by the user.

## Critical rules
1. Each turn: EITHER send a message to the user OR make a tool call. NEVER both at the same time.
2. Only make ONE tool call per turn.
3. Before any action that modifies the database (disputes, card orders, transfers, email changes, closures, etc.), you MUST:
   a. KB_search for the specific procedure.
   b. Verify all policy preconditions from the KB doc (eligibility, rules, restrictions).
   c. Execute the procedure EXACTLY as the KB doc describes — including the exact tool name and arguments.
4. The APIs do NOT enforce policy rules — YOU must check them before calling.
5. If a request is against policy OR requires escalation, deny/escalate. Do NOT just comply because the customer insists.
6. Do not proactively offer compensation unless the user explicitly asks.

## Search-first discipline — this is mandatory

**BEFORE making any mutation (write) tool call, you MUST have called KB_search for the specific procedure at least once.** The only mutations that do not require a prior KB_search are:
- `log_verification` (for identity)
- `get_*` read-only tools

Every other mutation — `change_user_email`, `transfer_to_human_agents`, any `unlock_discoverable_agent_tool`, any `give_discoverable_user_tool`, any `close_*_NNNN`, any `submit_*_NNNN`, any `update_*_NNNN`, any `file_*_NNNN`, any `order_*_NNNN`, any `transfer_*_NNNN`, any `open_*_NNNN`, any `apply_*_NNNN`, any `freeze_*_NNNN`, any `activate_*_NNNN`, any `change_*_NNNN`, any `pay_*_NNNN`, any `initial_*_NNNN` — **requires** a targeted KB_search first. Skip this and you will almost certainly fail.

**Search queries should be specific:** use the product name, the procedure name, the customer-reported problem verbatim. Examples:
- "change email address dispute procedure"
- "payment not reflecting customer transfer"
- "cash back rewards discrepancy dispute"
- "Silver Rewards Card cash back dispute"

If the first search is unproductive, try 2-3 different phrasings — but avoid repeating the same concept more than twice.

**Escalation patterns to recognize (search KB for the exact pattern):**
- Customer insists on account info that contradicts the DB (e.g., says "my email is X" but DB has Y) → possible account_ownership_dispute
- Payment confirmed but not reflecting / balance discrepancy / funds missing → possible escalation scenario
- Customer reports fraud, unauthorized activity, stolen card → dispute/freeze procedures
- Customer wants to do something themselves → give_discoverable_user_tool, not unlock_discoverable_agent_tool

## Discoverable tool workflow (CRITICAL)

Your initial tool list contains two categories of tools:

1. BASE tools — always available:
   - `get_user_information_by_id` / `by_name` / `by_email`
   - `get_credit_card_accounts_by_user`, `get_credit_card_transactions_by_user`, `get_referrals_by_user`
   - `log_verification` — writes to verification_history (don't call twice)
   - `change_user_email` — only for legitimate email changes, NOT disputed ownership
   - `transfer_to_human_agents` — last resort; many tasks prefer a discoverable `initial_transfer_to_human_agent_NNNN` variant first
   - `get_current_time`

2. DISCOVERY meta-tools — always available, used to activate ACTION tools:
   - `list_discoverable_agent_tools()` — shows what's currently unlocked for you
   - `unlock_discoverable_agent_tool(agent_tool_name="...")` — activates an action tool for YOU (the agent) to call
   - `give_discoverable_user_tool(discoverable_tool_name="...")` — activates an action tool for the USER to call
   - `call_discoverable_agent_tool(agent_tool_name="...", arguments="...")` — invokes a previously unlocked tool. **`arguments` is a JSON-ENCODED STRING, not a dict. Pass `'{"key":"value"}'` not `{"key":"value"}`.**

ACTION TOOLS (like `submit_cash_back_dispute_0589`, `update_transaction_rewards_3847`, `initial_transfer_to_human_agent_0218`) are NOT in your initial tool list. They are only mentioned inside KB_search results by their exact name — lowercase underscores followed by a 4+ digit suffix.

The canonical sequence:
1. KB_search → read results, extract the EXACT tool name (copy-paste; do not retype)
2. Decide who performs the action:
   - KB prose says "the customer submits/must/should", "have the customer", "the user submits" → `give_discoverable_user_tool(discoverable_tool_name="...")`
   - Otherwise → `unlock_discoverable_agent_tool(agent_tool_name="...")`
3. After unlocking, call the tool by its exact name (framework exposes it as a regular tool after unlock, OR use `call_discoverable_agent_tool`)

**Hard rules:**
- NEVER guess tool names. Always copy them EXACTLY from KB_search results.
- NEVER call a discoverable tool before unlocking/giving it — it will error.
- NEVER unlock a tool for the agent when the KB doc says the customer performs the action.
- NEVER both `unlock_discoverable_agent_tool` AND `give_discoverable_user_tool` for the same tool in the same task — pick one based on the KB wording.
- NEVER make an "extra" tool call (e.g., a second log_verification, or unlocking a tool you don't actually use) — every mutation is hashed and extras fail the task.

## Minimalism — strict exact-match scoring

This benchmark grades by comparing your final database state against the oracle's. Every call is hashed. This means:
- Extra `log_verification` → fail
- Extra unlock → fail
- Wrong variant of a tool (e.g., `activate_debit_card_8292` when oracle expected `_8291`) → fail
- Wrong enum argument value → fail

Do exactly what the task requires. Nothing more, nothing less. Do not guess which variant of a tool to call — look at the docstring (after unlocking, it contains the enum constraints) and match the customer's situation carefully.

## Key practices
- First identify the user (`get_user_information_by_*`) and verify identity (`log_verification`, ONCE).
- If the customer provides info, verify it matches the DB before assuming it's correct.
- Read KB results carefully — every procedure has constraints and tool names embedded.
- Use exact values from tool results (IDs, dates, amounts). Do not guess or approximate.
- When the user confirms, proceed immediately — do not ask for confirmation twice.
- Finish the ENTIRE procedure before reporting back. Many tasks have 3-15 expected actions.
- Keep user-facing messages concise.
""".strip()

SYSTEM_TEMPLATE = """
<instructions>
{instructions}
</instructions>
<policy>
{policy}
</policy>
""".strip()

# ── ANNOTATOR ─────────────────────────────────────────────────────────────────
# PRIMARY OPTIMIZATION LEVER: evolve this based on failure traces.
# Additions should be additive. Do not remove existing annotations without
# strong evidence from traces that they hurt.

# Matches lowercase_underscored_name followed by 4+ digit suffix
# e.g. submit_cash_back_dispute_0589, update_transaction_rewards_3847
_DISCOVERABLE_TOOL_PATTERN = re.compile(r'\b([a-z][a-z_]{3,}_\d{4,})\b')

# Phrases indicating the customer (not the agent) performs the action
_USER_ACTION_INDICATORS = [
    "the customer submits",
    "the customer must",
    "the customer should",
    "the user submits",
    "the user must",
    "the user should",
    "have the customer",
    "ask the customer to",
    "have the user",
    "ask the user to",
]


# Enum values are cited in KB docs with single quotes, typically in
# parameter descriptions: "dispute_reason must be one of: 'unauthorized_fraudulent_charge', 'duplicate_charge', ..."
_ENUM_PATTERN = re.compile(r"'([a-z][a-z_]{2,30})'")


def annotate_banking(content: str, state: dict | None = None) -> str:
    """Surface discoverable tools and procedure requirements from KB_search results.

    This is the PRIMARY optimization lever. Swarm agents should evolve this
    function based on trace diagnosis. Read traces/latest.json, find the most
    common failure class, and add an annotation that surfaces the missing
    signal. Additions are additive.

    Args:
        content: the raw tool result content from KB_search (or any ToolMessage)
        state: optional CustomAgent._task_state dict. When provided, the
            annotator reads fields like `unlocked_for_agent` / `unlocked_for_user`
            to produce state-aware notes (ALREADY UNLOCKED vs STILL TO UNLOCK,
            etc.). When state is None, all annotations remain stateless so
            this function is safe to call from tests or tools.

    Annotations:
    1. Discoverable tool name extraction → reminds agent to unlock before calling
       (state-aware: splits into ALREADY UNLOCKED / ALREADY GIVEN / STILL TO UNLOCK)
    2. User-facing action detection → reminds agent to use give_* not unlock_*
    3. Identity verification requirement → reminds agent to call log_verification
    4. Multi-step procedure detection → prevents stopping partway
    5. Cross-reference detection → suggests follow-up KB_search
    6. Enum value extraction → highlights exact-match arg constraints
    7. Escalation/dispute triggers → recognize tasks that need policy-level action
    """
    if not content:
        return content

    annotations = []
    content_lower = content.lower()

    # 1. Extract discoverable tool names mentioned in doc prose
    tool_mentions = sorted(set(_DISCOVERABLE_TOOL_PATTERN.findall(content)))
    if tool_mentions:
        # State-aware split: show the agent what's already unlocked vs still needed
        unlocked_agent: set = (state or {}).get("unlocked_for_agent", set()) or set()
        unlocked_user: set = (state or {}).get("unlocked_for_user", set()) or set()

        still_to_unlock = [t for t in tool_mentions if t not in unlocked_agent and t not in unlocked_user]
        already_unlocked_agent = [t for t in tool_mentions if t in unlocked_agent]
        already_given_user = [t for t in tool_mentions if t in unlocked_user]

        parts = []
        if still_to_unlock:
            parts.append(
                f"STILL TO UNLOCK: {', '.join(still_to_unlock)}\n"
                f"Before calling any of these, call EXACTLY ONE of:\n"
                f"  unlock_discoverable_agent_tool(agent_tool_name=\"<exact_name>\")     [agent performs the action]\n"
                f"  give_discoverable_user_tool(discoverable_tool_name=\"<exact_name>\")  [customer performs the action]"
            )
        if already_unlocked_agent:
            parts.append(
                f"ALREADY UNLOCKED (for agent): {', '.join(already_unlocked_agent)} — "
                f"do NOT unlock again; just call the tool."
            )
        if already_given_user:
            parts.append(
                f"ALREADY GIVEN (to user): {', '.join(already_given_user)} — "
                f"do NOT ALSO unlock for agent; the customer will call it."
            )
        if parts:
            annotations.append("\n\n".join(parts))

        # Detect if the doc says the customer performs the action
        for indicator in _USER_ACTION_INDICATORS:
            if indicator in content_lower:
                annotations.append(
                    "USER-FACING ACTION DETECTED: the doc says the customer performs "
                    "the action. Use `give_discoverable_user_tool(discoverable_tool_name=...)` "
                    "(NOT unlock_discoverable_agent_tool) for tools on this page."
                )
                break

    # 2. Flag identity verification requirements — but warn if already verified
    if ("verify" in content_lower and "identity" in content_lower) or "log_verification" in content:
        already_verified = bool((state or {}).get("verified_user_ids"))
        if already_verified:
            annotations.append(
                "VERIFICATION: this procedure mentions identity verification. "
                "You have ALREADY called log_verification this task — do NOT call it again. "
                "Extra log_verification calls cause DB mismatch and fail the task."
            )
        else:
            annotations.append(
                "VERIFICATION REQUIRED: this procedure requires identity verification. "
                "Call log_verification ONCE before any account mutation tool. "
                "Ask the customer for 2 of: date of birth, email, phone, address."
            )

    # 3. Detect multi-step procedures
    step_markers = re.findall(
        r'(?:step\s*\d|first,|then,|finally,|next,)',
        content_lower
    )
    if len(step_markers) >= 3:
        annotations.append(
            "MULTI-STEP PROCEDURE: this document describes a sequence of actions. "
            "Execute ALL steps in order — do NOT stop partway. Many tasks have "
            "3-15 required actions; stopping after the first one fails the task."
        )

    # 4. Flag cross-references to other docs
    if "see also" in content_lower or "refer to" in content_lower:
        annotations.append(
            "CROSS-REFERENCE: this doc references another procedure. "
            "Consider an additional KB_search for the referenced topic before acting."
        )

    # 5. Surface enum constraints — arg matching is EXACT
    # Look specifically for "one of:" or "must be one of" contexts
    enum_match = re.search(
        r"(?:one of|must be|values?):?\s*([^.\n]{10,300})",
        content_lower,
    )
    if enum_match:
        # Pull out all single-quoted values in the matched region
        region = enum_match.group(1)
        enums = _ENUM_PATTERN.findall(region)
        if len(enums) >= 2:
            annotations.append(
                f"ENUM CONSTRAINT: this doc specifies exact string values: "
                f"{', '.join(repr(e) for e in enums[:8])}. "
                f"Pass these EXACTLY — arg matching is strict equality."
            )

    # 6. Escalation / dispute trigger phrases
    escalation_triggers = [
        ("account ownership", "account_ownership_dispute → transfer_to_human_agents(reason=account_ownership_dispute)"),
        ("ownership dispute", "account_ownership_dispute → transfer_to_human_agents(reason=account_ownership_dispute)"),
        ("not reflected", "payment-not-reflecting may require initial_transfer_to_human_agent_NNNN procedure"),
        ("not reflecting", "payment-not-reflecting may require initial_transfer_to_human_agent_NNNN procedure"),
        ("cannot be resolved", "check for a discoverable escalation tool via KB_search"),
        ("escalate to", "check for a discoverable escalation tool before calling base transfer_to_human_agents"),
    ]
    for phrase, note in escalation_triggers:
        if phrase in content_lower:
            annotations.append(f"ESCALATION SIGNAL: '{phrase}' detected — {note}")
            break

    if annotations:
        return content + "\n\n--- AGENT NOTES ---\n" + "\n\n".join(annotations)
    return content


# ── MESSAGE CONVERSION ────────────────────────────────────────────────────────

def to_api_messages(messages, annotator=None):
    """Convert tau2 message objects to OpenAI-style dicts.

    If annotator is provided, it is called on tool message content before
    the message is passed to the LLM. This is the hook that makes
    annotate_banking() effective. The annotator callback takes (content: str)
    and returns str. Callers that want to pass state should wrap the
    annotator in a closure: `lambda c: annotate_banking(c, state=self._task_state)`.
    """
    out = []
    for m in messages:
        if isinstance(m, SystemMessage):
            out.append({"role": "system", "content": m.content})
        elif isinstance(m, UserMessage):
            out.append({"role": "user", "content": m.content})
        elif isinstance(m, AssistantMessage):
            d = {"role": "assistant", "content": m.content or ""}
            if m.is_tool_call():
                d["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ]
            out.append(d)
        elif isinstance(m, ToolMessage):
            content = m.content if m.content else ""
            if annotator:
                content = annotator(content)
            out.append({"role": "tool", "content": content, "tool_call_id": m.id})
    return out


def parse_response(choice):
    """Convert an LLM API response choice into a tau2 AssistantMessage."""
    tool_calls = None
    if choice.tool_calls:
        parsed = []
        for tc in choice.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            parsed.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        tool_calls = parsed if parsed else None
    return AssistantMessage(
        role="assistant",
        content=choice.content or "",
        tool_calls=tool_calls or None,
    )


# ── AGENT ─────────────────────────────────────────────────────────────────────

MAX_RETRIES = 3
LOOP_BREAK_LIMIT = 5  # Force text response after N consecutive tool calls to break search loops


class BankingAgentState:
    """Per-task conversation state for CustomAgent.

    Decoupled from τ²-bench internals so future API changes don't force us
    to refactor — we just need fields our generate_next_message uses.
    """

    def __init__(
        self,
        system_messages: list,
        messages: Optional[list] = None,
    ):
        self.system_messages = list(system_messages)
        self.messages = list(messages) if messages else []


class CustomAgent(HalfDuplexAgent[BankingAgentState]):
    """Self-contained banking knowledge customer service agent.

    The class provides three extension points for swarm agents to build on
    WITHOUT refactoring the foundation:

    1. `_task_state` dict — neutral per-task state container, reset on every
       new task. The base scaffold populates five fields (turn_count,
       tool_call_ledger, last_tool_result_by_name, mentioned_in_kb,
       verified_user_ids) via `_track_state()`. Agents add whatever
       additional fields they need.

    2. `_gate_tool_calls(msg)` — a NO-OP hook by default. Agents fill it in
       with interception/rewrite logic (e.g. verification gating, unlock
       enforcement, argument correction).

    3. `annotate_banking(content, state=...)` — the tool-result annotator
       now receives `self._task_state` via closure. Agents add state-aware
       annotations (e.g. "ALREADY UNLOCKED: [...]", "VERIFIED: yes").

    Nothing in this base class enforces any specific priority — it only
    measures facts and exposes hooks. See program.md for the priority
    framework agents use to pick what to work on.
    """

    def __init__(
        self,
        tools: list[Tool],
        domain_policy: str,
        llm: Optional[str] = None,
        llm_args: Optional[dict] = None,
    ):
        super().__init__(tools=tools, domain_policy=domain_policy)
        self.llm = llm or os.environ.get("SOLVER_MODEL", "gpt-4.1-mini")
        self.llm_args = dict(llm_args or {})
        self._consecutive_tool_calls = 0
        self._task_state: dict = {}
        self._reset_task_state()

    @property
    def system_prompt(self) -> str:
        return SYSTEM_TEMPLATE.format(
            instructions=BASE_INSTRUCTIONS,
            policy=self.domain_policy,
        )

    # ── state management ────────────────────────────────────────────────

    def _reset_task_state(self) -> None:
        """Initialize per-task state. Reset on every new task via get_init_state().

        This is a NEUTRAL container — no fields are interpreted or enforced by
        the base agent. Agents in the swarm decide what to put here and how to
        use it. The keys below are suggestions populated by the base
        `_track_state()`, not requirements. Agents extend freely.
        """
        self._task_state = {
            "turn_count": 0,
            "tool_call_ledger": [],         # append-only list of {name, args, turn}
            "last_tool_result_by_name": {}, # tool name -> parsed JSON of latest result
            "mentioned_in_kb": set(),       # discoverable tool names seen in KB results
            "verified_user_ids": set(),     # populated when log_verification succeeds
            "unlocked_for_agent": set(),    # names unlocked via unlock_discoverable_agent_tool
            "unlocked_for_user": set(),     # names given via give_discoverable_user_tool
            "kb_search_count": 0,           # how many KB_search calls have been made
            "gate_interventions": [],       # log of _gate_tool_calls rewrites (for debugging)
        }
        self._consecutive_tool_calls = 0

    def _track_state(self, incoming, assistant_msg) -> None:
        """Record facts from this turn into _task_state. Does NOT intervene.

        Facts recorded (base scaffold — stays neutral):
        - turn_count incremented
        - Every outgoing tool call appended to tool_call_ledger
        - log_verification calls update verified_user_ids
        - Incoming tool result JSON parsed into last_tool_result_by_name
          (keyed by the most recent tool call name as a best-effort match)
        - Discoverable tool names from KB results accumulated into mentioned_in_kb

        Agents extend this method (or write directly to self._task_state from
        annotate_banking / _gate_tool_calls) to track additional facts for
        their priority implementation.
        """
        self._task_state["turn_count"] = self._task_state.get("turn_count", 0) + 1

        # Log outgoing tool calls
        if assistant_msg is not None and assistant_msg.tool_calls:
            for tc in assistant_msg.tool_calls:
                args = tc.arguments if isinstance(tc.arguments, dict) else {}
                self._task_state["tool_call_ledger"].append({
                    "name": tc.name,
                    "args": args,
                    "turn": self._task_state["turn_count"],
                })
                # Record meta-tool outcomes (assumed successful at call time;
                # the next tool result will reveal if it actually worked)
                if tc.name == "log_verification":
                    user_id = args.get("user_id")
                    if user_id:
                        self._task_state["verified_user_ids"].add(user_id)
                elif tc.name == "unlock_discoverable_agent_tool":
                    target = args.get("agent_tool_name") or args.get("tool_name")
                    if target:
                        self._task_state["unlocked_for_agent"].add(target)
                elif tc.name == "give_discoverable_user_tool":
                    target = args.get("discoverable_tool_name") or args.get("tool_name")
                    if target:
                        self._task_state["unlocked_for_user"].add(target)
                elif tc.name in ("KB_search", "kb_search", "search_knowledge_base"):
                    self._task_state["kb_search_count"] += 1

        # Parse incoming tool results and accumulate KB mentions
        tool_messages = []
        if isinstance(incoming, ToolMessage):
            tool_messages = [incoming]
        elif isinstance(incoming, MultiToolMessage):
            tool_messages = list(incoming.tool_messages)

        for tm in tool_messages:
            content = tm.content or ""
            # Accumulate discoverable tool mentions from KB results
            for name in _DISCOVERABLE_TOOL_PATTERN.findall(content):
                self._task_state["mentioned_in_kb"].add(name)
            # Parse JSON result if possible; pair with most recent tool call by position
            try:
                parsed = json.loads(content)
                if self._task_state["tool_call_ledger"]:
                    last_call = self._task_state["tool_call_ledger"][-1]
                    self._task_state["last_tool_result_by_name"][last_call["name"]] = parsed
            except (json.JSONDecodeError, TypeError):
                pass

    def _gate_tool_calls(self, assistant_msg: AssistantMessage) -> AssistantMessage:
        """Minimally-invasive rewrites to prevent known deterministic failures.

        Every τ³-bench task is graded by DB hash equality. This means EVERY
        extra tool call is a potential task-killer. This gate therefore only
        rewrites when the current call is guaranteed-wrong given current state
        and the rewrite is strictly safer.

        Current interventions:

        A. DROP the duplicate-meta-tool pattern
           If the LLM tries to `unlock_discoverable_agent_tool(X)` for a tool
           already in `unlocked_for_user`, OR vice versa, drop that specific
           call. This is an observed failure mode (task_100): the LLM gave a
           tool to the user AND then unlocked it for itself, producing a
           `wasted_unlocks` record that breaks db_match. Dropping one of the
           two sides is strictly better because the chosen side still fires.

        B. DROP redundant re-unlock / re-give
           If the LLM tries to unlock/give a tool already in the corresponding
           set, drop it. Dupes waste turns and can fail db_match.

        C. FIX dict-shaped `arguments` on `call_discoverable_agent_tool`
           τ²-bench requires the `arguments` parameter to be a JSON-encoded
           STRING, not a dict. LiteLLM may silently pass `{}`, causing the
           target tool to effectively no-op. We JSON-encode on the fly.

        The gate NEVER adds calls the LLM didn't request, and NEVER changes
        tool names. It only removes redundant calls and reformats arguments.
        All interventions are logged to `_task_state["gate_interventions"]`.
        """
        if not assistant_msg.tool_calls:
            return assistant_msg

        unlocked_agent = self._task_state.get("unlocked_for_agent", set())
        unlocked_user = self._task_state.get("unlocked_for_user", set())
        kept: list[ToolCall] = []
        log = self._task_state.setdefault("gate_interventions", [])
        turn = self._task_state.get("turn_count", 0)

        for tc in assistant_msg.tool_calls:
            args = tc.arguments if isinstance(tc.arguments, dict) else {}
            name = tc.name

            # Intervention A/B: deduplicate unlock/give
            if name == "unlock_discoverable_agent_tool":
                target = args.get("agent_tool_name") or args.get("tool_name")
                if target and target in unlocked_user:
                    log.append({
                        "turn": turn,
                        "reason": "dropped_unlock_already_given_to_user",
                        "target": target,
                    })
                    continue
                if target and target in unlocked_agent:
                    log.append({
                        "turn": turn,
                        "reason": "dropped_redundant_unlock",
                        "target": target,
                    })
                    continue
            elif name == "give_discoverable_user_tool":
                target = args.get("discoverable_tool_name") or args.get("tool_name")
                if target and target in unlocked_agent:
                    log.append({
                        "turn": turn,
                        "reason": "dropped_give_already_unlocked_for_agent",
                        "target": target,
                    })
                    continue
                if target and target in unlocked_user:
                    log.append({
                        "turn": turn,
                        "reason": "dropped_redundant_give",
                        "target": target,
                    })
                    continue

            # Intervention C: JSON-encode dict-shaped `arguments`
            if name == "call_discoverable_agent_tool" and isinstance(args, dict):
                inner = args.get("arguments")
                if isinstance(inner, dict):
                    fixed = dict(args)
                    fixed["arguments"] = json.dumps(inner)
                    tc = ToolCall(id=tc.id, name=tc.name, arguments=fixed)
                    log.append({
                        "turn": turn,
                        "reason": "json_encoded_dict_arguments",
                        "target": args.get("agent_tool_name", "?"),
                    })

            kept.append(tc)

        # If we dropped everything, return an empty text response so the
        # orchestrator advances to the next turn. Empty tool_calls with
        # content="" is legal — the LLM re-tries on the next turn.
        if not kept and assistant_msg.tool_calls:
            return AssistantMessage(
                role="assistant",
                content=assistant_msg.content or "(pending)",
                tool_calls=None,
            )

        if kept != assistant_msg.tool_calls:
            return AssistantMessage(
                role="assistant",
                content=assistant_msg.content or "",
                tool_calls=kept or None,
            )
        return assistant_msg

    # ── main loop ────────────────────────────────────────────────────────

    def get_init_state(
        self,
        message_history: Optional[list[Message]] = None,
    ) -> BankingAgentState:
        # tau2-bench calls this once per task — perfect place to reset state
        self._reset_task_state()
        return BankingAgentState(
            system_messages=[SystemMessage(role="system", content=self.system_prompt)],
            messages=list(message_history or []),
        )

    def generate_next_message(
        self,
        message: ValidAgentInputMessage,
        state: BankingAgentState,
    ) -> tuple[AssistantMessage, BankingAgentState]:
        # 1. Append incoming message(s) to conversation history
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        elif isinstance(message, UserMessage):
            self._consecutive_tool_calls = 0  # reset loop counter on user input
            state.messages.append(message)
        else:
            state.messages.append(message)

        # 2. Build API request with stateful annotator applied to tool results
        api_messages = to_api_messages(
            state.system_messages + state.messages,
            annotator=lambda c: annotate_banking(c, state=self._task_state),
        )
        api_tools = [t.openai_schema for t in self.tools] if self.tools else None

        # 3. Break search loops — force text response after too many consecutive tool calls
        if api_tools and self._consecutive_tool_calls >= LOOP_BREAK_LIMIT:
            tool_choice = "none"
        elif api_tools:
            tool_choice = "auto"
        else:
            tool_choice = None

        # 4. Call LLM with retry logic
        for attempt in range(MAX_RETRIES):
            try:
                response = completion(
                    model=self.llm,
                    messages=api_messages,
                    tools=api_tools,
                    tool_choice=tool_choice,
                    **self.llm_args,
                )
                break
            except Exception:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

        # 5. Parse response
        assistant_msg = parse_response(response.choices[0].message)

        # 6. Gate hook — swarm agents put rewrite rules here. NO-OP by default.
        assistant_msg = self._gate_tool_calls(assistant_msg)

        # 7. Track consecutive tool calls for the loop breaker
        if assistant_msg.tool_calls:
            self._consecutive_tool_calls += 1
        else:
            self._consecutive_tool_calls = 0

        # 8. Record facts from this turn into _task_state (neutral, no intervention)
        self._track_state(message, assistant_msg)

        state.messages.append(assistant_msg)
        return assistant_msg, state

    def set_seed(self, seed: int):
        self.llm_args["seed"] = seed


# ── FACTORY ───────────────────────────────────────────────────────────────────
# τ²-bench v1.0.0 uses a factory-function registration pattern.
# The factory signature is: factory(tools, domain_policy, **kwargs) -> agent.

def create_custom_agent(
    tools: list[Tool],
    domain_policy: str,
    llm: Optional[str] = None,
    llm_args: Optional[dict] = None,
    **kwargs,
) -> CustomAgent:
    """Factory function used by tau2's registry.register_agent_factory."""
    return CustomAgent(
        tools=tools,
        domain_policy=domain_policy,
        llm=llm,
        llm_args=llm_args,
    )
