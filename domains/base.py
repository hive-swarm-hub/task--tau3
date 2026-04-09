"""Shared agent logic for τ³-bench — message conversion, LLM calling, retry, parsing.

This module is the frozen base. Changes here affect ALL domains, so any
modification must pass the full 4-domain eval before being kept.
"""

import json
import os
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

MAX_RETRIES = 3

SYSTEM_TEMPLATE = """
<instructions>
{instructions}
</instructions>
<policy>
{policy}
</policy>
""".strip()


def to_api_messages(messages, annotator=None):
    """Convert tau2 message objects to OpenAI-style dicts."""
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


class BaseAgent(LLMAgent):
    """Base agent with shared LLM calling logic. Domain agents subclass this."""

    # Subclasses override these
    DOMAIN_INSTRUCTIONS = ""
    ANNOTATOR = None
    LOOP_BREAK_LIMIT = None  # set to int to force text after N consecutive tool calls

    def __init__(self, tools: list[Tool], domain_policy: str, llm=None, llm_args=None):
        LocalAgent.__init__(self, tools=tools, domain_policy=domain_policy)
        self.llm = llm or os.environ.get("SOLVER_MODEL", "gpt-4.1-mini")
        self.llm_args = dict(llm_args or {})
        self._consecutive_tool_calls = 0

    def get_base_instructions(self) -> str:
        return """
You are a customer service agent. You MUST follow the <policy> exactly. The policy is your sole source of truth — never invent rules, procedures, or information not in the policy or provided by the user.

## Critical rules
1. Each turn: EITHER send a message to the user OR make a tool call. NEVER both at the same time.
2. Only make ONE tool call per turn.
3. Before any action that modifies the database (booking, modifying, cancelling), you MUST:
   a. Verify all policy preconditions are met (eligibility, rules, restrictions).
   b. List the exact action details to the user and get explicit confirmation.
   c. Only then make the tool call.
4. The APIs do NOT enforce policy rules — YOU must check them before calling.
5. If a request is against policy, deny it and explain why.
6. Transfer to a human agent ONLY if the request cannot be handled within the scope of your actions. To transfer: first call transfer_to_human_agents, then send "YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON."
7. Do not proactively offer compensation unless the user explicitly asks.

## Key practices
- First identify the user (get user ID).
- Gather all needed information using tools before taking action.
- Always look up CURRENT prices/availability — never reuse prices from old reservations.
- Check every policy rule that applies to the situation before calling an API.
- Use exact values from tool results (IDs, dates, amounts). Do not guess or approximate.
- When the user confirms, proceed immediately — do not ask for confirmation again.
- Keep responses concise.
""".strip()

    @property
    def system_prompt(self) -> str:
        instructions = self.get_base_instructions()
        if self.DOMAIN_INSTRUCTIONS:
            instructions += "\n\n## Domain-specific rules\n" + self.DOMAIN_INSTRUCTIONS
        return SYSTEM_TEMPLATE.format(instructions=instructions, policy=self.domain_policy)

    def get_init_state(self, message_history=None) -> LLMAgentState:
        return LLMAgentState(
            system_messages=[SystemMessage(role="system", content=self.system_prompt)],
            messages=list(message_history or []),
        )

    def generate_next_message(self, message: ValidAgentInputMessage, state: LLMAgentState):
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        elif isinstance(message, UserMessage):
            self._consecutive_tool_calls = 0
            state.messages.append(message)
        else:
            state.messages.append(message)

        api_messages = to_api_messages(
            state.system_messages + state.messages,
            annotator=self.ANNOTATOR,
        )
        api_tools = [t.openai_schema for t in self.tools] if self.tools else None

        if api_tools and self.LOOP_BREAK_LIMIT and self._consecutive_tool_calls >= self.LOOP_BREAK_LIMIT:
            tool_choice = "none"
        elif api_tools:
            tool_choice = "auto"
        else:
            tool_choice = None

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
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

        assistant_msg = parse_response(response.choices[0].message)
        if assistant_msg.tool_calls:
            self._consecutive_tool_calls += 1
        else:
            self._consecutive_tool_calls = 0

        state.messages.append(assistant_msg)
        return assistant_msg, state

    def set_seed(self, seed: int):
        self.llm_args["seed"] = seed
