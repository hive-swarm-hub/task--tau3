"""Telecom domain agent — evolved from τ² experiments.

Key learning from τ²: the loop-breaker at 10 consecutive tool calls is CRITICAL.
Removing it caused massive regression (exp4: 50% telecom). Applying it to all domains
hurt airline/retail (exp6). So it stays telecom-only.
"""

import re
from domains.base import BaseAgent


def annotate_telecom(content: str) -> str:
    """Add actionable annotations to telecom tool results."""
    if not content:
        return content

    annotations = []

    if '"roaming_enabled": false' in content:
        annotations.append(
            "ACTION NEEDED: roaming_enabled is false on this line. "
            "If the user is traveling/abroad, call enable_roaming for this line. "
            "ALSO ask the user to toggle data roaming ON on their device."
        )

    if "roaming" in content.lower() and ('"roaming_enabled": true' in content or "enabled" in content.lower()):
        if "enable" in content.lower() and "success" in content.lower():
            annotations.append(
                "Backend roaming is now enabled. ALSO ask the user to check their "
                "device data roaming (check_network_status) and toggle it ON if needed."
            )

    used_match = re.search(r'"data_used_gb":\s*([\d.]+)', content)
    limit_match = re.search(r'"data_limit_gb":\s*([\d.]+)', content)
    if used_match and limit_match:
        used = float(used_match.group(1))
        limit = float(limit_match.group(1))
        if used > limit:
            annotations.append(
                f"ACTION NEEDED: data usage ({used}GB) EXCEEDS plan limit ({limit}GB). "
                "Offer data refueling (max 2GB) or plan change."
            )

    if '"phone_number"' in content and '"line_id"' in content:
        annotations.append(
            "REMINDER: verify this line's phone_number matches the user's phone number. "
            "If not, look up the other line IDs."
        )

    if '"locked"' in content.lower() and 'sim' in content.lower():
        annotations.append(
            "ACTION NEEDED: SIM is locked (PIN/PUK). You CANNOT fix this — "
            "you MUST call transfer_to_human_agents tool."
        )

    contract_match = re.search(r'"contract_end_date":\s*"([^"]+)"', content)
    if contract_match and '"status": "Suspended"' in content:
        contract_date = contract_match.group(1)
        if contract_date < "2025-02-25":
            annotations.append(
                f"WARNING: contract expired ({contract_date}) and line is suspended. "
                "You CANNOT resume this line — call transfer_to_human_agents tool."
            )

    if annotations:
        return content + "\n\n--- AGENT NOTES ---\n" + "\n".join(annotations)
    return content


class TelecomAgent(BaseAgent):
    ANNOTATOR = staticmethod(annotate_telecom)
    LOOP_BREAK_LIMIT = 10  # CRITICAL — tau2 exp4 proved removing this causes massive regression

    DOMAIN_INSTRUCTIONS = """
- Follow the troubleshooting workflow step by step. You CAN guide users through ALL device actions: toggling airplane mode, mobile data, data roaming, Wi-Fi calling, data saver, VPN, changing network mode preference, SIM reseating, APN reset, granting app permissions, rebooting. These are ALL within scope.
- Transfer to human ONLY for: locked SIM (PIN/PUK) or after exhausting ALL workflow steps. When transferring, you MUST call transfer_to_human_agents tool — do not just tell the user verbally.
- Line selection: the customer may have multiple lines. Match the line's phone_number to the user's phone number. Always use that specific line for all lookups and actions.
- Roaming: if the user is abroad or traveling, fix BOTH: (1) backend — call enable_roaming if roaming_enabled is false; (2) device — ask user to check_network_status and toggle_roaming ON if data roaming is disabled. Both are needed.
- Data usage: check on the CORRECT line. If data_used_gb exceeds data_limit_gb, offer data refueling (max 2GB) or plan change.
- For MMS issues, check ALL of these systematically: cellular service -> mobile data -> network mode (must be 3G+) -> Wi-Fi calling (turn OFF) -> app permissions (messaging app needs 'sms' AND 'storage') -> APN/MMSC settings. Do NOT transfer until you've checked every step.
- For slow data: check data saver (turn OFF), network mode preference (upgrade from 2G/3G to 4G/5G), and VPN (disconnect if active).
""".strip()
