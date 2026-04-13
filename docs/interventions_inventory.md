# Intervention Inventory: agent.py

This document catalogs every intervention, annotation, and extension hook embedded in the τ³-bench banking customer service agent.

## Summary Table

| ID | Name | Hook | Cluster | LOC | Target Tool(s) |
|----|------|------|---------|-----|----------------|
| A  | dedupe-unlock-agent | gate_pre | discovery | 855–869 | unlock_discoverable_agent_tool |
| B  | dedupe-give-user | gate_pre | discovery | 870–884 | give_discoverable_user_tool |
| C  | json-encode-args | gate_pre | arguments | 886–904 | call_discoverable_*_tool |
| D  | hallucination-guard | gate_pre | discovery | 824–848 | unlock/give_discoverable_*_tool |
| E  | phase2-guard | gate_pre | execution | 959–1002 | call_discoverable_agent_tool |
| F  | post-give-reminder | gate_post | execution | 1006–1060 | give_discoverable_user_tool |
| G  | canonicalize-args | gate_pre | arguments | 805–822 | log_verification |
| H  | enum-pre-validation | gate_pre | arguments | 906–957 | call_discoverable_agent_tool |
| — | annotator-tools | annotator | discovery | 277–307 | KB_search results |
| — | annotator-enum | annotator | arguments | 377–392 | KB_search results |
| — | annotator-playbook | annotator | discovery | 266–275 | KB_search results |
| — | annotator-verification | annotator | verification | 342–356 | KB_search results |
| — | annotator-multistep | annotator | execution | 358–368 | KB_search results |
| — | annotator-calculator | annotator | execution | 406–412 | KB_search results |

## Detailed Intervention Specs

### Intervention A: Deduplicate unlock (agent)
**Hook type:** `_gate_tool_calls` gate_pre  
**LOC:** 855–869  
**Target cluster:** discovery  

**Description:** When the LLM calls `unlock_discoverable_agent_tool` with a tool name already in `unlocked_for_agent` or `unlocked_for_user`, drop the redundant call and inject a note telling the LLM it already has access.

**Reads from state:**
- `unlocked_for_agent` (set of already-unlocked agent tools)
- `unlocked_for_user` (set of tools already given to user)
- `turn_count`

**Writes to state:**
- `gate_interventions[].reason = "dropped_redundant_unlock"` or `"dropped_unlock_already_given_to_user"`

**Input:** ToolCall with name="unlock_discoverable_agent_tool", args containing `agent_tool_name` or `tool_name`

**Output:** 
- Drop the call (continue without adding to `kept`)
- Append drop_note: "I already have {target} unlocked and will proceed with the call."

**Dependencies:** None (first gate intervention to fire)

---

### Intervention B: Deduplicate give (user)
**Hook type:** `_gate_tool_calls` gate_pre  
**LOC:** 870–884  
**Target cluster:** discovery  

**Description:** When the LLM calls `give_discoverable_user_tool` with a tool name already unlocked (either for agent or user), drop it. If already given to user, tell the LLM "use it now". If already unlocked for agent, tell the LLM the agent will handle it.

**Reads from state:**
- `unlocked_for_agent`
- `unlocked_for_user`
- `turn_count`

**Writes to state:**
- `gate_interventions[].reason = "dropped_give_already_unlocked_for_agent"` or `"dropped_redundant_give"`

**Input:** ToolCall with name="give_discoverable_user_tool", args containing `discoverable_tool_name` or `tool_name`

**Output:**
- Drop the call
- Append drop_note with context-specific message

**Dependencies:** None (first gate intervention)

---

### Intervention C: JSON-encode and canonicalize arguments
**Hook type:** `_gate_tool_calls` gate_pre  
**LOC:** 886–904  
**Target cluster:** arguments  

**Description:** When calling `call_discoverable_agent_tool` or `call_discoverable_user_tool`, the `arguments` field MUST be a JSON STRING (not a dict), and τ²-bench's action evaluator compares it literally. This intervention normalizes the JSON using `canonicalize_json_args()` (sorted keys, compact separators) so it matches the oracle's serialization format.

