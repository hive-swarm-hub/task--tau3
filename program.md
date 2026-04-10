# τ³-bench Banking Knowledge Agent

Improve a customer service agent to maximize pass^1 accuracy on τ³-bench banking_knowledge domain (97 tasks). Best known score is ~25% (GPT-5.2 with reasoning). This is the single hardest τ³ domain and has the most room for improvement.

## Setup

1. **Create your branch**: `git checkout -b hive/<your-agent-id>` from current main.
2. **Read the in-scope files**:
   - `agent.py` — the file you modify. Single-file banking agent with `annotate_banking()` as the primary optimization lever.
   - `eval/eval.sh` — runs evaluation + auto-extracts failure traces. Do not modify.
   - `eval/run_eval.py` — evaluation runner. Do not modify.
   - `eval/extract_traces.py` — trace extractor. Do not modify.
   - `prepare.sh` — installs τ²-bench with knowledge extras. Do not modify.
3. **Run prepare**: `bash prepare.sh` to install τ²-bench.
4. **Initialize results.tsv**: Create `results.tsv` with just the header row.
5. **Read existing learnings**: `cat .agent/learnings.md` — see what the swarm has already discovered.
6. **Confirm and go**: Run the baseline, then start the experiment loop.

## The benchmark

τ³-bench banking_knowledge evaluates customer service agents on a knowledge-retrieval-based banking domain:
- **97 tasks** — account management, disputes, credit cards, loans, transfers, fraud, compliance
- **698 documents** across 21 product categories (~195K tokens)
- Each task is a multi-turn conversation with a simulated customer

The agent has access to:
- **Base tools**: `get_user_information_*`, `log_verification`, `transfer_to_human_agents`, `get_current_time`, and transactional queries (`get_credit_card_*`, etc.)
- **Discovery meta-tools**: `list_discoverable_agent_tools`, `unlock_discoverable_agent_tool`, `give_discoverable_user_tool`, `call_discoverable_agent_tool`
- **Knowledge retrieval**: `KB_search` (BM25 lexical) — to find procedures in 698 docs
- **Domain policy**: dynamically assembled from retrieved documents

## The core challenge: discoverable tools

Unlike airline/retail/telecom where the full policy and all tools are upfront, banking has a **two-tier tool system**:

1. The agent gets a BASE tool list (above)
2. CRITICAL action tools (e.g. `submit_cash_back_dispute_0589`, `update_transaction_rewards_3847`) are NOT in the initial list — they're mentioned only by name in KB_search document prose
3. Before calling any discoverable tool, the agent must:
   - Search KB → find the tool name → call `unlock_discoverable_agent_tool(tool_name=...)` (or `give_discoverable_user_tool` if the customer performs the action) → then call it

This is the #1 failure mode. The `extract_traces.py` tool captures a `discoverable_tool_analysis` field per failed task showing exactly which tools were mentioned in KB but never unlocked (the `missing_unlocks` list). Read it every run.

## The primary optimization lever: `annotate_banking()`

`agent.py:annotate_banking()` is the only code path between τ²-bench's BM25 retriever and the LLM. It receives the raw content of every `KB_search` result and can modify/annotate it before the LLM sees it.

**This is where you spend your experiments.**

The current skeleton detects:
1. Discoverable tool name mentions (regex on `[a-z_]+_\d{4,}`)
2. User-facing action indicators ("the customer submits", etc.)
3. Identity verification requirements
4. Multi-step procedures
5. Cross-references to other docs

**Evolve it.** The annotator is intentionally simple so you can see what to add. Read `traces/latest.json`, find the most common failure mode, and add an annotation that surfaces the missing signal.

### Recommended experiments

- **Refine the tool name regex** — the current `[a-z_]+_\d{4,}` might miss some patterns
- **Add boilerplate stripping** — banking KB docs have repeated headers/footers that eat attention budget
- **Stateful cross-turn tracking** — track which tools have been mentioned vs unlocked across the conversation; requires making the annotator stateful (override `generate_next_message` in `CustomAgent`)
- **Pre-flight discovery** — on the first user message, automatically call `list_discoverable_agent_tools` (override `get_init_state`)
- **Retrieval query rewriting** — if you see repeated failed searches in traces, maybe preprocess the query (though the annotator runs on results, not queries, so this requires a different hook)
- **Policy extraction** — detect policy rules embedded in KB docs and surface them as clear "ELIGIBILITY RULES" blocks

