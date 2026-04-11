# Compass Framework Reusability Audit — τ²-bench v1.0.0

**Report Date:** 2026-04-11  
**Scope:** Verify Kimi's claim that only `banking_knowledge` uses `@is_discoverable_tool`, assess generalizability of the compass framework across τ²-bench domains.

---

## Executive Summary

Kimi's claim is **correct and material**: only `banking_knowledge` uses `@is_discoverable_tool` at all. The other four τ²-bench domains (airline, retail, telecom, mock) expose **all tools from turn 0** with `@is_tool` decorators, requiring no discovery mechanism. 

This means the **AST catalog parser** in `compass.py` (the most distinctive feature of the framework) effectively becomes banking-only infrastructure. However, four other components—scenario playbooks, canonicalization hooks, prompt rendering, and the plug-in system—show genuine cross-domain applicability **in principle**, though they require each domain to adopt similar patterns to realize that value.

**Verdict:** The refactor is organizationally useful for separation of concerns, but the "any domain can plug in" framing is **oversold**. The framework solves a banking-specific problem (graded tool discovery). Other domains could theoretically benefit from scenario playbooks and argument canonicalization, but none currently implement those patterns.

---

## 1. Discoverable-Tool Usage Across Domains

### Findings

| Domain | File | `@is_discoverable_tool` count | `@is_tool` count | Total Tools |
|--------|------|------|------|------|
| **banking_knowledge** | `tools.py` | 49 | 20 | 69 (agent) |
| **airline** | `tools.py` | 0 | 15 | 15 |
| **retail** | `tools.py` | 0 | 17 | 17 |
| **telecom** | `tools.py` + `user_tools.py` | 0 | 43 | 43 |
| **mock** | `tools.py` + `user_tools.py` | 0 | 7 | 7 |

### File-Level Evidence

- **banking_knowledge/tools.py**: Lines 701, 710, 747, 760, 773, 786, 915, 1118, 1163, 1199, 1371, 1474, 1510, 1550, 1602, 1642, 1729, 1827, 1877, 1969, 2035, 2077, 2137, 2268, 2337 (first 25 of 49 `@is_discoverable_tool` decorators shown; full count: 49).
- **airline/tools.py**: Zero `@is_discoverable_tool` decorators. All tools use `@is_tool(ToolType.READ/WRITE/GENERIC)`.
- **retail/tools.py**: Zero `@is_discoverable_tool` decorators. All tools use `@is_tool(ToolType.READ/WRITE/GENERIC)`.
- **telecom/tools.py + user_tools.py**: Zero `@is_discoverable_tool` decorators. All tools use `@is_tool(ToolType.READ/WRITE/GENERIC)`.
- **mock/tools.py + user_tools.py**: Zero `@is_discoverable_tool` decorators. All tools use `@is_tool(ToolType.READ/WRITE/GENERIC)`.

### Conclusion

**Kimi's claim is 100% correct.** The `@is_discoverable_tool` pattern is used **only and exclusively** in `banking_knowledge`. All other domains immediately expose their entire tool set. This invalidates the implicit assumption that the AST catalog parser (`ToolCompass._parse_catalog`) would be reusable across domains.

---

## 2. Tool Count Comparison

| Domain | Total Tool Count | Discoverable Count | Discovery Rate |
|--------|-----|------|------|
| airline | 15 | 0 | 0% |
| retail | 17 | 0 | 0% |
| telecom | 43 | 0 | 0% |
| mock | 7 | 0 | 0% |
| **banking_knowledge** | **69** | **49** | **71%** |

**Key insight:** Banking_knowledge is the largest domain (69 tools) and **only banking_knowledge** gates tool access. The 49 discoverable tools represent a real problem: the agent must search 45 KB docs (out of 698 total) to find tool names, then unlock them. Other domains—even the large telecom domain (43 tools)—avoid this problem entirely by exposing all tools immediately.

---

## 3. Scenario Playbook Applicability

The compass framework includes `match_scenario_playbook()` and `render_playbook_for_prompt()` (compass.py, lines 724–756) designed to detect and guide the agent through known-trap scenarios. Currently only `banking_knowledge` has scenario playbooks defined in `compass_banking.py` (line 163: `SCENARIO_PLAYBOOKS`), with one entry: the "11/13 backend incident" (lines 164–202).