**Reads from state:**
- `turn_count`

**Writes to state:**
- `gate_interventions[].reason = "canonicalized_json_arguments"`

**Input:** ToolCall with name in ("call_discoverable_agent_tool", "call_discoverable_user_tool"), args["arguments"] being a dict

**Output:**
- Replace the ToolCall with a new one where arguments is a canonical JSON string
- Log the change

**Helper calls:** `canonicalize_json_args(inner)` from compass module

**Dependencies:** None (pure format transformation)

---

### Intervention D: Hallucination guard
**Hook type:** `_gate_tool_calls` gate_pre  
**LOC:** 824–848  
**Target cluster:** discovery  

**Description:** If the LLM tries to unlock/give a discoverable tool name that does NOT exist in the static catalog (checked via `_VALID_DISCOVERABLE_NAMES`), drop it. This prevents confusing "Error: Unknown discoverable tool X" errors that would degrade the LLM; instead, inject a corrective note telling the LLM to consult the catalog in its system prompt.

**Reads from state:**
- `turn_count`

**Writes to state:**
- `gate_interventions[].reason = "dropped_hallucinated_tool_name"`

**Input:** ToolCall with name in ("unlock_discoverable_agent_tool", "give_discoverable_user_tool"), args["agent_tool_name/discoverable_tool_name/tool_name"] not in `_VALID_DISCOVERABLE_NAMES`

**Output:**
- Drop the call
- Append drop_note: "The tool name '{target}' does not exist in the discoverable tool catalog..."

**Helper calls:** Checks `_VALID_DISCOVERABLE_NAMES` (from `COMPASS.valid_names`)

**Dependencies:** Only fires when catalog is non-empty (i.e., tau2-bench source is available)

---

### Intervention E: Phase-2 guard
**Hook type:** `_gate_tool_calls` gate_pre  
**LOC:** 959–1002  
**Target cluster:** execution  

**Description:** When the agent is about to call an agent-side mutation tool, check if it pairs with a still-pending user-side tool. The domain extension (banking) provides `phase2_pairs`: a map from user tools to agent-tool prefixes that should be blocked until the customer has called the user tool. Classic case: agent gives `submit_cash_back_dispute_0589`, customer submits only 1 dispute, agent immediately calls `update_transaction_rewards_*` to clean up — DB mismatch fails the task. This blocks that pattern by checking `user_calls_by_tool[given_tool] == 0`.

**Reads from state:**
- `user_calls_by_tool` (counts of user-side tool executions)
- `unlocked_for_user` (tools given to customer)
- `turn_count`

**Writes to state:**
- `gate_interventions[].reason = "blocked_phase2_before_user_call"`

**Input:** ToolCall with name="call_discoverable_agent_tool", args["agent_tool_name"] matching a phase2_pairs prefix

**Output:**
- Drop the call (continue without adding to kept)
- Append drop_note: "I gave you the tool {given_tool} earlier — please call it with the specific transaction details first..."

**Helper calls:** `_BANKING_EXT.phase2_pairs` property (from compass_banking.py)

**Dependencies:** 
- Requires domain extension registered (no-op when `_BANKING_EXT is None`)
- Reads state written by annotator and _track_state (user_calls_by_tool population)

---

### Intervention F: Post-give reminder (targeted disputes)
**Hook type:** `_gate_tool_calls` gate_post  
**LOC:** 1006–1060  
**Target cluster:** execution  

**Description:** After all gate interventions, when a `give_discoverable_user_tool` call was KEPT (not dropped), append a reminder to the assistant content. This nudges the agent to follow up with specific argument values the customer needs. Phase D upgrade: if the banking extension has cached dispute candidates for the current user (from a prior `get_credit_card_transactions_by_user` call), surface the EXACT transaction_ids identified by the offline calculator, bypassing the LLM's fragile arithmetic.

**Reads from state:**
- `unlocked_for_user` (to find which tools were given)
- `current_user_id` (to look up dispute candidates)
- `dispute_candidates_by_user` (Phase D cache from extension hook)
- `transactions_by_user` (fallback id-only cache)

**Writes to state:** None (read-only)

