"""τ³-bench agent router — dispatches to domain-specific agents.

The meta-agent modifies domain files in domains/, not this file.
This router detects the domain from the policy text and instantiates
the appropriate domain agent.

Structure:
  agent.py              <- this router (thin, rarely changed)
  domains/
    base.py             <- shared logic (changes require full 4-domain eval)
    airline.py          <- evolved from τ² (0.76 best)
    retail.py           <- evolved from τ²
    telecom.py          <- evolved from τ²
    banking.py          <- clean baseline from τ³ (highest leverage)
"""

from tau2.environment.tool import Tool

from domains.airline import AirlineAgent
from domains.retail import RetailAgent
from domains.telecom import TelecomAgent
from domains.banking import BankingAgent


def detect_domain(policy: str) -> str:
    """Detect the domain from the policy text."""
    lower = policy.lower()
    if "airline" in lower and "reservation" in lower and "flight" in lower:
        return "airline"
    elif "retail" in lower and "pending" in lower and "delivered" in lower:
        return "retail"
    elif "telecom" in lower:
        return "telecom"
    elif "banking" in lower or "knowledge" in lower:
        return "banking_knowledge"
    return "unknown"


DOMAIN_AGENTS = {
    "airline": AirlineAgent,
    "retail": RetailAgent,
    "telecom": TelecomAgent,
    "banking_knowledge": BankingAgent,
}


class CustomAgent:
    """Router that delegates to domain-specific agents.

    τ²-bench calls __init__ with (tools, domain_policy), then calls
    get_init_state() and generate_next_message(). We detect the domain
    and forward everything to the right agent class.
    """

    def __init__(self, tools: list[Tool], domain_policy: str, llm=None, llm_args=None):
        domain = detect_domain(domain_policy)
        agent_cls = DOMAIN_AGENTS.get(domain, BankingAgent)
        self._agent = agent_cls(
            tools=tools,
            domain_policy=domain_policy,
            llm=llm,
            llm_args=llm_args,
        )

    def get_init_state(self, message_history=None):
        return self._agent.get_init_state(message_history)

    def generate_next_message(self, message, state):
        return self._agent.generate_next_message(message, state)

    def set_seed(self, seed: int):
        self._agent.set_seed(seed)
