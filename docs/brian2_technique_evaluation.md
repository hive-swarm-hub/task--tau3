# Brian2 Technique Evaluation (SHA 7edf50cc)

Brian2 reached 10/97 from our 7/97 baseline (0d3e76ac). This doc evaluates
their three techniques for adoption into our scaffold.

---

## Technique 1: Enum Pre-Validation Gate (Intervention H)

**Verdict: ADOPT (modified)**

### What it does

Before `call_discoverable_agent_tool` goes through, parses enum constraints
from the tool docstring via `COMPASS.enum_constraints(tool_name)`, checks if
proposed argument values are in the valid set, and drops the call with a
correction note if invalid.

### Assessment

- **`COMPASS.enum_constraints()` already exists** in our `compass.py:575`.
  It returns `{param_name: [valid_values]}` by parsing "Must be one of:"
  patterns from docstrings. It covers 8 tools today (the disputes,
  `apply_statement_credit_8472`, `open_bank_account_4821`, etc.).
- **Our annotator already detects enums** (`agent.py:377-392`, the
  `ENUM CONSTRAINT:` annotation) but only *surfaces* them — it does not
  *enforce* them. Brian2's gate is the missing enforcement half.
- **Domain-agnostic**: `enum_constraints()` is generic — it parses any
  docstring with the "one of:" pattern. Works for airline/retail/telecom
  tools too. No banking-specific code needed.
