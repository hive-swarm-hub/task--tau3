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

from litellm import completion

from tau2.agent.base import LocalAgent, ValidAgentInputMessage
from tau2.agent.llm_agent import LLMAgent, LLMAgentState
from tau2.data_model.message import (
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
3. Before any action that modifies the database (opening disputes, ordering cards, transfers, etc.), you MUST:
   a. Verify all policy preconditions are met (eligibility, rules, restrictions).
   b. List the exact action details to the user and get explicit confirmation.
   c. Only then make the tool call.
4. The APIs do NOT enforce policy rules — YOU must check them before calling.
5. If a request is against policy, deny it and explain why.
6. Transfer to a human agent ONLY if the request cannot be handled within the scope of your actions. To transfer: call transfer_to_human_agents with the appropriate reason.
7. Do not proactively offer compensation unless the user explicitly asks.

## Knowledge retrieval
- You have access to KB_search to find relevant banking procedures and policies in the knowledge base.
- ALWAYS search the knowledge base when you encounter a question or situation not directly covered by the base policy.
- Use specific, targeted search queries. Search for the product or procedure name, not vague terms.
- Read search results carefully — they contain the exact procedures you must follow, AND may mention discoverable tool names you must unlock (see below).
- If the first search does not find what you need, try different search terms. Avoid searching the same concept more than twice.

## Discoverable tool workflow (CRITICAL)

Your initial tool list contains two categories of tools:

1. BASE tools — always available:
   - get_user_information_by_id / get_user_information_by_name / get_user_information_by_email
   - log_verification
   - transfer_to_human_agents
   - get_current_time

2. DISCOVERY meta-tools — always available, used to activate ACTION tools:
   - list_discoverable_agent_tools — shows what's currently unlocked for you
   - unlock_discoverable_agent_tool — activates an action tool for YOU (the agent) to call
   - give_discoverable_user_tool — activates an action tool for the USER to call
   - call_discoverable_agent_tool — invokes a previously unlocked agent tool

ACTION TOOLS (like submit_cash_back_dispute_0589, update_transaction_rewards_3847, close_account_1234, transfer_funds_5678, etc.) are NOT in your initial tool list. They are only mentioned inside KB_search results by their exact name. You MUST:

1. Search KB → read results carefully for tool names matching patterns like submit_*_NNNN, update_*_NNNN, close_*_NNNN, transfer_*_NNNN, file_*_NNNN (lowercase underscored name followed by 4+ digits)
2. Before calling a discoverable tool, call unlock_discoverable_agent_tool(tool_name="exact_name_from_docs") to activate it for YOU (the agent)
3. OR call give_discoverable_user_tool(tool_name="exact_name_from_docs") to activate it for the USER — use this when the KB doc says the customer performs the action ("the customer submits...", "have the customer...", "the user must...")
4. After unlocking, call the tool by its exact name via call_discoverable_agent_tool or as a regular tool call
5. If unsure what's currently available to you, call list_discoverable_agent_tools() first

NEVER guess tool names. Always copy them EXACTLY from KB_search results.
NEVER call a discoverable tool before unlocking/giving it — it will error with tool_not_found.
NEVER unlock a tool for the agent when the KB doc clearly says the customer must perform the action.

## Key practices
- First identify the user (get user ID or verify identity via log_verification).
- Gather all needed information using tools before taking action.
- Always look up CURRENT account details — never assume.
- Check every policy rule that applies to the situation before calling an API.
- Use exact values from tool results (IDs, dates, amounts). Do not guess or approximate.
- When the user confirms, proceed immediately — do not ask for confirmation again.
- Keep responses concise.
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


def annotate_banking(content: str, state: dict | None = None) -> str:
    """Surface discoverable tools and procedure requirements from KB_search results.

    This is the PRIMARY optimization lever. Swarm agents should evolve this
    function based on trace diagnosis. Read traces/latest.json, find the most
    common failure class, and add an annotation that surfaces the missing
    signal. Additions are additive.

    Args:
        content: the raw tool result content from KB_search (or any ToolMessage)
        state: optional CustomAgent._task_state dict. The base scaffold does NOT
            use state — all existing annotations are stateless. Agents add
            state-aware annotations by reading fields like
            state["unlocked_for_agent"] and state["verified_user_ids"] to
            build context-dependent notes (e.g. "ALREADY UNLOCKED: [...]"
            vs "STILL TO UNLOCK: [...]"). The signature accepts state so
            swarm agents can extend annotations without changing the caller.

    Current annotations (stateless, carry over from baseline):
    1. Discoverable tool name extraction → reminds agent to unlock before calling
    2. User-facing action detection → reminds agent to use give_* not unlock_*
    3. Identity verification requirement → reminds agent to call log_verification
    4. Multi-step procedure detection → prevents stopping partway
    5. Cross-reference detection → suggests follow-up KB_search
    """
    if not content:
        return content

    annotations = []

    # 1. Extract discoverable tool names mentioned in doc prose
    tool_mentions = sorted(set(_DISCOVERABLE_TOOL_PATTERN.findall(content)))
    if tool_mentions:
        tools_list = ", ".join(tool_mentions)
        annotations.append(
            f"DISCOVERABLE TOOLS MENTIONED: {tools_list}\n"
            f"Before calling any of these, you MUST call:\n"
            f"  unlock_discoverable_agent_tool(tool_name=\"<exact_name>\")  [agent performs the action]\n"
            f"  OR give_discoverable_user_tool(tool_name=\"<exact_name>\")  [customer performs the action]"
        )

        # Detect if the doc says the customer performs the action
        content_lower = content.lower()
        for indicator in _USER_ACTION_INDICATORS:
            if indicator in content_lower:
                annotations.append(
                    "USER-FACING ACTION DETECTED: the doc says the customer performs "
                    "the action. Use give_discoverable_user_tool (NOT unlock_discoverable_agent_tool) "
                    "for tools on this page."
                )
                break

    # 2. Flag identity verification requirements
    content_lower = content.lower()
    if ("verify" in content_lower and "identity" in content_lower) or "log_verification" in content:
        annotations.append(
            "VERIFICATION REQUIRED: this procedure requires identity verification. "
            "Call log_verification before any account mutation tool."
        )

    # 3. Detect multi-step procedures
    step_markers = re.findall(
        r'(?:step\s*\d|first,|then,|finally,|next,)',
        content_lower
    )
    if len(step_markers) >= 3:
        annotations.append(
            "MULTI-STEP PROCEDURE: this document describes a sequence of actions. "
            "Execute ALL steps in order. Do NOT stop partway."
        )

    # 4. Flag cross-references to other docs
    if "see also" in content_lower or "refer to" in content_lower:
        annotations.append(
            "CROSS-REFERENCE: this doc references another procedure. "
            "Consider an additional KB_search for the referenced topic before acting."
        )

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


class CustomAgent(LLMAgent):
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

    def __init__(self, tools: list[Tool], domain_policy: str, llm=None, llm_args=None):
        LocalAgent.__init__(self, tools=tools, domain_policy=domain_policy)
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
            # swarm agents may add fields like:
            # "unlocked_for_agent": set(),
            # "unlocked_for_user": set(),
            # "kb_searches": [],
            # "argument_corrections": [],
            # "procedure_checklist": [],
            # ... whatever their priority implementation needs
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
                self._task_state["tool_call_ledger"].append({
                    "name": tc.name,
                    "args": tc.arguments if isinstance(tc.arguments, dict) else {},
                    "turn": self._task_state["turn_count"],
                })
                # Neutral: note when log_verification fires
                if tc.name == "log_verification" and isinstance(tc.arguments, dict):
                    user_id = tc.arguments.get("user_id")
                    if user_id:
                        self._task_state["verified_user_ids"].add(user_id)

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
        """Hook for intercepting/rewriting tool calls before they execute.

        By default this is a NO-OP — it returns assistant_msg unchanged.
        Swarm agents implement rewrite rules here based on their failure traces.

        Common patterns agents may choose to implement (examples, NOT mandatory):
        - Priority 1: rewrite mutation tool calls to log_verification if unverified
        - Priority 1: rewrite discoverable tool calls to unlock calls if not unlocked
        - Priority 2: rewrite argument values if they disagree with known_values
        - ...

        When you rewrite, log the intervention to self._task_state["...":"..."]
        so traces show why the rewrite fired and agents reading traces can
        diagnose over/under-interception.
        """
        return assistant_msg

    # ── main loop ────────────────────────────────────────────────────────

    def get_init_state(self, message_history=None) -> LLMAgentState:
        # tau2-bench calls this once per task — perfect place to reset state
        self._reset_task_state()
        return LLMAgentState(
            system_messages=[SystemMessage(role="system", content=self.system_prompt)],
            messages=list(message_history or []),
        )

    def generate_next_message(self, message: ValidAgentInputMessage, state: LLMAgentState):
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