### Analysis of Other Domains

**Airline (policy.md lines 1–100):**
- Core trap: **cabin class consistency** — all flights in a reservation must use the same cabin class (line 69).
- Core trap: **baggage allowance tiers** — depends on membership level (silver/gold) and cabin class, with complex rules for free-vs-paid baggage (lines 83–94).
- Core trap: **payment method combinations** — max 1 travel certificate, max 1 credit card, max 3 gift cards per reservation (line 78).
- *Scenario playbookability: HIGH.* These are explicit, deterministic rules that trap agents into invalid sequences. Examples:
  1. "Customer wants to book premium cabin on a multi-leg trip but accidentally mixes economy on the return" → playbook detects mismatch and corrects.
  2. "Customer wants to add 5 checked bags but agent forgets to apply membership discount" → playbook detects overpayment.
  3. "Customer tries to pay with 2 travel certificates" → playbook detects and rejects.

**Retail (policy.md lines 1–100):**
- Core trap: **authentication required upfront** — agent must look up user by email OR name+zip before ANY action (lines 10–14).
- Core trap: **single-call-per-action constraint** — "modify order tools can only be called once per order. Be sure all items to be changed are collected into a list before making the tool call" (line 84).
- Core trap: **order status constraints** — can only cancel/modify pending orders, return/exchange delivered orders (lines 86, 94).
- *Scenario playbookability: MEDIUM.* These are procedural (not branching scenarios), but the single-call constraint could be enforced by a playbook that collects all modifications before unlocking the tool.
  1. "Customer wants to change shipping address AND payment method on the same order" → playbook collects both, calls modify once.
  2. "Customer tries to cancel a delivered order" → playbook detects status mismatch and proposes return/exchange instead.

**Telecom (main_policy.md lines 1–100):**
- Core trap: **line-level operations** — telecom has "lines" (phone numbers, plans, devices) separate from the customer account. Customer lookup can be by phone, ID, or name+DOB, and each lookup method may return different results.
- Core trap: **payment extension limits** — customer has a `last_extension_date` (line 32) and finite "goodwill credit usage for the year" (line 33).
- Core trap: **device/SIM management** — device has eSIM capability (line 74) and transfer history (`last_eSIM_transfer_date`, line 75); agent must not exceed transfer limits.
- *Scenario playbookability: MEDIUM-HIGH.* Device SIM management and payment extension limits are perfect for playbooks.
  1. "Customer requests an eSIM transfer but already transferred this year" → playbook detects and denies.
  2. "Customer requests a payment extension but already extended once this billing cycle" → playbook detects and proposes goodwill credit instead.

**Conclusion:** All three domains have **explicit, catchable trap scenarios** that could benefit from playbook-style guidance. Telecom and airline are especially strong candidates. However, **no other domain currently implements any scenario playbooks in their codebase**, meaning this feature is latent, not demonstrated as cross-domain.

---

## 4. Argument Canonicalization Applicability

The `compass_banking.py` file defines `canonicalize_log_verification_args()` (lines 80–106) that normalizes brittle string formats:

- `time_verified` → `"YYYY-MM-DD HH:MM:SS EST"` (line 77)
- `date_of_birth` → `"MM/DD/YYYY"` with leading zeros (line 100–134)
- `phone_number` → `"XXX-XXX-XXXX"` (line 137–150)

### Analysis of Other Domains

**Airline tools:**
- `book_reservation()`: Takes `flights: List[FlightInfo | dict]` and `cabin: CabinClass` enum.
- `search_direct_flight()`: Takes `date: str` (format not strictly validated in docstring).
- `update_reservation_passengers()`: Takes `passengers: List[Passenger | dict]` with nested `Passenger` objects containing DOB.
- *Canonicalization candidates:* `date` parameter across `search_direct_flight`, `search_onestop_flight`, `update_reservation_passengers` (nested); passenger DOB within the Passenger struct.
- **Risk level: MEDIUM.** Date formats could drift (e.g., "2024-05-15" vs. "05/15/2024"); passenger DOB is nested and harder to normalize.