**Input:** ToolCall list after all drops/rewrites, filtered for name="give_discoverable_user_tool"

**Output:**
- Append a templated note to `give_notes` list
- At the end, inject `give_notes` into the final AssistantMessage content

**Helper calls:**
- `_BANKING_EXT.get_dispute_candidates(state, uid)` → list[dict]
- `_BANKING_EXT.format_dispute_targets_message(target, candidates, uid)` → Optional[str]
- `_BANKING_EXT.format_give_fallback_message(target, state, uid)` → Optional[str]

**Dependencies:**
- Fires AFTER A/B/C/D/E (post-processing of kept calls)
- Depends on state populated by extension hooks in _track_state (disputes_by_user, transactions_by_user)

---

### Intervention G: Canonicalize log_verification arguments
**Hook type:** `_gate_tool_calls` gate_pre  
**LOC:** 805–822  
**Target cluster:** arguments  

**Description:** When the LLM calls `log_verification`, normalize fields to match τ²-bench's expected formats. Banking oracle stores records keyed on (user_id, time_verified) with deterministic string formats. This intervention rewrites `time_verified`, `date_of_birth`, and `phone_number` to the exact formats the oracle expects (e.g., "MM/DD/YYYY" with leading zeros for DOB).

**Reads from state:**
- `turn_count`

**Writes to state:**
- `gate_interventions[].reason = "canonicalized_log_verification"`

**Input:** ToolCall with name="log_verification", args being a dict

**Output:**
- Replace ToolCall with normalized arguments
- Log changed_keys

**Helper calls:** `canonicalize_log_verification_args(args)` from compass_banking module

**Dependencies:** None (self-contained format normalization)

---

### Intervention H: Enum pre-validation
**Hook type:** `_gate_tool_calls` gate_pre  
**LOC:** 906–957  
**Target cluster:** arguments  

**Description:** τ²-bench's action matcher scores the FIRST call attempt only. If the LLM sends an invalid enum value (e.g., `account_type="premium"` when only "checking", "savings" are valid), the task is marked failed even if the LLM retries with the correct value later. This closes the detect→enforce gap by blocking out-of-set enum values at the gate and injecting a correction note. Enum constraints come from two sources: (1) docstring "one of ..." clauses parsed by `COMPASS.enum_constraints()`, and (2) banking-specific constraints (e.g., account_class conditional on account_type) from `_BANKING_EXT.extra_enum_constraints()`.

**Reads from state:**
- `turn_count`

**Writes to state:**
- `gate_interventions[].reason = "blocked_enum_violation"`

**Input:** ToolCall with name="call_discoverable_agent_tool", args["arguments"] being a JSON string

**Output:**
- Drop the call if enum violations detected
- Append drop_note with exact valid values and guidance to "retry with a valid value"

**Helper calls:**
- `COMPASS.enum_constraints(tool_name)` → {param: [valid_values]}
- `_BANKING_EXT.extra_enum_constraints(tool_name, inner_str)` → additional constraints (if registered)

**Dependencies:**
- Reads from extension if registered; no-op when `_BANKING_EXT is None`
- Independent of other gate interventions

---

## Annotator-Level Annotations

The `annotate_banking()` function (LOC 234–447) is the PRIMARY optimization lever. It runs between τ²-bench's KB_search retriever and the LLM, modifying every ToolMessage content before the LLM sees it. These are NOT labeled "# Intervention" but are conceptually equivalent.

### Annotation 1: Tool name extraction + state-aware unlock hints
**LOC:** 277–307  
**Reads from state:**
- `unlocked_for_agent`, `unlocked_for_user`

**Output:** Three-part note showing:
- Tools STILL TO UNLOCK (with code example for unlock/give)
- Tools ALREADY UNLOCKED (agent-side)
- Tools ALREADY GIVEN (user-side)

**Cluster:** discovery

---

### Annotation 2: User-action detection
**LOC:** 309–340  
**Reads from state:**
- `unlocked_for_user`

**Pattern:** Looks for `_USER_ACTION_INDICATORS` phrases ("the customer submits", etc.)  
Also checks if mentioned tools are in the user-side catalog (`_DISCOVERABLE_CATALOG["user"]`)

