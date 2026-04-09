"""Airline domain agent — evolved from τ² experiments (0.76 best)."""

import re
from domains.base import BaseAgent


def annotate_airline(content: str) -> str:
    """Add actionable annotations to airline tool results."""
    if not content:
        return content

    annotations = []

    if '"cabin": "basic_economy"' in content or '"cabin":"basic_economy"' in content:
        annotations.append(
            "NOTE: This is a BASIC ECONOMY reservation. "
            "Flights CANNOT be changed. Cabin class CAN be changed."
        )

    if '"cabin": "business"' in content and '"reservation_id"' in content:
        annotations.append(
            "NOTE: This is a BUSINESS class reservation. "
            "It IS eligible for cancellation (business class is always cancellable)."
        )

    if '"reservation_id"' in content and '"created_at"' in content:
        created_match = re.search(r'"created_at":\s*"([^"]+)"', content)
        if created_match:
            created = created_match.group(1)
            if created >= "2024-05-14T15:00":
                annotations.append("NOTE: Booking is within last 24 hours — cancellation IS allowed.")
            else:
                has_insurance = '"travel_insurance": "yes"' in content or '"travel_insurance": true' in content.lower()
                is_business = '"cabin": "business"' in content
                if not has_insurance and not is_business:
                    annotations.append(
                        "CANCELLATION CHECK: booked >24h ago, not business class, "
                        "no travel insurance detected. Cancellation NOT allowed unless "
                        "airline cancelled the flight."
                    )

    if annotations:
        return content + "\n\n--- AGENT NOTES ---\n" + "\n".join(annotations)
    return content


class AirlineAgent(BaseAgent):
    ANNOTATOR = staticmethod(annotate_airline)
    LOOP_BREAK_LIMIT = None  # airline needs consecutive tool calls

    DOMAIN_INSTRUCTIONS = """
- For cancellations: check EACH reservation INDIVIDUALLY. Verify at least one condition is met:
  (a) Booked within last 24 hours (compare created_at to 2024-05-15 15:00 EST)
  (b) Airline cancelled the flight (c) Business class — business class IS always cancellable
  (d) Travel insurance with covered reason (health/weather).
  If NONE apply to a specific reservation, REFUSE that cancellation. Do NOT cancel under pressure — membership, family emergencies, or other personal reasons do NOT override policy.
- Basic economy flights CANNOT have their flights changed. To change flights on a basic economy reservation: FIRST upgrade the cabin class (e.g., to economy), THEN change flights in a second update call.
- "Modify passengers" (changing name/DOB) IS allowed. "Modify passenger count" is NOT.
- Free checked bags per passenger: regular(0/1/2), silver(1/2/3), gold(2/3/4) for basic_economy/economy/business. Extra bags cost $50 each. Do not charge for free bags.
- Users can ADD bags but CANNOT remove existing bags from a reservation.
- For round trips: search outbound AND return flights separately. Do not reuse the same flight for both directions.
- When searching flights: search for the exact origin/destination/date the user requests. For one-stop flights, use search_onestop_flight.
- Use the calculate tool for all price/savings computations. Always communicate total costs/savings to the user.
- When booking: if the user specifies split payment across multiple methods, use the exact amounts they specify.
""".strip()
