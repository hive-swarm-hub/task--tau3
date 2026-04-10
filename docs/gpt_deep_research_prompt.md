# GPT Deep Research Prompt — τ³-bench banking_knowledge

Paste the section below into ChatGPT with Deep Research enabled (or Gemini
Deep Research, Claude web, etc). The prompt is self-contained — Deep Research
will need to browse tau2-bench upstream to verify the facts.

---

## Role

You are a senior applied-ML research engineer reviewing an autonomous agent
submission to a multi-agent swarm on τ³-bench banking_knowledge. The swarm
has been running for ~24 hours across multiple agents and has plateaued
around a verified score of **0.02–0.06 on the full 97-task eval** (and
~0.05–0.20 on noisy 20-task subsamples). We need a deeper, code-level
diagnosis of what's actually blocking progress, and a ranked list of
unexplored interventions.

## Environment

- **Benchmark**: [τ²-bench banking_knowledge](https://github.com/sierra-research/tau2-bench)
  on the `banking_knowledge` domain. 97 tasks in the test split.
- **Scoring**: `reward = db_reward × action_reward × communicate_reward ×
  env_assertion_reward`. A task **passes** iff `reward >= 0.99`. For most
  banking_knowledge tasks `reward_basis = ["DB"]`, so the only thing that
  matters is `db_match` — a strict hash comparison across 23 TransactionalDB
  tables. Every extra mutation (extra log_verification, extra unlock, wrong
  enum value) fails the hash.
- **Tool system**: 10 base tools (always visible) + 4 discovery meta-tools
  (always visible) + **48 discoverable tools hidden inside KB docs** (44
  agent-side via `unlock_discoverable_agent_tool`, 4 user-side via
  `give_discoverable_user_tool`). Discoverable tool names are static:
  defined as `@is_discoverable_tool`-decorated methods in
  `tau2-bench/src/tau2/domains/banking_knowledge/tools.py`.
- **Retrieval**: BM25 over 698 banking policy JSON docs, `top_k=10`. Only
  **45 of 698 docs** mention any discoverable tool. The agent must surface
  the right tool by searching the right keywords into the right doc.
- **Agent model**: `gpt-4.1-mini` (configurable) with `temperature=0.0` and
  a fixed seed, but OpenAI's determinism is best-effort — same code gives
  variance across reruns (1/20 vs 4/20 observed).
- **User simulator**: `gpt-4.1-2025-04-14`. Progressive disclosure: the
  customer won't volunteer IDs/DOB/etc; the agent must explicitly ask.
- **Max turns**: 200 per task; `max_errors=10`.
- **Termination**: `user_stop` (###STOP###), `max_steps`, `too_many_errors`.

## What the swarm has already ruled out (or learned)

From reading `hive task context --task tau3-banking` feed posts and runs:

1. **Prompt engineering is saturated near stock.** Few-shot additions,
   minimal nudges, three-phase procedural prompts, pre-write verification
   prompts, and write-gate self-critique were all measured at 0.05–0.15
   and called within-noise of the stock baseline (stock full=0.02–0.06).
2. **Retrieval variance doesn't matter.** Dense retrieval regressed by
   −0.15 vs BM25. Reranker was neutral. `top_k=15` vs `top_k=10` was neutral.
   Full-KB context injection regressed (hurts). Quote: "retrieval not the
   bottleneck, info is not the bottleneck."
3. **Self-critique doesn't fix wrong-arg errors.** Write-gate critique that
   asked the LLM to review its own tool call before executing didn't
   measurably improve pass rate.
4. **Determinism is fake.** At temp=0 + fixed seed, OpenAI's
   `system_fingerprint` drifts across ~2000 generations/run, cascading
   into different tool-call trajectories. A local litellm cache monkey-
   patch was proposed and rejected as "hiding the variance from the blog
   post numbers."
5. **Gold-action match rate is ~6%.** Across all assistant actions across
   all tasks in one run: 9/142 matched the oracle exactly. "The model
   simply can't execute the benchmark reliably at this scale."

## The work we've done (to build on, not repeat)

The submission under review is a clean τ²-bench v1.0.0 agent with:

1. **Catalog pre-baking via `compass.py`** — AST-parses tau2-bench tools.py
   at import time, extracts all 48 `@is_discoverable_tool` functions with
   their type (READ/WRITE/GENERIC), parameters, and full docstrings. The
   full catalog is rendered into the system prompt (~2000 tokens) so the
   agent sees every discoverable tool from turn 0. Also scans the 45 KB
   docs that mention any tool to build a tool↔doc cross-reference.

2. **State-aware annotator** — `annotate_banking(content, state)` enriches
   KB_search results with: ALREADY UNLOCKED / STILL TO UNLOCK split (state-
   aware), USER-FACING ACTION DETECTED (from "the customer submits" prose),
   VERIFICATION REQUIRED vs "already verified" (state-aware), MULTI-STEP
   PROCEDURE flag, CROSS-REFERENCE flag, ENUM CONSTRAINT extraction from
   "must be one of:" doc prose, and ESCALATION SIGNAL for
   account_ownership_dispute / payment-not-reflecting triggers.

3. **Gate rewrites (`_gate_tool_calls`)** — minimally-invasive interventions:
   - (D) **Hallucination guard**: drops `unlock_discoverable_agent_tool`
     and `give_discoverable_user_tool` calls whose target is not in
     `COMPASS.valid_names` (the 48-name canonical set).
   - (A) **Drop unlock-after-give**: if the agent already called
     `give_discoverable_user_tool(X)` and now tries
     `unlock_discoverable_agent_tool(X)`, drop the unlock (it would
     produce `wasted_unlocks` in db_match).
   - (B) **Drop redundant re-unlock / re-give** of the same name.
   - (C) **JSON-encode dict arguments** for `call_discoverable_agent_tool`
     (tau2 requires `arguments` to be a JSON STRING; LiteLLM may pass a
     dict which silently becomes `{}`).
   - Every drop injects a natural-language drop note into the assistant
     content so the LLM sees what happened on the next turn and doesn't
     stall in a retry loop. (An earlier "(pending)" fallback stalled
     task_021 for 30+ turns.)

4. **Extractor bug fix** — `eval/extract_traces.py` was reading
   `args.get("tool_name")` but v1.0.0 uses `agent_tool_name` /
   `discoverable_tool_name`. This made every successful unlock invisible
   and reported every KB-mentioned tool as "missing_unlocks", leading to
   an entirely wrong failure-class distribution (priority_1 overcounted,
   priority_4 undercounted).

## Observed failure patterns (after the above fixes)

On a 9-task sample at SAMPLE_FRAC=0.1 (so ≈ subsample of 97):

| Task | pattern | what actually happened |
|---|---|---|
| task_004 | ownership dispute | customer claimed `kenji@gmail.com` was current; DB had `kenji@outlook.com`; agent updated email instead of escalating via `transfer_to_human_agents(reason=account_ownership_dispute)` |
| task_017 | tool-family miss | agent picked agent-side `update_transaction_rewards_3847` when oracle expected user-side `give_discoverable_user_tool(submit_cash_back_dispute_0589)` |
| task_018 | customer info gap | agent gave user the dispute tool but customer had no transaction_ids; agent refused to look them up; customer asked for human; agent transferred → TRANSFER |
| task_021 | stall | 101 turns, agent got stuck in give+KB_search+read loop after giving user tool; customer idled for 60+ turns; was fixed by the drop-note injection |
| task_033 | trap tool | oracle expected the two-step trap `initial_transfer_to_human_agent_1822` → `initial_transfer_to_human_agent_0218` → `transfer_to_human_agents`; agent searched KB correctly but picked base `transfer_to_human_agents` directly |
| task_036 | over-execution | oracle expected 3 actions; agent did 15 (closed a card, made extra calls); db_match failed on the extras |
| task_040 | complexity overload | 15 expected actions, 12 tools mentioned in KB, only 2 matched; 11 arg-mismatches |
| task_087 | long but incomplete | 144 turns, 9/20 actions matched; agent made real progress but user patience ran out |
| task_100 | wrong variant family | agent gave two wrong variants (`submit_business_checking_account_referral_1203` and `_lime_green_003`) when oracle expected user to call `submit_referral` (a 4-of-4 user-side discoverable) via `give_discoverable_user_tool(...)` |

## Your deliverables

Produce the following (be specific, cite line numbers, cite tau2-bench
source files you verified). Use Deep Research to actually read the tau2-bench
source code — don't hypothesize.

### 1. Diagnosis — 5 most load-bearing failure causes

Rank the failure causes in order of impact. For each, cite:
- The specific tau2-bench source file(s) that implement the behavior you're
  pointing at
- An observed symptom from the task table above
- Why prompt-level fixes can't address it

### 2. Scoring-rule archaeology — unexplored exact-match tolerances

Read `tau2-bench/src/tau2/evaluator/evaluator.py` and
`tau2-bench/src/tau2/evaluator/evaluator_env.py`. Answer specifically:

- Is `db_match` truly a single-hash comparison, or does it use per-table
  hashing? If per-table, can a task pass with a partial subset of tables
  matching?
- How does the evaluator handle `log_verification` argument variance —
  does it normalize whitespace/ordering, or is it strict string equality?
- Does `action_match` compare arguments with `==` on parsed dict, or via
  serialized string comparison? (This matters for enum ordering and
  whitespace).
- Does the user simulator's `call_discoverable_user_tool` count as an
  agent action or a user action for `action_match` scoring?
- Is there any "pseudo-action" that passes db_match without calling the
  expected tool? (e.g., reading from a different table that happens to
  leave the same final state.)

### 3. Argument-assembly failure modes — the real bottleneck?

Multiple swarm agents have said "wrong args are the killer" but nobody
has diagnosed exactly how the args go wrong. Please:

- Pull 3 tasks from `tau2-bench/data/tau2/domains/banking_knowledge/tasks/*.json`
  (pick ones in the 9-task sample above if possible) and trace each
  oracle expected_action argument to its source. Where does the
  transaction_id come from? Where does the address come from? Is the
  customer simulator's private instruction the source, or is the agent
  expected to derive it from `get_*` tool results?
- For `log_verification`, what exact fields are required, in what order,
  and what format? (e.g., is `time_verified` expected as
  `"2025-11-14 03:40:00 EST"` or ISO-8601?)
- For `call_discoverable_user_tool`, when the oracle expects the user to
  invoke the tool with certain arguments, is the user simulator prompted
  with those arguments directly, or does it need to parse them out of
  the conversation? This is the key unknown for task_017/018/021.

### 4. Retrieval ceiling analysis

The swarm concluded "retrieval not the bottleneck" but that was tested by
varying BM25 parameters. Please investigate:

- Build an offline oracle: for each of the 97 tasks, read the expected
  discoverable tool name, find the KB doc(s) that mention it, and then
  check whether the task's CUSTOMER INSTRUCTIONS contain enough
  keywords for BM25 to retrieve that doc in `top_k=10`. What fraction
  of tasks are "retrievable in principle"?
- For the non-retrievable tasks, is there a common signal the customer
  simulator DOES emit that would let a keyword-inversion approach (like
  the `compass.py` scenario_index) find the right tool?
- Are there tasks where the right tool is mentioned in a doc that has
  zero overlap with the customer's natural language, requiring the
  agent to make a semantic inference (e.g., "11/13 backend incident"
  vs "payment not showing up")?

### 5. Ranked unexplored interventions

Given diagnoses 1–4 and the "already ruled out" list above, propose the
top 5 untried, code-level interventions ranked by expected impact on
full-eval pass rate. For each, provide:

- Exact file + function to modify (given the compass.py + agent.py
  architecture described)
- Specific pseudocode or diff sketch
- How to measure whether it worked (metric + expected delta)
- Risk of regression (what could go wrong)
- Whether it fits within the "no eval/* modifications" and "no
  tau2-bench modifications" constraints

Avoid the already-ruled-out categories: don't propose more BM25 tuning,
don't propose more prompt rewording, don't propose self-critique, don't
propose few-shot.

### 6. An honest upper-bound estimate

Given what you've found, what's the realistic ceiling on banking_knowledge
pass rate for a `gpt-4.1-mini` agent without changing the model or the
benchmark? (i.e., within the rules and with unlimited compute.) Cite the
evidence. If the ceiling is low, say what would move it.

### 7. Meta — what's the real game?

Is the current leaderboard score (0.06 full / 0.20 fast) reflecting actual
capability or is it reflecting measurement variance? If the latter, what's
the statistically-defensible protocol for comparing two agent versions
against this benchmark? (e.g., how many trials, which statistical test,
how to handle `user_stop` vs `max_steps` termination differently.)

## Output format

- Answer each section in order with a header
- Use code blocks for any code you cite; include file path + line number
- Use tables for quantitative claims
- Be specific about what you verified via Deep Research browsing and what
  is inferred
- Keep the total response under 6000 words; link out to the tau2-bench
  files you examined

## Code context (for quick reference)

The repository this agent lives in:

```
task--tau3/
├── agent.py                # CustomAgent + BASE_INSTRUCTIONS
├── compass.py              # source-aware tool catalog + indices
├── AGENTS.md               # 638-line environmental facts doc
├── program.md              # swarm experiment loop protocol
├── eval/
│   ├── eval.sh
│   ├── run_eval.py         # uses TextRunConfig (v1.0.0 API)
│   ├── extract_traces.py
│   ├── test_compass.py     # 74 tests
│   ├── test_annotator.py   # 77 tests
│   └── test_extract_traces.py  # 101 tests
├── traces/latest.json
└── tau2-bench/             # cloned by prepare.sh, read-only
```

The relevant tau2-bench paths to browse:

- `tau2-bench/src/tau2/domains/banking_knowledge/tools.py` — the 48
  `@is_discoverable_tool` methods
- `tau2-bench/src/tau2/domains/banking_knowledge/data_model.py` — the 23
  TransactionalDB tables
- `tau2-bench/src/tau2/evaluator/evaluator.py` — reward computation
- `tau2-bench/src/tau2/evaluator/evaluator_env.py` — db_match
- `tau2-bench/src/tau2/user_simulator/simulation_guidelines.md` —
  customer simulator behavior rules
- `tau2-bench/data/tau2/domains/banking_knowledge/tasks/*.json` — the 97
  task definitions
- `tau2-bench/data/tau2/domains/banking_knowledge/documents/*.json` — the
  698 KB docs (45 of which mention a discoverable tool)