**Output:** Reminder that customer performs the action + explicit guidance to use `give_discoverable_user_tool` NOT unlock_*

**Cluster:** discovery

---

### Annotation 3: Scenario playbook match
**LOC:** 266–275  
**Reads from state:**
- `scenario_playbook` (matched by _track_state on first UserMessage)

**Output:** Calls `render_playbook_for_prompt(pb)` to surface the exact required action sequence

**Helper calls:** `render_playbook_for_prompt()` from compass module

**Cluster:** discovery

**Note:** This annotation fires on EVERY tool result once a playbook is matched, keeping the directive visible until executed.

---

### Annotation 4: Verification requirement
**LOC:** 342–356  
**Reads from state:**
- `verified_user_ids`

**Pattern:** Detects "verify" + "identity" or "log_verification" in content

**Output:** 
- If already verified: "You have ALREADY called log_verification — do NOT call it again"
- If not yet verified: "Call log_verification ONCE before any account mutation tool"

**Cluster:** verification

---

### Annotation 5: Multi-step procedure detection
**LOC:** 358–368  
**Pattern:** Regex for "step 1", "first,", "then,", "finally,", etc. (≥3 matches trigger)

**Output:** "MULTI-STEP PROCEDURE: Execute ALL steps in order — do NOT stop partway"

**Cluster:** execution

---

### Annotation 6: Enum constraint extraction
**LOC:** 377–392  
**Pattern:** Regex for "one of:" or "must be" followed by quoted values

**Output:** "ENUM CONSTRAINT: this doc specifies exact string values: ..."

**Cluster:** arguments

---

### Annotation 7: Cross-reference detection
**LOC:** 370–375  
**Pattern:** "see also" or "refer to"

**Output:** "CROSS-REFERENCE: this doc references another procedure. Consider an additional KB_search"

**Cluster:** execution

---

### Annotation 8: Dispute calculator ready (Phase D)
**LOC:** 406–412  
**Reads from state:**
- `dispute_candidates_by_user`
- `unlocked_for_user`

**Helper calls:** `_BANKING_EXT.format_calculator_ready_annotation(state, unlocked_for_user)`

**Output:** "DISPUTE CALCULATOR READY: I have analyzed {uid}'s transactions and identified N with incorrect cash back..." when candidates are cached but tool not yet given

**Cluster:** execution

**Note:** Domain-specific formatter lives on extension; no-op when `_BANKING_EXT is None`

---

### Annotation 9: User-tool compliance status
**LOC:** 414–429  
**Reads from state:**
- `user_calls_by_tool` (populated by _track_state)
- `unlocked_for_user`

**Output:** "USER TOOL STATUS:\n  - {tool1}: customer has called it N time(s)\n  ..."

**Cluster:** execution

**Note:** Gives LLM real-time visibility into how many submissions have landed

---

### Annotation 10: Escalation trigger phrases
**LOC:** 431–443  
**Pattern:** Looks for phrases like "account ownership", "not reflected", "cannot be resolved"

**Output:** "ESCALATION SIGNAL: '{phrase}' detected — {action_note}"

**Cluster:** execution

---

## Extension Hooks

The banking-specific extension (`compass_banking.BankingExtension`) plugs into the agent via method calls at five points:

### Hook 1: hook_on_tool_call
**Called by:** `_track_state()` at LOC 681 for each outgoing tool call

**Method signature:** `hook_on_tool_call(tool_name: str, args: dict, state: dict) -> None`

**Banking behavior:** If tool_name is in `{"get_user_information_by_id", "get_credit_card_transactions_by_user"}`, extract `user_id` from args and set `state["current_user_id"]`

**Output:** Modifies `state` in-place

---

### Hook 2: hook_on_tool_result
**Called by:** `_track_state()` at LOC 724 when a tool result lands

**Method signature:** `hook_on_tool_result(tool_name: str, content: str, current_user_id: str, state: dict) -> None`