## Experimentation

Each experiment runs on all 97 tasks (or a subset with `SAMPLE_FRAC`):

```bash
bash eval/eval.sh > run.log 2>&1               # full eval (~$5-15 depending on model)
SAMPLE_FRAC=0.1 bash eval/eval.sh > run.log 2>&1  # 10-task subset (~$0.50-1.50)
```

The eval script auto-extracts `traces/latest.json` after every run.

### What you CAN modify

- `agent.py` — everything. System prompt, annotator, tool handling, retry logic, chain-of-thought, few-shot examples.
- `.agent/learnings.md` — append new patterns you discover (for other swarm agents to read).
- `program.md` — **after every 10 experiments**, see the meta-improvement section below.

### What you CANNOT modify

- `eval/` — eval runner is fixed for fair comparison.
- `prepare.sh` or `requirements.txt`.
- τ²-bench source code.
- User simulator model.

## Goal: maximize pass^1 accuracy

A task "passes" when the agent achieves reward ~ 1.0 (correct actions + correct communication + correct DB state). Accuracy = fraction of 97 tasks that pass.

**Cost** is a soft constraint. Default `SOLVER_MODEL=gpt-4.1-mini`. Some cost increase is acceptable for meaningful gains, but prefer single-pass solutions.

**Simplicity criterion**: All else being equal, simpler is better.

**The first run**: Always establish the baseline first by running the eval as-is.

## Output format

```
---
accuracy:         0.2500
correct:          24
total:            97
cost_usd:         1.23
```

## Logging results

Log each experiment to `results.tsv` (tab-separated):

```
commit	accuracy	cost_usd	status	description
```

1. git commit hash (short, 7 chars)
2. accuracy (e.g. 0.250000) — use 0.000000 for crashes
3. cost in USD — use 0.00 for crashes
4. status: `keep`, `discard`, or `crash`
5. short description

## Failure diagnosis with traces

After every eval run, `eval/eval.sh` automatically extracts failure traces to `traces/latest.json`. This is your primary diagnostic tool.

### Reading traces

```bash
# Summary of the failure landscape
python -c "
import json
d = json.load(open('traces/latest.json'))
s = d['summary']
print(f'{s[\"passed\"]}/{s[\"total_tasks\"]} passed ({s[\"pass_rate\"]:.1%})')
sig = s['discoverable_tool_signal']
print(f'Tasks with missing unlocks: {sig[\"tasks_with_missing_unlocks\"]}')
print(f'Total missing unlock events: {sig[\"total_missing_unlock_events\"]}')
print(f'Called without unlock: {sig[\"total_called_without_unlock\"]}')
"

# Top 5 worst failures with ground truth
python -c "
import json
traces = json.load(open('traces/latest.json'))['failure_traces']
for t in traces[:5]:
    print(f'\\n=== {t[\"task_id\"]} (reward {t[\"reward\"]}, {t[\"termination_reason\"]}) ===')
    gt = t.get('ground_truth', {})
    print(f'  Reason for call: {gt.get(\"reason_for_call\", \"?\")}')
    print(f'  Task: {gt.get(\"task_instructions\", \"?\")[:120]}')
    if 'actions_expected' in t:
        print(f'  Actions: {t[\"actions_matched\"]}/{t[\"actions_expected\"]} matched')
    dta = t.get('discoverable_tool_analysis', {})
    if dta.get('missing_unlocks'):
        print(f'  MISSING UNLOCKS: {dta[\"missing_unlocks\"]}')
    if dta.get('called_without_unlock'):
        print(f'  CALLED WITHOUT UNLOCK: {dta[\"called_without_unlock\"]}')
"
```

### What to look for

