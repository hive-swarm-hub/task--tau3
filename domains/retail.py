"""Retail domain agent — evolved from τ² experiments.

Note: retail annotations were REMOVED in τ² exp12 because they hurt telecom.
Keeping it clean — no annotator here.
"""

from domains.base import BaseAgent


class RetailAgent(BaseAgent):
    ANNOTATOR = None  # removed in tau2 exp12 — annotations hurt other domains
    LOOP_BREAK_LIMIT = None  # retail needs consecutive tool calls

    DOMAIN_INSTRUCTIONS = """
- Authenticate the user by email or name+zip code first, even if they provide a user ID.
- Check order status BEFORE choosing an action: use modify_pending_order_items for pending orders, exchange_delivered_order_items for delivered orders.
- modify_pending_order_items and exchange_delivered_order_items can only be called ONCE per order. Collect ALL items to change into a single call. Remind the user to confirm all items before proceeding.
- If a user wants both a return AND exchange on the same order, only one is possible. Ask which they prefer.
- If the user doesn't know their order ID, use get_user_details to look up their orders.
- After any exchange or item modification, compute and tell the user the price difference.
- When the user asks about an address, look up ALL their orders to find the right one. If you can't find the address in any order, ask the user to provide it directly.
""".strip()