**Banking behavior:** When tool_name is `"get_credit_card_transactions_by_user"`:
1. Regex-scan for transaction_ids and cache in `state["transactions_by_user"][user_id]`
2. Parse structured records and run dispute calculator: `compute_dispute_candidates(records)`
3. Cache results in `state["dispute_candidates_by_user"][user_id]`

**Outputs:** Populates state with:
- `transactions_by_user` (id-only cache for fallback)
- `transaction_records_by_user` (parsed records)
- `dispute_candidates_by_user` (structured candidates sorted by drift)

---

### Hook 3: extra_enum_constraints
**Called by:** Intervention H at LOC 926–929 after `COMPASS.enum_constraints()`

**Method signature:** `extra_enum_constraints(tool_name: str, inner_args_str: str) -> dict[str, list[str]]`

**Banking behavior:** When tool_name is `"open_bank_account_4821"`:
- Parse the JSON arguments to extract `account_type`
- Look up valid `account_class` values from KB-mined mapping (`_ACCOUNT_CLASS_MAP`)
- Return `{"account_class": [valid_values]}`

**Output:** Dict of additional constraint overrides

---

### Hook 4: format_dispute_targets_message
**Called by:** Intervention F at LOC 1036 when dispute candidates exist

**Method signature:** `format_dispute_targets_message(given_tool: str, candidates: list[dict], user_id: str) -> Optional[str]`

**Banking behavior:** When given_tool is `"submit_cash_back_dispute_0589"` and candidates exist:
- Format as "DISPUTE TARGETS IDENTIFIED (N transactions)" 
- List top 8 candidates with drift details
- Return exact format: `submit_cash_back_dispute_0589(user_id="...", transaction_id="...")`

**Output:** Multi-line string with specific txn_ids and format, or None

---

### Hook 5: format_calculator_ready_annotation
**Called by:** Annotator at LOC 408 on every tool result

**Method signature:** `format_calculator_ready_annotation(state: dict, unlocked_for_user: set) -> Optional[str]`

**Banking behavior:** When cached dispute candidates exist and the dispute tool has NOT yet been given:
- Format as "DISPUTE CALCULATOR READY: I have analyzed {uid}'s transactions..."
- List top 6 transaction_ids
- Return None if already given or no candidates

**Output:** High-priority nudge string or None

---

## Dispatch Order in _gate_tool_calls

The interventions fire in this sequence (LOC 790–1086):

1. **Intervention G** (log_verification args canonicalization) — LOC 813–822
2. **Intervention D** (hallucination guard) — LOC 831–848
3. **Intervention A/B** (deduplicate unlock/give) — LOC 855–884
4. **Intervention C** (JSON-encode args) — LOC 892–904
5. **Intervention H** (enum pre-validation) — LOC 919–957
6. **Intervention E** (phase-2 guard) — LOC 979–1002
7. *Collect kept calls*
8. **Intervention F** (post-give reminder) — LOC 1019–1060
9. *Return modified AssistantMessage*

**Critical ordering insights:**
- G fires first: canonicalize formats before any validation
- D fires early: detect hallucinations before dedup logic
- A/B run together: both affect unlock/give dedup
- C transforms args before H validates them
- H only fires after C normalizes JSON
- E checks phase-2 pairs only on kept agent-side calls
- F runs post-collection: only reminders, never filters calls

**What breaks if order changes:**
- Swapping G and D: hallucination guard might see non-canonical time_verified, but D doesn't inspect args, so low impact
- Swapping D and A/B: dedup might skip a hallucinated tool name, then it gets dropped by D anyway — idempotent
- Swapping C and H: H validates before C normalizes JSON, might reject canonical JSON as invalid — **BREAKS**
- Swapping H and E: low impact (independent constraints)
- Moving F before collection: F only operates on kept calls, would try to access calls that don't exist — **BREAKS**

---

## Dependency Graph