- **Risk**: Low false-positive rate. The docstring IS the ground truth for
  enum values in tau2-bench. One gap: some tools list enums without "one of:"
  phrasing (e.g., `close_debit_card_4721` says "Reason for closing: lost,
  stolen, fraud_suspected..."). Our regex misses these. We should widen the
  `_ENUM_RE` pattern first.
- **LOC estimate**: ~20 lines added to `_gate_tool_calls()` in `agent.py`,
  after the existing Intervention D hallucination guard. Parse the inner
  `arguments` JSON, call `COMPASS.enum_constraints(target_tool)`, check
  each constrained param, drop + inject correction note if invalid.

### Where to put it

`agent.py`, inside `_gate_tool_calls()`, as Intervention H. Insert after
the existing hallucination guard (Intervention D, line ~825) and before
Intervention E (Phase-2 guard, line ~920). The gate already has the
`call_discoverable_agent_tool` args parsed — add ~20 lines:

```python
# Intervention H: enum pre-validation gate
if name == "call_discoverable_agent_tool" and isinstance(args, dict):
    target_tool = args.get("agent_tool_name") or ""
    inner_str = args.get("arguments", "{}")
    try:
        inner = json.loads(inner_str) if isinstance(inner_str, str) else inner_str
    except json.JSONDecodeError:
        inner = {}
    constraints = COMPASS.enum_constraints(target_tool)
    for param, valid_vals in constraints.items():
        actual = inner.get(param)
        if actual and actual not in valid_vals:
            drop_notes.append(
                f"Invalid value '{actual}' for {param}. "
                f"Must be one of: {valid_vals}"
            )
            # ... drop and continue
```

### Pre-requisite

Widen `compass.py` `_ENUM_RE` to also catch "Reason for closing: X, Y, Z"
patterns (the non-"one of:" format). ~5 lines.

---

## Technique 2: KB-Mined account_class Map (Intervention I)

**Verdict: MODIFY (adopt the data, not the implementation)**

### What it does

Scans KB doc filenames matching `doc_<category>_accounts_<slug>_NNN.json`,
extracts `(account_type -> account_class)` mapping, injects into system
prompt and enum validator. Recovered task_058 (savings/Silver Account)
and task_075 (checking/Green Fee-Free Account).

### Assessment

- **Real problem**: `open_bank_account_4821`'s docstring says `account_class`
  is "The full official account class name" with NO enum list. The LLM must
  guess from KB docs. Task_058 and task_075 both fail because the LLM picks
  wrong account_class values.
- **Brian2's implementation is fragile**: hardcodes `/Users/brianchen/...`,
  uses regex on filename slugs with heuristic normalization ("cobalt_blue" ->
  "Cobalt Blue Account"). Filenames are not guaranteed to follow this pattern
  long-term.
- **Better approach**: Read the actual doc JSON content, not filenames. Each
  doc's `title` field contains the canonical account name (e.g., "Green
  Fee-Free Account"). Parse category from the filename prefix
  (`doc_checking_accounts_*` -> checking, `doc_savings_accounts_*` -> savings,
  etc.) and title from the JSON. This is robust to filename slug changes.
- **Placement**: `compass_banking.py` as a `BankingExtension.account_class_map`
  property, lazy-loaded from `_TAU2_BANKING_DOCS_DIR`. The map gets injected
  into the system prompt alongside the existing catalog section.
- **LOC estimate**: ~40 lines in `compass_banking.py` for the parser +
  property, ~5 lines in `agent.py` to inject into system prompt.
- **Also extend enum gate**: Once we have the map, register it as a synthetic
  enum constraint for `open_bank_account_4821.account_class` so Intervention
  H catches wrong values automatically.

### Concrete file list

The filesystem shows these personal account categories:
- `doc_checking_accounts_*` (11 account classes: Blue, Bluest, Dark Green, Evergreen, Gold Years, Green, Green Fee-Free, Light Blue, Light Green, Purple)
- `doc_savings_accounts_*` (9 account classes: Bronze, Diamond Elite, Gold, Gold Plus, Green, Platinum, Platinum Plus, Silver, Silver Plus)
- `doc_business_checking_accounts_*` (8 classes)
- `doc_business_savings_accounts_*` (8+ classes)

---

## Technique 3: Phase-2 Guard Tightening (reverted)

**Verdict: SKIP**

### What brian2 did

Changed `user_calls >= 1` to `user_calls >= len(candidates)` — requiring
ALL expected disputes to be submitted before allowing agent-side cleanup.

### Why they reverted

The dispute calculator's candidate count does not always match the oracle's
expected submission count. Some tasks expect fewer submissions than the
calculator identifies (e.g., the customer stops after partial submission, or
the oracle only expects disputes above a certain drift threshold). Blocking
until `count == len(candidates)` causes the agent to stall indefinitely
waiting for submissions that will never come.

### Is there a version that would work?

Possibly: track a "customer said they're done" signal (e.g., the user
simulator says "that's all" or "###STOP###") and release the guard then.
But this adds complexity for uncertain gain — the `>= 1` check already
handles the primary failure mode (premature Phase-2 before ANY user
submission). Revisit only if we see new traces where exactly-one-submission
tasks regress.

---

## What's Still Missing

Neither our interventions (A-G) nor brian2's (H-I) address these failure
modes visible in `traces/latest.json`:

### 1. Long-sequence execution discipline (task_091, task_087)

task_091 expects 25 actions across 4 debit cards (PIN resets, closures,
disputes, reorders) but the agent completes 0/25 in only 13 turns. task_087
gets 6/20 in 57 turns. The LLM loses track of multi-card workflows. Neither
enum validation nor account_class maps help here — the bottleneck is the LLM's
ability to follow a 20+ step procedure without losing state. A possible
intervention: inject a "checklist" annotation tracking completed vs remaining
actions when the expected-action count is high.

### 2. User-simulator social engineering (task_005)

task_005 expects the agent to be socially engineered: the user provides a
mismatched email, the agent leaks the real email, user "corrects" to match,
then a fake bypass code is presented. The oracle expects the agent to comply
with the bypass code (`log_verification` with all fields = "9K2X7M4P1N8Q3R5T6A").
Our agent correctly refuses the social engineering — which means it "fails"
the task. This is an adversarial-compliance task that no generic intervention
can solve without specifically recognizing bypass-code patterns.

### 3. Credit card application argument format (task_024)

task_024 expects `apply_for_credit_card(card_type="Business Bronze Rewards
Card", ...)` but the agent either never reaches this tool or passes wrong
arguments. The `apply_for_credit_card` tool is a BASE tool (not discoverable),
so the enum gate does not intercept it. A separate pre-validation for base
tool enum values would be needed.