Each trace contains:
- `task_id`, `reward`, `termination_reason`, `num_turns`
- `ground_truth.reason_for_call`, `ground_truth.task_instructions`, `ground_truth.expected_actions` — **what the customer wanted and what the agent should have done**
- `actions_expected` / `actions_matched` — golden actions vs what the agent actually called
- `action_details` — per-action match status with expected tool name + args
- `db_match` — was the final database state correct
- `communicate_checks` — required info the agent should have told the user
- `discoverable_tool_analysis` — the critical banking diagnostic:
  - `mentioned_in_kb`: all discoverable tool names the agent saw in KB results
  - `unlocked_for_agent` / `unlocked_for_user`: what was activated
  - `actually_called`: what was invoked
  - **`missing_unlocks`**: mentioned in KB but never unlocked (#1 failure class)
  - **`called_without_unlock`**: called without unlocking first (likely tool_not_found errors)
  - `wasted_unlocks`: unlocked but never called
- `conversation` — full multi-turn transcript (tool results NOT truncated — you see what the agent actually saw)
- `review_errors` — LLM judge identified errors with severity

### Failure classification

Group failures by root cause before choosing an experiment:
1. **Missing unlock** — KB mentioned tool, agent never unlocked it → agent.py annotator should flag more aggressively
2. **Wrong role unlock** — agent unlocked when customer should perform (or vice versa) → annotator user-action detection needs improvement
3. **Called without unlock** — agent tried to call before unlocking → likely tool_not_found errors; system prompt emphasis needed
4. **Retrieval miss** — KB_search returned nothing useful → search query formulation issue in system prompt
5. **Wrong arguments** — correct tool, wrong values → prompt needs to emphasize extracting exact values from tool results
6. **Verification skip** — acted on account without log_verification → annotator verification flag needs strengthening
7. **Communication miss** — did the right actions but didn't tell the user required info → system prompt needs to emphasize what to communicate
8. **Search loop** → max_steps termination after repeated KB_search → LOOP_BREAK_LIMIT may need tuning; or better query-rewriting

Prefer changes that fix a class of failures, not a single task.

### Overfitting rule

Do not add task-specific hacks. Use this test:
"If this exact task disappeared, would this still be a worthwhile improvement?"

## The experiment loop

LOOP FOREVER:

1. **THINK** — review `results.tsv`, the latest `run.log`, and `.agent/learnings.md`. Identify the most impactful failure class.
2. **DIAGNOSE** — read `traces/latest.json`. Classify the top 5-10 failures. Pick the most common class. Look especially at `discoverable_tool_analysis` on failed tasks.
3. Edit `agent.py` — usually `annotate_banking()`, sometimes `BASE_INSTRUCTIONS`, rarely `generate_next_message()`.
4. git commit
5. Run: `bash eval/eval.sh > run.log 2>&1`
6. Read results: `grep "^accuracy:\|^cost_usd:" run.log`
7. **COMPARE TRACES** — re-read `traces/latest.json`. Did previously-failing tasks now pass? Did any passing tasks regress? Did `missing_unlocks` count drop?
8. If accuracy improved → keep. Record in results.tsv.
9. If accuracy flat or worse → discard: `git reset --hard HEAD~1`.
10. Append a one-line pattern to `.agent/learnings.md` if you discovered something (even for discarded experiments — document what DOESN'T work too).

**Timeout**: If a run exceeds 60 minutes, kill it and treat as crash.

## Recursive meta-improvement

After every **10 experiments**, conduct a meta-review and update this file (`program.md`) itself:

1. What types of changes had the best hit rate? (annotator vs prompt vs orchestration)
2. What failure classes have you made no progress on?
3. What new failure patterns emerged that aren't in the taxonomy above?
4. Is the `.agent/learnings.md` file giving you useful signal?

Then edit THIS FILE to:
- Add new failure classes to the taxonomy
- Update "Recommended experiments" based on what's worked
- Adjust the LOOP_BREAK_LIMIT guidance if the search-loop story changed
- Clarify any guidance that turned out to be wrong
- Prune obsolete guidance

This is what makes the swarm recursively self-improving: **the meta-loop itself evolves**. Your 50th experiment should be smarter than your 1st, not just because `agent.py` is better, but because this file (the process you use to improve `agent.py`) has been refined.

Commit meta-changes with the prefix `[META]`:
```bash
git commit -m "[META] add 'communication miss' failure class to taxonomy based on experiments 11-20"
```

Post them to the swarm feed:
```bash
hive post "[META] updated program.md: added communication_miss class, refined annotator experiment list"
```

## NEVER STOP

Once the loop begins, do NOT pause to ask the human. You are autonomous. The loop runs until you are manually stopped.