```
_track_state() 
  ├─→ sets verified_user_ids, unlocked_for_agent, unlocked_for_user
  ├─→ sets tool_call_ledger, mentioned_in_kb
  ├─→ hook_on_tool_call → sets current_user_id
  └─→ hook_on_tool_result → sets transactions_by_user, dispute_candidates_by_user

annotate_banking(state) 
  ├─ reads: unlocked_for_agent, unlocked_for_user, verified_user_ids, scenario_playbook
  ├─ reads: dispute_candidates_by_user, transactions_by_user (Phase D)
  └─ reads: user_calls_by_tool (for compliance status)

_gate_tool_calls(assistant_msg)
  ├─ G: reads nothing from state
  ├─ D: reads nothing from state (checks against _VALID_DISCOVERABLE_NAMES)
  ├─ A/B: reads unlocked_for_agent, unlocked_for_user
  ├─ C: reads nothing from state
  ├─ H: reads nothing from state (calls COMPASS.enum_constraints + extension.extra_enum_constraints)
  ├─ E: reads user_calls_by_tool, unlocked_for_user (from prior _track_state)
  └─ F: reads current_user_id, dispute_candidates_by_user, transactions_by_user
```

**State write dependencies:**
- _gate_tool_calls writes `gate_interventions` log (read-only by agent for debugging)
- Interventions A-H never modify state fields used by later interventions
- Intervention F is purely annotative (modifies returned content, not state)

---

## Surprises & Gotchas

1. **Hallucination guard (D) fires after dedup (A/B) logic doesn't apply — order is D then A/B, not A/B then D.** This means the agent could unlock a hallucinated tool, it gets dropped by D, but dedup A/B doesn't see it. The correct order is D first (verify name exists), THEN deduplicate. Currently it's G→D→A/B, which is correct but worth noting.

2. **Intervention F uses TWO separate formatting paths.** When `format_dispute_targets_message()` returns None (e.g., not a dispute tool), it falls back to `format_give_fallback_message()` which uses the id-only cache. If that also returns None, it uses a generic reminder. The layering is intentional (structured → fallback → generic) but the code path is deep.

3. **Phase2_pairs mapping is bidirectional.** The mapping is `{user_tool: agent_prefixes}`, and E checks "if target_tool starts with any prefix". This allows one user tool to block multiple agent tools, which is why cash-back disputes block both `update_transaction_rewards_*` AND potentially others. The set of pairs is domain-specific and lives on the extension.

4. **JSON canonicalization (C) is ONLY applied to `call_discoverable_*_tool.arguments`.** It's not applied to `unlock_discoverable_agent_tool` or `give_discoverable_user_tool` arguments, which don't have an `arguments` field. This is intentional (only the inner call needs JSON normalization).

5. **Scenario playbook matches only on the FIRST UserMessage.** Once `scenario_playbook` is set in `_track_state()`, it never changes. If the customer's initial message matches "payment not reflected", every subsequent tool result will surface the playbook until the LLM executes it. This is a form of persistent context injection.

6. **The user-side tool counter (user_calls_by_tool) is populated by regex matching tool result content,** not by observing actual tool calls. The _track_state regex looks for "Executed: {name}" or "Called: {name}" in the ToolMessage content. This means if the environment's format changes, the counter breaks silently.

7. **Dispute candidates are computed OFFLINE by the extension,** not by the LLM. The extension parses transaction records and runs the cash-back formula directly. If the formula in the extension doesn't match the oracle's behavior, Intervention F will give the LLM incorrect txn_ids to dispute, causing failures downstream.

8. **The catalog is built at IMPORT TIME by AST-parsing tau2-bench source.** If tau2-bench is not cloned, `_VALID_DISCOVERABLE_NAMES` is empty and Intervention D (hallucination guard) is disabled. The agent falls back to generic behavior and the LLM can hallucinate tool names unchecked.

---

## Statistics

- **Total explicit interventions:** 8 (labeled A–H in `_gate_tool_calls`)
- **Total annotator rules:** 10 (in `annotate_banking`)
- **Extension hooks:** 5 (plugged into _track_state and gate)
- **Total gate interventions:** ~2000 LOC of interdependent code across agent.py + compass.py + compass_banking.py
- **State fields touched:** 10+ (unlocked_for_agent, unlocked_for_user, verified_user_ids, turn_count, user_calls_by_tool, current_user_id, dispute_candidates_by_user, transactions_by_user, scenario_playbook, gate_interventions)