**Retail tools:**
- `modify_pending_order_address()`: Takes `address1: str, address2: str, city: str, state: str, zip: str`.
- `modify_user_address()`: Same address fields.
- `get_customer_by_id()`, `get_customer_by_email()`: Take `email: str` or `user_id: str` (less brittle).
- *Canonicalization candidates:* Email normalization (lowercase, trim whitespace); zip code validation (leading zeros? numeric?). Address fields are free-form and have less structured risk.
- **Risk level: LOW-MEDIUM.** Email is a common canonicalization target. Zip codes could drift (e.g., "02134" vs. "2134").

**Telecom tools:**
- `get_customer_by_phone()`: Takes `phone_number: str`, expects matching against stored `customer.phone_number`.
- `get_customer_by_name()`: Takes `full_name: str, dob: str` with exact format `"YYYY-MM-DD"` (from docstring, line ~107 in tools.py).
- `pay_bill()`: Takes `amount: str` or `amount: float` (would need to check call sites).
- *Canonicalization candidates:* `phone_number` (same as banking's normalization: XXX-XXX-XXXX); `dob` (currently YYYY-MM-DD, but agents might produce MM/DD/YYYY or other formats); `amount` (string vs. float coercion).
- **Risk level: MEDIUM-HIGH.** Phone and DOB are identical to banking's brittle fields. Amount as float could lose cents precision.

**Conclusion:** All three domains have **documented string format requirements** in their tool signatures and policy docs. Telecom and airline would benefit from DOB and phone normalization identical to banking's. Retail could benefit from email and zip normalization. However, **none of them implement any canonicalization hooks**, and the refactor provides no mechanism for them to register domain-specific canonicalizers (the shims in compass.py lines 859–900 hard-code banking extension lookups).

---

## 5. The Broader Framework Value Prop

Analyzing each component's applicability:

| Component | Generic? | Banking-Adjacent? | Evidence |
|-----------|----------|---------|----------|
| **ToolCompass catalog parser (AST-based)** | ❌ No | ✅ Yes | Requires `@is_discoverable_tool` decorators; only banking_knowledge uses them. Parsing would fail silently or return empty for other domains. |
| **match_scenario_playbook / render_playbook_for_prompt** | ✅ Yes | ✅ Yes | Logic is domain-agnostic (keyword matching, sequence validation). But no other domain implements scenarios yet. |
| **canonicalize_json_args** | ✅ Yes | ❌ No | Generic JSON round-tripping for argument serialization. Useful for any domain but not specific enough to address the string-format canonicalization problem. |
| **validate_tool_name / suggest_tools** | ❌ No | ✅ Yes | Depends on the catalog parser, which is banking-only. Suggests tools via scenario keywords only if catalog is populated. |
| **render_prompt_section** | ❌ No | ✅ Yes | Renders catalog to system prompt; only works if catalog is non-empty. Would produce blank output for non-banking domains. |
| **register_extension / get_extension / has_extension** | ✅ Yes | ❌ No | Generic plug-in system, but compass.py hard-codes banking extension lookups in compatibility shims (lines 859–900). Other domains would need to rewrite the shims. |
| **SCENARIO_PLAYBOOKS data structure** | ✅ Yes | ✅ Yes | Plain dict structure; any domain can define playbooks. But requires each domain to implement scenario detection logic separately. |

### Detailed Breakdown

**Generic (work for any domain):**
- `match_scenario_playbook(text, playbooks)`: Takes arbitrary dict and does keyword matching. Fully reusable if a domain writes its own playbook dict.
- `canonicalize_json_args(value)`: Pure JSON utility, independent of domain.
- Plug-in registration API: `register_extension(name, obj)` and `get_extension(name)` are generic, but the backwards-compat shims (lines 859–900) hard-code banking lookups.

**Banking-adjacent (only useful if another domain adopts banking's pattern):**
- `ToolCompass` catalog parser: Requires the `@is_discoverable_tool` pattern; no other domain uses it.
- `validate_tool_name`, `suggest_tools`, `render_prompt_section`: All depend on a populated catalog, which only exists for banking.
- The extension system becomes generic only **if** each domain reimplements the backwards-compat shims. As-is, the shims are banking-specific (e.g., `canonicalize_log_verification_args` checks `hasattr(ext, "canonicalize_log_verification_args")` on line 872, assuming banking-style canonicalization exists).

---

## 6. Is the Refactor Still Worth Keeping?

**Answer:** Yes, but reframe the value prop as **organizational clarity and maintainability**, not cross-domain catalog reuse.

### Specific Reasoning

1. **Separation of Concerns (Real Win):**
   - `compass.py` is genuinely generic: AST parsing, scenario dispatch, prompt rendering, plug-in infrastructure.
   - `compass_banking.py` is genuinely domain-specific: banking playbooks, DOB/phone/time normalization, dispute calculator, rate tables.
   - A developer can now understand banking-specific logic without wading through framework scaffolding, and vice versa.

2. **Scenario Playbooks and Canonicalization are Latent Value (Not Yet Realized):**
   - The playbook system and canonicalization hooks are **structurally generic**; airline or telecom *could* adopt them.
   - But they currently don't. The framework enables future adoption but doesn't demonstrate it.
   - If airline or telecom engineers want to add scenario playbooks, they'd follow the `compass_banking.py` pattern and create `compass_airline.py`. This is easier because the pattern is now visible and separated.

3. **The AST Catalog Parser is Banking-Only (Accept It):**
   - Don't market `compass.py` as a "multi-domain tool discovery framework." It's a **banking-specific catalog parser** with a generic plug-in architecture for extensions.
   - Rewrite the docstring at the top of `compass.py` (lines 1–104) to say: "Compass is a *static tool catalog and scenario dispatch framework* for τ³-bench. It's currently instantiated for banking_knowledge, which uses graded tool discovery. Other domains can register their own scenarios and canonicalizers via extensions."

4. **The Plug-In System Needs Work for True Multi-Domain Use:**
   - Currently, backwards-compat shims hard-code banking (e.g., `_banking_ext()` on line 859 assumes banking is the only extension).
   - To support airline *and* banking *and* telecom simultaneously, the shims would need to dispatch to the correct extension based on domain, e.g., `_get_domain_ext(domain_name)`.
   - As-is, the refactor is ready for **single-agent-per-domain** (banking agent uses compass_banking, airline agent uses compass_airline, etc.), but not **multi-domain-in-one-agent**.

### Conclusion

Keep the refactor. It's a net organizational win for clarity and future extensibility. But **update the messaging**: this is not a "multi-domain catalog framework." It's a "banking tool catalog + generic scenario/canonicalization hooks." The genericity is real but underdeveloped. Airline, retail, and telecom can adopt similar patterns in the future if they choose.

---

## 7. Final Recommendations

1. **Rename for clarity:** Consider `compass_catalog.py` + `compass_framework.py` instead of splitting `compass.py` into generic/domain. (Current split is fine, but name it `compass_banking_catalog.py` to be explicit.)

2. **Document the latent-genericity story:** Add a section to the refactor's docstring explaining that scenario playbooks and canonicalization are available to other domains if they follow the pattern, with an example blueprint for `compass_airline.py`.

3. **Freeze the backwards-compat shims:** The shims in `compass.py` lines 859–900 are a good idea for single-domain use but will become unmaintainable if multiple agents import them. At that point, refactor to a `compass_load_domain(domain_name)` factory function instead.

4. **Do NOT market as "any domain can plug in tools"** — the catalog parser is banking-specific. Do say: **"scenario dispatch and argument canonicalization can be extended to other domains; see compass_banking.py for a worked example."**

---

## Appendix: Detailed File Citations

- **@is_discoverable_tool decorators (banking_knowledge only):** `tau2-bench/src/tau2/domains/banking_knowledge/tools.py` lines 701, 710, 747, 760, 773, 786, 915, 1118, 1163, 1199, 1371, 1474, 1510, 1550, 1602, 1642, 1729, 1827, 1877, 1969, 2035, 2077, 2137, 2268, 2337 (and 24 more; 49 total).

- **Scenario playbooks API:** `compass.py` lines 724 (match_scenario_playbook), 756 (render_playbook_for_prompt); data in `compass_banking.py` lines 163–202.

- **Canonicalization API:** `compass.py` lines 695 (canonicalize_json_args), 864 (canonicalize_log_verification_args shim); implementation in `compass_banking.py` lines 80–150.

- **Plug-in system:** `compass.py` lines 363–381 (register_extension, get_extension, has_extension); backwards-compat shims lines 859–900.

- **Tool counts:** Verified via grep on domain `tools.py` and `user_tools.py` files in `tau2-bench/src/tau2/domains/{airline,retail,telecom,mock,banking_knowledge}/`.
