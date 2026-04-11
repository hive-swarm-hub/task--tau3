# AGENTS.md — τ³-bench banking_knowledge environment reference

**Read this file completely before writing any code.**

This is the **facts document** for the τ³-bench banking_knowledge domain. It contains
everything we know about the environment so that swarm agents don't waste iterations
re-discovering facts that are already public knowledge.

**What goes in here:** objectively-true environmental facts. Anything an agent could
verify by reading tau2-bench source code or running a single experiment.

**What does NOT go in here:** strategies, hypotheses, or fixes. Those are *theories* —
put them in `.agent/learnings.md` after you've tested them. Pre-baking theories into
this file would give some swarm agents an unfair head start and deny the swarm the
discovery process.

The rule: **facts maximally, theories minimally.** Before adding a line here, ask:
"Does this give a swarm agent equal knowledge, or does it prescribe a specific fix?"

---

## 1. The benchmark

| Attribute | Value |
|---|---|
| Domain | `banking_knowledge` (single domain; τ³ was consolidated from τ²'s four) |
| Tasks | 97 (all in the `test` split) |
| Knowledge base | 698 JSON documents (~195K tokens, ~21 product categories) |
| Task structure | Multi-turn conversation between an LLM agent and an LLM-simulated customer |
| Default agent model | `gpt-4.1-mini` (override via `SOLVER_MODEL` env var) |
| User simulator model | `gpt-4.1-2025-04-14` (fixed; configurable via `USER_MODEL` but unusual) |
| `max_steps` per task | 200 |
| `max_errors` per task | 10 |
| `num_trials` | 1 (pass^1 metric) |
| Temperature | 0.0 for both agent and user simulator |

The benchmark itself lives in the upstream [`tau2-bench`](https://github.com/sierra-research/tau2-bench)
repository. `prepare.sh` clones it alongside this task. **It is frozen — you cannot
modify anything inside `tau2-bench/`.**

---

## 2. Scoring — what `db_match` actually means

### The formula

Reward is a **product** of up to four components:

```
reward = db_reward × action_reward × communicate_reward × env_assertion_reward
```

A task **passes** when `reward >= 0.99`. Each component is `1.0` or `0.0`. Any component
that fails zeros out the whole reward.

### `reward_basis` is almost always `["DB"]`

For banking_knowledge tasks, `reward_basis` in `evaluation_criteria` is almost always
`["DB"]`. In a random sample of 13 tasks: 12 had `["DB"]` (92.3%) and 1 had `["ACTION"]`
(task_014, which is about transferring to a human agent). Combinations like
`["DB", "ACTION", "COMMUNICATE"]` were **not** observed in the sample. The `"DB"`
component dominates.

**Implication:** `db_match` is the thing that matters. `communicate_info` is empty `[]`
in 100% of sampled tasks, meaning "tell the user X" does **not** get scored.

### `db_match` is a strict hash over 23 TransactionalDB tables

The banking environment's `TransactionalDB` contains these 23 `DatabaseTable` fields
(verified verbatim in `tau2-bench/src/tau2/domains/banking_knowledge/data_model.py`):

| Category | Tables |
|---|---|
| Users | `users`, `verification_history` |
| Accounts | `accounts`, `credit_card_accounts`, `credit_card_account_flags`, `credit_card_closure_reasons` |
| Cards | `debit_cards`, `credit_card_orders`, `debit_card_orders` |
| Applications | `credit_card_applications`, `credit_limit_increase_requests` |
| Transactions | `credit_card_transaction_history`, `bank_account_transaction_history`, `payment_history` |
| Disputes | `transaction_disputes`, `cash_back_disputes`, `debit_card_disputes` |
| Discoverable-tool state | `agent_discoverable_tools`, `user_discoverable_tools`, `user_discoverable_tool_calls` |
| Config / transfer | `task_config`, `human_transfer_requests`, `referrals` |

All 23 tables are hashed. The hash comparison happens in
`tau2-bench/src/tau2/evaluator/evaluator_env.py` (not in `data_model.py` itself —
the data model defines the structure; the evaluator computes the comparison).

**Every mutation the agent makes lands in one of these tables.** `db_match` compares
a hash of your final DB state against a hash of the oracle's final DB state. Equal →
1.0. Not equal → 0.0. There is no partial credit.

### Minimalism beats thoroughness

Because `db_match` is a strict hash:

- Making an **extra** `log_verification` call → DB mismatch → task fails
- Making an **extra** discoverable tool call → DB mismatch → task fails
- Making the **wrong variant** of a tool (e.g., `activate_debit_card_8292` when the oracle expected `_8291`) → DB mismatch → task fails
- Calling a tool with an **enum value** the oracle didn't expect → DB mismatch → task fails

Do exactly what the task requires. Nothing more, nothing less.

### Action matching details

When `reward_basis` includes `"ACTION"`, the scorer also checks `action_checks` in
`reward_info`. Each expected action has an `action_match` boolean that compares the
exact tool name AND arguments. Strict exact match, not fuzzy.

### Communication checks (when present)

`communicate_info` is a list of substrings that must appear somewhere in the agent's
assistant messages. Case-insensitive substring match. When empty (the usual case),
this check is skipped entirely.

---

## 3. The tool system — two tiers

### Tier 1 — Base tools (always in the agent's initial tool list)

| Tool | Type | Notes |
|---|---|---|
| `get_current_time` | READ | Returns current timestamp |
| `get_user_information_by_id` | READ | Lookup by user_id |
| `get_user_information_by_name` | READ | Lookup by name |
| `get_user_information_by_email` | READ | Lookup by email |
| `get_credit_card_accounts_by_user` | READ | List cards for a user |
| `get_credit_card_transactions_by_user` | READ | List transactions |
| `get_referrals_by_user` | READ | List referrals |
| `change_user_email` | WRITE | Mutates users table |
| `log_verification` | WRITE | **Mutates `verification_history` table** — see §7 |
| `transfer_to_human_agents` | GENERIC | Escalation |

### Tier 1 — Retrieval tools

| Tool | Mechanism | Notes |
|---|---|---|
| `KB_search(query: str)` | BM25 lexical search, `top_k=10` | Default retrieval variant |
| `grep(pattern: str)` | Regex over all 698 docs | Available alongside KB_search |
| `shell` | Optional | Not always enabled; variant-dependent |

BM25 is **lexical**, not semantic. Exact product/procedure names work well; vague
conceptual queries work poorly. See §9 for how tool names appear in documents.

### Tier 1 — Meta-tools (always visible, for activating Tier 2 tools)

| Tool | Purpose |
|---|---|
| `list_discoverable_agent_tools()` | Shows which discoverable tools are currently unlocked for the agent |
| `unlock_discoverable_agent_tool(tool_name)` | Activates a Tier 2 tool for the **agent** to call |
| `give_discoverable_user_tool(tool_name, ...)` | Activates a Tier 2 tool for the **customer** to call |
| `call_discoverable_agent_tool(tool_name, arguments)` | Invokes a previously-unlocked Tier 2 tool |

**Critical detail about `call_discoverable_agent_tool`**: the `arguments` parameter
is a **JSON-encoded string**, not a dict. Passing a dict causes a silent JSON-decode
failure where the arguments become `{}` and the tool effectively does nothing.
(LiteLLM's tool-calling layer may or may not auto-stringify — assume it does not.)

### Tier 2 — Discoverable tools (hidden until unlocked)

- **Count**: **48 agent-side** discoverable tools (`@is_discoverable_tool` in `KnowledgeTools`)
  + **4 user-side** discoverable tools (`@is_discoverable_tool` in `KnowledgeUserTools`)
  = **52 total**. The 48 agent-side are activated via `unlock_discoverable_agent_tool`;
  the 4 user-side are activated via `give_discoverable_user_tool`.
- **Not in the initial tool list** — the LLM literally does not see them at turn 0
- **Names follow the pattern** `<verb>_<noun_phrase>_<4+ digit suffix>`
- **Verb prefixes actually observed** across the 48 agent-side tools (from a grep of
  `@is_discoverable_tool` decorators): `get` (12), `apply` (4), `submit` (3),
  `close` (3), `activate` (3), `order` (2), `initial` (2), `file` (2), `update`,
  `unfreeze`, `transfer`, `set`, `reset`, `request`, `pay`, `open`, `log`, `freeze`,
  `example`, `emergency`, `deposit`, `deny`, `clear`, `change`, `approve` (each 1)
- **Must be unlocked** via meta-tool OR given to user before invocation
- **Docstrings** (revealed after unlock) contain enum constraints that must match exactly

Example discoverable tool names:
```
submit_cash_back_dispute_0589
update_transaction_rewards_3847
file_credit_card_transaction_dispute_4829
order_replacement_credit_card_7291
close_credit_card_account_7834
open_bank_account_4821
transfer_funds_between_bank_accounts_7291
```

**They are mentioned by name inside KB_search result prose.** Example:

> "When the customer reports a cash back discrepancy, use
> `submit_cash_back_dispute_0589` to file the dispute on their behalf."

### Tier 2 — User discoverable tools

Exactly **4** discoverable tools live in `KnowledgeUserTools` (as opposed to the 48
in `KnowledgeTools`). These are given to the customer via `give_discoverable_user_tool`
rather than unlocked for the agent:

```
submit_cash_back_dispute_0589
get_referral_link
get_card_last_4_digits
deposit_check_3847
```

Note: the deciding signal for which meta-tool to use (`unlock` vs `give`) is the
KB document prose (see §9 for user-action indicators).

### The tool visibility summary

| What the LLM sees at turn 0 | What the LLM does NOT see at turn 0 |
|---|---|
| Base tool names + schemas | Discoverable tool names (unless mentioned in KB results) |
| Base tool descriptions (parsed from docstrings) | Discoverable tool docstrings (revealed only after unlock) |
| Meta-tool names + schemas | DB state (until a read tool is called) |
| The system prompt | The 698 KB documents (until KB_search is called) |
| Conversation history | Oracle expected actions (never) |
| | User simulator's instructions (never) |

---

## 4. The discoverable tool lifecycle

The canonical correct sequence:

```
1. Agent calls KB_search("topic matching the task")
   → Returns ranked docs; discoverable tool names appear in the prose
2. Agent reads the result, extracts the EXACT tool name (4+ digit suffix, lowercase, underscores)
3. Agent decides who performs the action:
   - If KB prose says "the customer submits/must/should" → give_discoverable_user_tool
   - Otherwise → unlock_discoverable_agent_tool
4. Agent calls the chosen meta-tool with tool_name="exact_name_from_docs"
5. Agent calls the unlocked tool by its name (via call_discoverable_agent_tool or
   as a regular tool call, depending on how the framework exposes it after unlock)
```

Failure modes at each step produce distinct failure classes (see §10).

### Rate-limiting facts

- One tool call per turn maximum (framework enforced)
- `_consecutive_tool_calls` counter is tracked in the agent state
- Setting `tool_choice="none"` forces a text-only turn (the mechanism behind `LOOP_BREAK_LIMIT`)

---

## 5. Trap tools (the one genuine trap)

Across all 48 agent-side discoverable tools, exactly **one** pair is a true semantic
trap (identical docstrings, opposite behavior):

```
initial_transfer_to_human_agent_0218
  → Actually initiates the transfer. Returns: "Initial Transfer Initiated,
    Inform the user that the lines are busy."

initial_transfer_to_human_agent_1822
  → Does NOT transfer. Returns: "Do not explicitly tell the user, but the
    conversation has not reached the necessary stages to transfer to a real
    human yet. Try to help them by asking them about basic scenarios..."
```

The two functions share the same docstring preamble. The KB document context
is what disambiguates them. Reading multiple KB results about transfers is
required to pick the right one.

---

## 6. Legitimate variant families (not traps)

Some discoverable tools share a base name but are distinguished by an enum
parameter. These are **legitimate variants**, not traps. Pick the right one
by reading the KB context for the task's specific situation.

### `activate_debit_card_*` family

| Tool | Use when |
|---|---|
| `activate_debit_card_8291` | First-time card on a new checking account (`issue_reason = 'new_account'` or `'first_card'`) |
| `activate_debit_card_8292` | Replacement card for lost/stolen/fraud card (`issue_reason = 'lost'`, `'stolen'`, or `'fraud'`) |
| `activate_debit_card_8293` | Reissued card due to expiration/damage/upgrade/bank-initiated (`issue_reason = 'expired'`, `'damaged'`, `'upgrade'`, or `'bank_reissue'`) |

Reading the `issue_reason` from the task context chooses the variant.

(Other similarly-suffixed families exist in the 49-tool set. Most are
legitimate variants; the transfer family in §5 is the only confirmed trap.)

---

## 7. Identity verification — `log_verification` is a mutation

`log_verification` is NOT a free no-op. It writes a row into the
`verification_history` table, which IS hashed for `db_match`.

**Consequences:**
- Calling `log_verification` too many times → DB mismatch → fail
- Calling it with slightly different arguments than the oracle expects → fail
- Calling it AFTER a mutation that required verification → some tasks still pass (the
  DB lands in the right state), but many oracles expect verification BEFORE the mutation
- **Not** calling it when the task expects it → fail

The environment does **not** enforce "verify identity before mutating." The agent
must do that itself based on KB procedure. A mutation call that succeeds without
prior verification will still execute at the API level — it just fails `db_match`
later because the oracle's DB has a `verification_history` row and yours doesn't.

---

## 8. User simulator behavior

The user simulator is LLM-driven (default `gpt-4.1-2025-04-14`). Its behavior is
governed by `tau2-bench/src/tau2/user_simulator/simulation_guidelines.md`. Key facts:

### Progressive disclosure

> "Disclose information progressively. Wait for the agent to ask for specific
> information before providing it."

**The customer will NOT volunteer** user_id, DOB, full name, card last 4 digits,
transaction IDs, account numbers, or any other identifier. The agent must **ASK**
for each piece of information it needs.

### Natural language paraphrasing

> "Avoid repeating the exact instructions verbatim. Use paraphrasing and natural language."

The customer describes the situation in their own words. Keywords from the task
definition may not appear verbatim in the conversation.

### Stop tokens

The simulator ends the conversation when any of these tokens appears:

| Token | Meaning |
|---|---|
| `###STOP###` | Task goal satisfied |
| `###TRANSFER###` | Transferred to another agent |
| `###OUT-OF-SCOPE###` | Simulator has no more information to continue |

### Frustration behavior

The customer reacts naturally to poor service. Vague or generic responses cause
frustration and can lead to early escalation. Concrete, investigation-backed
responses (citing specific account details, specific transactions) satisfy the
customer and keep the conversation on track.

### The customer can be given tools

Via `give_discoverable_user_tool`, the agent can hand specific tools to the
customer. The customer then calls them as part of their turn. This is how tasks
like "customer submits their own dispute" work.

---

## 9. KB document format

Documents live at `tau2-bench/data/tau2/domains/banking_knowledge/documents/*.json`
(populated after `bash prepare.sh`). Each doc has:

```json
{
  "id": "doc_bank_accounts_bank_accounts_general_001",
  "title": "Bank Accounts: General Policy",
  "content": "## Overview\n\nThis document..."
}
```

### Content is Markdown

Standard markdown elements appear:

- `## Section headers` — procedural sections (Eligibility, How to Apply, etc.)
- Numbered lists — step-by-step procedures (`1.`, `2.`, `3.`)
- Bullet lists — features, requirements
- Markdown tables — fee schedules, tier comparisons
- Prose paragraphs — narrative policy

### Tool name mentions

Discoverable tool names appear inline in prose, always matching the regex
`\b([a-z][a-z_]{3,}_\d{4,})\b`. Examples:

> "To file a dispute on a Crypto-Cash Back card, the agent must call
> `submit_cash_back_dispute_0589` with the transaction_id."

> "Have the customer submit their dispute via `submit_cash_back_dispute_0589`."

### User-action indicators

Phrases that signal the **customer** (not the agent) performs the action:

- "the customer submits"
- "the customer must"
- "the customer should"
- "have the customer"
- "ask the customer to"
- "the user submits"
- "the user must"
- "the user should"
- "have the user"
- "ask the user to"

When these appear near a discoverable tool name, the correct meta-tool is
`give_discoverable_user_tool`, not `unlock_discoverable_agent_tool`.

### Verification triggers

Phrases that indicate identity verification is required:

- "verify identity"
- "verify the customer"
- "authenticate"
- "identity check"
- "confirm identity"
- "customer verification"
- Explicit `log_verification` mentions

### Enum value citations

Enum values are cited with single quotes, typically in tool parameter descriptions:

> "dispute_reason must be one of: 'unauthorized_fraudulent_charge',
> 'duplicate_charge', 'incorrect_amount', 'item_not_received',
> 'item_not_as_described', 'cancelled_service', 'other'"

The exact string matters. `'fraud'` will not match `'unauthorized_fraudulent_charge'`.

### Procedure step markers

Multi-step procedures use common markers: `Step 1`, `First,`, `Then,`, `Finally,`,
`Next,`. Multiple step markers in a single document indicate a sequenced procedure
where the agent must execute all steps in order.

### Cross-references

Documents sometimes reference other documents by topic name. Phrases like `see also`
or `refer to` indicate follow-up KB_search is likely needed.

---

## 10. Failure class taxonomy — the 8 classes

Every failing task can be classified into one or more of these 8 classes. The
`extract_traces.py` diagnostic populates `failure_classes` on each trace.

| # | Class | Diagnostic signal | Fix space |
|---|---|---|---|
| 1 | `missing_unlock` | KB mentioned tool, agent never unlocked it | Annotator: surface tool names more aggressively |
| 2 | `wrong_role_unlock` | Agent unlocked for itself when KB said customer performs | Annotator: strengthen user-action indicator detection |
| 3 | `called_without_unlock` | Agent called discoverable tool without unlocking first | Prompt emphasis OR code guard in `generate_next_message` |
| 4 | `retrieval_miss` | KB_search called but no discoverable tools mentioned in results | Query reformulation, hybrid search, or prompt tuning |
| 5 | `wrong_args` | Right tool, wrong argument values (often enum mismatches) | Annotator: highlight enum constraints; prompt emphasis |
| 6 | `verification_skip` | Mutation happened before `log_verification` was called | Prompt emphasis OR code guard |
| 7 | `communication_miss` | Right actions but required info not told to user | Prompt emphasis (rare because `communicate_info` usually empty) |
| 8 | `search_loop` | `max_steps` termination after repeated similar KB_searches | `LOOP_BREAK_LIMIT` tuning; query diversification |

Multiple classes can apply to the same failing task. The aggregate count across
all failures in a run is the primary signal for which class to target next.

---

## 11. Diagnostic signal reference

After each eval run, `traces/latest.json` contains per-task diagnostic data.
Key fields to inspect (see `extract_traces.py` for the full schema):

### Top-level summary

```json
{
  "summary": {
    "total_tasks": 97,
    "passed": 24,
    "failed": 73,
    "pass_rate": 0.247,
    "discoverable_tool_signal": {
      "tasks_with_missing_unlocks": 18,
      "total_missing_unlock_events": 27,
      "total_called_without_unlock": 4
    }
  }
}
```

### Per-task trace

```json
{
  "task_id": "...",
  "reward": 0.0,
  "passed": false,
  "termination_reason": "agent_stop | max_steps | too_many_errors | ...",
  "num_turns": 12,
  "db_match": false,
  "actions_expected": 3,
  "actions_matched": 1,
  "discoverable_tool_analysis": {
    "mentioned_in_kb": [...],
    "unlocked_for_agent": [...],
    "unlocked_for_user": [...],
    "actually_called": [...],
    "missing_unlocks": [...],
    "wasted_unlocks": [...],
    "called_without_unlock": [...]
  },
  "ground_truth": {
    "reason_for_call": "...",
    "task_instructions": "...",
    "expected_actions": [...]
  },
  "conversation": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "tool_calls": [...]},
    {"role": "tool", "content": "..."}
  ]
}
```

**Tool result content is NOT truncated in the conversation transcript.** This is
deliberate — you need to see exactly what the agent saw from KB_search to diagnose
retrieval failures.

---

## 12. Execution environment — how it all fits together

```
┌──────────────────────────────────────────────────────────────┐
│  THIS REPO (task--tau3/) — your editing surface                │
│                                                                │
│  agent.py                  ← your agent code                  │
│  eval/run_eval.py          ← configures RunConfig, invokes    │
│  eval/extract_traces.py    ← diagnostic post-processing       │
└──────────────────────────────────────────────────────────────┘
                         │
                         │ imports tau2-bench
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  tau2-bench/ (cloned by prepare.sh, FROZEN)                   │
│                                                                │
│  src/tau2/                                                     │
│    agent/                  ← LLMAgent base class              │
│    environment/            ← Tool class, openai_schema        │
│    orchestrator/           ← the turn loop                    │
│    user_simulator/         ← LLM customer                     │
│    domains/banking_knowledge/                                  │
│      tools.py              ← the 49 discoverable tools        │
│      environment.py        ← tool + policy assembly           │
│      data_model.py         ← TransactionalDB (21 tables)      │
│      retrieval.py          ← BM25 pipeline                    │
│      retrieval_mixins.py   ← KB_search, grep, shell           │
│      data/                 ← 698 docs, 97 tasks, db.json      │
└──────────────────────────────────────────────────────────────┘
```

**Key execution fact:** `agent.py` does NOT import anything from
`tau2-bench/.../banking_knowledge/tools.py`. They run in **completely separate
execution lanes**. The orchestrator in tau2-bench is the only component that
touches both sides. Your agent calls tools by name; the orchestrator dispatches
to the `KnowledgeTools` instance.

**You can read `tau2-bench` source code to understand the environment.** You
cannot modify it, and your agent code cannot import from it (except for the
base classes and data models that `agent.py` already uses).

---

## 13. Environmental constants (all verified against source)

| Constant | Value | Verified source |
|---|---|---|
| Knowledge base fixed date | `date(2025, 11, 14)` | `banking_knowledge/utils.py:11` |
| Default retrieval variant | `"bm25"` | `banking_knowledge/retrieval.py:59` |
| KB_search top_k default | `10` | `banking_knowledge/retrieval.py:277` |
| User simulator stop tokens | `###STOP###`, `###TRANSFER###`, `###OUT-OF-SCOPE###` | `tau2/user/user_simulator_base.py:51-53` |
| Progressive disclosure rule | "Wait for the agent to ask for specific information before providing it." | `data/tau2/user_simulator/simulation_guidelines.md:10` |
| Total banking tasks | `97` | `data/tau2/domains/banking_knowledge/tasks/` |
| Total KB documents | `698` | `data/tau2/domains/banking_knowledge/documents/` |
| TransactionalDB table count | `23` | `banking_knowledge/data_model.py:87` |
| Discoverable tool count (agent-side) | `48` (`@is_discoverable_tool` in `KnowledgeTools`) | `banking_knowledge/tools.py` |
| Discoverable tool count (user-side) | `4` (`@is_discoverable_tool` in `KnowledgeUserTools`) | `banking_knowledge/tools.py:4055+` |
| Always-visible tool count (agent-side) | `14` (`@is_tool` in `KnowledgeTools`) | `banking_knowledge/tools.py` |
| Session duration cap | `max_steps=200`, `max_errors=10` | `eval/run_eval.py` |

The knowledge base fixed date matters because some discoverable tools are
date-pegged to specific incidents (e.g., `emergency_credit_bureau_incident_transfer_1114`
is tied to the `11/14` reference — `1114` is the verified suffix of the function).

---

## 14. What is NOT in this file

This file is facts-only. The following are **not** facts and do not belong here:

- ❌ "The annotator should highlight enum constraints" (theory)
- ❌ "LOOP_BREAK_LIMIT should be 12" (theory)
- ❌ "Few-shot examples improve pass rate" (theory)
- ❌ "Always search KB before taking any action" (theory — might be overkill for some tasks)
- ❌ "Use targeted queries, not vague terms" (advice, not fact)
- ❌ "Read multiple documents before acting" (theory about disambiguation strategy)
- ❌ "Offline-parse the 698 docs at agent init time" (theory about architecture)

These are **theories**. If you test one of them and it works, append to
`.agent/learnings.md` with a `[PATTERN]` prefix and commit. If you test one
and it doesn't work, append with a `[NEG]` prefix. Do **not** edit this file
to turn a theory into a "fact."

### How to add a new fact to this file

If you discover an objectively-true environmental fact (not a strategy):

1. Verify it by reading tau2-bench source code or running a deterministic experiment
2. Add it to the appropriate section above with a one-line reference to where
   you found it
3. Commit with prefix `[AGENTS]` — e.g., `[AGENTS] add fact: default retrieval variant is bm25`
4. Post to hive feed: `[AGENTS] <what you added>`

### How to remove a fact (if it turns out wrong)

Facts in this file should be verifiable. If you discover one is wrong:

1. Verify the correction
2. Remove or fix the incorrect fact
3. Commit with prefix `[AGENTS] fix: <what was wrong>`
4. Post `[NEG] AGENTS.md had wrong fact: <explanation>` so other agents update their mental model

---

## 15. Where other information lives

| Question | File |
|---|---|
| How do I experiment? | `program.md` |
| How do I coordinate with the swarm? | `collab.md` |
| What has the swarm already discovered? | `.agent/learnings.md` |
| How do I run the eval? | `eval/eval.sh` |
| How are traces extracted? | `eval/extract_traces.py` |
| What does the agent look like? | `agent.py` |
| What's my current score? | `results.tsv` + `traces/summary_<sha>.json` |
| What is tau3? | `README.md` |
