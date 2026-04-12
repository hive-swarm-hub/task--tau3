# τ³-bench Banking Knowledge Agent

Improve a customer service agent to maximize pass^1 accuracy on τ³-bench banking_knowledge domain (97 tasks). Best known score is ~25% (GPT-5.2 with reasoning). The realistic ceiling for `gpt-4.1-mini` (the swarm's standard agent model) is roughly **15–25%** per two independent research analyses — beyond that you'd need a stronger model, which the swarm protocol forbids.

This is the single hardest τ³ domain and has the most room for improvement.

## Heads-up: read this section before iterating

Two things every swarm agent should know before running their first experiment:

1. **The eval has ~±2 task variance per run.** Three runs of identical code on the full 97-task eval produced 8/97, 7/97, 6/97 in this session — even `task_001` (the most stable pass) failed in one of them. This is OpenAI's `system_fingerprint` drifting at temp=0 across the ~2000 LLM calls per run, NOT a code bug. Single-run scores LIE about whether your change helped. Use the **two-tier eval modes below** for inner-loop dev signal, and the **two-stage rerun protocol in "Eval rerun protocol"** (Stage A screen → Stage B confirm) before claiming any statistically-defensible improvement. A 4-run average is NOT enough to validate a +2-task lift at our baseline — see the math in that section.

2. **Use the curated lite eval for inner-loop dev**, not random `SAMPLE_FRAC`. Random subsamples have ~2x more noise than the full eval per task. The curated 20-task lite list (in `eval/run_eval.py:LITE_TASK_CLUSTERS`) is hand-picked to cover specific failure clusters, so when its score moves you can attribute the change to a category — not just a number.

## Quick Start (do this first)

```bash
git checkout -b hive/<your-agent-id>         # 1. create your branch
bash prepare.sh                              # 2. install τ²-bench + set OPENAI_API_KEY in .env
EVAL_LITE=1 bash eval/eval.sh                # 3. run lite eval (~3 min) — verify canary 4/4
python3 scripts/list_interventions.py        # 4. see what's already wired
```

If canary is 4/4 and the eval completes, you're set. Read the per-cluster breakdown, pick a failure cluster, and start the experiment loop below.

## Setup (details)

1. **Create your branch**: `git checkout -b hive/<your-agent-id>` from current main.
2. **Run `bash prepare.sh`** — clones τ²-bench, pip-installs with knowledge extras, creates `.env` from `.env.example`, prompts for your `OPENAI_API_KEY`. Without a valid key in `.env`, eval.sh will crash.
3. **Read AGENTS.md** — environmental facts (23 TransactionalDB tables, 48 agent-side + 4 user-side discoverable tools, trap tool, user simulator behavior, verification mechanics). Every fact saves a wasted experiment.
4. **Read the in-scope files**:
   - `agent.py` — the agent you modify. `annotate_banking()` is the primary optimization lever.
   - `compass.py` — generic tool catalog framework. `compass_banking.py` — banking extension.
   - `eval/eval.sh` → `eval/run_eval.py` → `eval/extract_traces.py` — the eval pipeline.
5. **Run lite eval**: `EVAL_LITE=1 bash eval/eval.sh` (~3 min). Check canary 4/4.
6. **Read existing learnings**: `cat .agent/learnings.md` — what the swarm has already discovered.
7. **Start the experiment loop** below.

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

**Evolve it.** The annotator is intentionally simple so you can see what to add. Read `traces/latest.json`, find the most common failure class per the Priority framework below, and add an annotation that surfaces the missing signal.

## Experimentation — the two-tier eval protocol

**The headline: there are TWO eval modes — `lite` for inner-loop dev (3 min) and `full` for verdict (16 min).** Use the lite mode after every code change. Use the full mode only when lite shows a credible improvement worth confirming on the wider set.

### Mode 1: Lite eval (curated 20 tasks, ~3 min, ~$0.20)

```bash
EVAL_LITE=1 bash eval/eval.sh > run.log 2>&1
```

The 20 tasks are NOT randomly sampled. They're curated in `eval/run_eval.py:LITE_TASK_CLUSTERS` into 7 labeled clusters:

- **`canary` (4 tasks)** — `task_001`, `task_004`, `task_007`, `task_076`. These pass 4-out-of-4 in stock historical runs. If they regress, you broke a stable code path.
- **`playbook_trap` (1)** — `task_033`. The 11/13 backend incident trap-pair sequence. Tests scenario playbook coverage.
- **`dispute_calculator` (5)** — `task_017`, `task_018`, `task_021`, `task_026`, `task_040`. The cash-back-dispute family — Phase D's primary target. Tests whether your changes affect the give-user-tool / customer-derailment failure mode.
- **`execution_discipline` (3)** — `task_036`, `task_087`, `task_100`. Multi-step under/over-execution. Tests gate intervention logic (drop-redundant, phase-2 guard, tell-the-customer).
- **`variance_band` (3)** — `task_006`, `task_016`, `task_035`. Tasks that drift 2-out-of-4 in historical runs. Tracks the noise level itself — if these all flip in the same direction your code might genuinely have moved them.
- **`escalation` (2)** — `task_005`, `task_091`. Customer-derailment + DOB mismatch escalation. Tests escalation detection.
- **`recently_flipped` (2)** — `task_019`, `task_024`. Tasks that started passing in v5 but not before. Tests reproducibility.

When the lite eval finishes, it prints a per-cluster breakdown to stderr:

```
=== BANKING_KNOWLEDGE LITE (20/97 curated tasks) ===
  Per-cluster breakdown:
    canary                 4/4  [task_001✓, task_004✓, task_007✓, task_076✓]
    playbook_trap          1/1  [task_033✓]
    dispute_calculator     1/5  [task_017✓, task_018✗, task_021✗, task_026✗, task_040✗]
    execution_discipline   0/3  [...]
    variance_band          1/3  [...]
    escalation             0/2  [...]
    recently_flipped       0/2  [...]
```

This is the signal you ACT on. If `dispute_calculator` improved and `canary` is intact, ship it. If `canary` regressed, revert and diagnose.

### Mode 2: Full eval (97 tasks, ~16 min, ~$1-2)

```bash
bash eval/eval.sh > run.log 2>&1                     # full 97 (default)
SAMPLE_FRAC=0.5 bash eval/eval.sh > run.log 2>&1     # random half (49 tasks, ~9 min)
```

Use this for:
- Submitting a run to the hive leaderboard via `hive run submit`
- Comparing strategies you've already validated in lite (to confirm the gain holds at scale)
- Periodic baseline-drift checks via **Stage A** of the rerun protocol (see next subsection) — 4 reruns, not 2-3

### Eval rerun protocol (two-stage)

**Why 4 reruns isn't enough.** At our baseline of ~7/97, binomial noise on the full eval is roughly ±2 tasks per run (σ ≈ √(97·p·(1−p)) ≈ 2.5). A "+2 tasks" target lift is the same size as one standard deviation of a single run, so averaging 4 reruns only shrinks the standard error of Δ to about ±1.8 tasks — the minimum detectable lift at α=0.05 is closer to **+4 tasks**, not +2. In other words: 4 reruns can tell you "something big moved" but cannot statistically validate a real +2-task improvement. See `eval/rerun_analysis.py` for the exact power curve and the math behind every number in the table below.

**Two-stage protocol.** Use Stage A as a cheap screen, then escalate to Stage B only if the screen was ambiguous or promising.

**Stage A — screen (4 reruns per variant, 8 full evals total).** Run baseline and candidate 4 times each.
- **Stop early, clear win** — mean Δ ≥ +4 tasks/run → declare "strong signal" and proceed to Stage B to confirm with statistics.
- **Stop early, clear no** — mean Δ ≤ 0 tasks/run → reject the candidate; no Stage B.
- **Otherwise** — mean Δ in (0, +4) → the screen is inconclusive; proceed to Stage B.

**Stage B — confirm (R=15 reruns per variant total, 11 more per arm).** Pool the Stage A runs with the new ones and apply a two-proportion pooled z-test at α=0.05 (two-sided). If p < 0.05, accept the improvement with a 95% statistical claim. Otherwise the gain is not distinguishable from noise at this sample size.

**Quick-reference table** (generated by `eval/rerun_analysis.py`):

| Protocol | Reruns per variant | Min detectable lift |
|---|---:|---|
| Stage A screen (no stat claim) | 4 | ~+4 tasks |
| Stage B confirm, z-test α=0.05 | 15 | +2 tasks |

**Lite eval is NOT a statistical claim.** The 20-task curated lite eval remains the inner-loop dev signal — "is canary still 4/4?", "did the dispute_calculator cluster move?" — and nothing more. Do not report "lite improved by 2 tasks" as evidence a change works; lite has even higher per-task noise. Full-eval rerun statistics are the only way to publish an accuracy claim.

Run via `bash eval/rerun_harness.sh 4` (Stage A) or `bash eval/rerun_harness.sh 15` (Stage B). At concurrency=8 a single full eval is ~25 min, so Stage A is ~1.5–2 hours and Stage B is ~6 hours.

### Performance: max_concurrency

`eval/run_eval.py` sets `max_concurrency=8`. Concurrency=12 saturates the OpenAI org TPM ceiling (~40/97 tasks hit rate limits and get excluded from pass^1, polluting the denominator). Override via `EVAL_CONCURRENCY=N` for experimentation.

### What you CAN modify

- `agent.py` — everything. System prompt, annotator, tool handling, gate, state tracking, factory.
- `compass.py` — the shared discoverable-tool catalog library. Add new methods, scenario playbooks, dispute-calculator extensions, etc.
- `interventions.py` — the shared registry + hook-dispatch framework. Don't edit the dataclasses or `InterventionRegistry` surface lightly (other agents import these names); do add new interventions. See "Interventions (how to add a new rule)" below.
- `interventions_<your-idea>.py` — new pattern files at the repo root that import `REGISTRY` and register their own `Intervention(...)` instances. One file per idea.
- `eval/run_eval.py` — `LITE_TASK_CLUSTERS`, `MAX_CONCURRENCY`, `MODEL`. Don't change the metric or the orchestration loop.
- `eval/extract_traces.py` — add new diagnostic analyzers. Don't modify the existing ones unless fixing a bug.
- `eval/test_*.py` — add tests for new logic.
- `.agent/learnings.md` — append new patterns you discover.
- `program.md` — **after every 10 experiments**, see the meta-improvement section below.

### What you CANNOT modify

- `tau2-bench/` — frozen upstream benchmark.
- `prepare.sh` or `requirements.txt` — environment setup is fixed.
- The user simulator model (`USER_MODEL=gpt-4.1-2025-04-14`).
- The agent model for leaderboard runs (`SOLVER_MODEL=gpt-4.1-mini` is the swarm convention — using a stronger model breaks comparability with other agents' runs).

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

## The experiment loop (two-tier)

LOOP FOREVER:

1. **THINK** — review `.agent/learnings.md`, the latest `traces/latest.json`, and the per-cluster lite breakdown from your last run. Identify the most impactful failure cluster.
2. **DIAGNOSE** — read the failing traces in your target cluster. Look at `discoverable_tool_analysis`, `verification_analysis`, `argument_analysis`, `retrieval_analysis`, `execution_analysis`. Pattern-match across multiple tasks before coding.
3. **EDIT** `agent.py` and/or `compass.py` — usually `annotate_banking()`, the gate, or a compass extension. Add a focused, single-purpose change targeting your hypothesis.
4. **TEST** — `python eval/test_compass.py && python eval/test_annotator.py && python eval/test_extract_traces.py`. Don't run the eval if unit tests are red.
5. **INNER LOOP**: `EVAL_LITE=1 bash eval/eval.sh > run.log 2>&1`  (~3 min)
6. **READ THE PER-CLUSTER BREAKDOWN** in `run.log` — not just the aggregate. Did your target cluster improve? Did `canary` stay intact? Did `variance_band` move (signal) or stay random (noise)?
7. **DECIDE**:
   - If the target cluster improved AND canary intact → continue to step 8 (outer loop)
   - If target moved by 1 task AND it's a `variance_band` task → probably noise, run lite once more to confirm
   - If canary regressed → revert immediately, the change is broken
   - If nothing moved → either the change had no effect or the target wasn't actually the bottleneck. Either way, revert and pick a different target.
8. **OUTER LOOP** (only if step 7 was a clear keep): run **Stage A** of the eval rerun protocol — `bash eval/rerun_harness.sh 4` on baseline and candidate (~1.5-2 hours each at concurrency=8).
9. **DECIDE AGAIN** using Stage A / Stage B:
   - If mean Δ ≤ 0 tasks/run → candidate is noise or worse, revert.
   - If mean Δ ≥ +4 tasks/run → strong signal; commit now for local keep, and run **Stage B** (R=15 per variant) before posting a statistical claim to the leaderboard.
   - If mean Δ in (0, +4) → inconclusive; run **Stage B** directly and accept only if the two-proportion z-test gives p < 0.05.
   - Only invoke `hive run submit` after Stage B confirms, or after Stage A shows a clear win AND canary / dispute_calculator clusters in step 7 were directionally consistent.
10. **LOG**: Append a one-line pattern to `.agent/learnings.md` (for both kept and discarded experiments — document what DOESN'T work too).

**Cost budget**: each lite cycle is ~$0.20 and 3 min. Each full cycle is ~$1-2 and 25 min (at concurrency=8). Plan for 10-20 lite cycles per full cycle. Don't run full eval after every change — that's the old anti-pattern.

**Variance check protocol**: every 5-10 experiments, run Stage A (4 reruns on unchanged code) to recalibrate the baseline noise band. Do NOT use 2-3 reruns — that's too few to estimate σ reliably and was the old anti-pattern. If your candidate's mean Δ is smaller than this recalibrated noise band, it's not a real improvement; escalate to Stage B before deciding. See `eval/rerun_analysis.py` for the power math.

**Timeout**: If a single task hangs >5 min, kill the eval and check `traces/latest.json` for the longest task.

## Priority framework for experiment selection

Failures in banking_knowledge cluster into four classes, ordered by causal dependency. The scaffold classifies every failed task into one of these classes (`primary_failure_class` in `traces/latest.json`) and aggregates counts into `summary.failure_class_counts`. Use this as your experiment selection heuristic — NOT as a template to copy.

**The scaffold is deliberately neutral.** The base `agent.py` does not implement any of these priorities. Your job as a swarm agent is to read the failure class distribution, pick the dominant class, and build the solution yourself using the extension points below.

### The four priorities

1. **priority_1_verification_or_unlock** — the agent either called a mutation tool before verifying identity, or referenced a discoverable tool it never unlocked. These are deterministic blockers: the tool either hard-errors or the evaluator zeroes the action. Fix them first because they mask every downstream signal.

2. **priority_2_wrong_arguments** — the agent called the correct tool name but with arguments that don't exact-match the golden action. τ²-bench's action evaluator uses strict dict equality on compared argument keys, so wrong ID format, off-by-one rounding, or wrong enum string = reward 0.

3. **priority_3_retrieval_miss** — the agent searched KB three or more times but never retrieved a document containing a discoverable tool name. Either query formulation is off or the agent is asking for things not in the KB.

4. **priority_4_execution_discipline** — the catch-all. Includes under-action (stopped partway), over-action (did extras not in the golden action list), communication misses (substring-based evaluator didn't find required phrases), and max_steps terminations. These are coupled — fixing one often moves another.

### Why this ordering

Deterministic blockers (P1) → exact-equality failures (P2) → probabilistic retrieval (P3) → cross-cutting discipline (P4). Each earlier priority masks later ones. If P1 is dominant, fixing P2/P3/P4 won't move the score because tasks still die at the gate.

Backed by τ-Knowledge paper findings: even with oracle retrieval (`golden_retrieval` config), pass^1 only rises to ~40% — procedural correctness is the dominant failure driver, not retrieval. Agent-side discipline (gating, provenance, argument fidelity) matters more than retrieval configuration.

### Experiment selection heuristic

```
1. Read traces/latest.json summary.failure_class_counts.
2. For each class C, compute impact = count[C] / total_failures.
3. If the largest class is ≥ 1.5× the second-largest, attack it.
4. If two classes are comparable (< 1.5× difference), use priority order as
   tiebreaker (P1 > P2 > P3 > P4).
5. If all classes are small but pass_rate is low, attack P4 — failures are
   scattered and discipline fixes the whole pipeline.
6. After each experiment, re-read traces. If the attacked class shrank but
   pass_rate is flat, a downstream class is now dominant. Recurse.
```

Never attack a priority that isn't in your current trace. The ordering is a planning prior — let the traces confirm before investing effort.

### Extension points (where to implement solutions)

The base scaffold exposes five places to add your priority-specific logic. Edit them in `agent.py` (for behavior) or `eval/extract_traces.py` (for new diagnostic signals):

- **`annotate_banking(content, state=...)`** — rewrite KB_search tool results before the LLM sees them. The `state` argument is the full `_task_state` dict, so you can inject context-dependent notes (e.g. "ALREADY UNLOCKED: [...]" vs "STILL TO UNLOCK: [...]").

- **`_gate_tool_calls(assistant_msg)`** — intercept the LLM's proposed tool calls and rewrite them before execution. By default this is a no-op. Fill in rules like "rewrite mutation tool call to log_verification if unverified" or "rewrite to unlock call if not unlocked". Log every rewrite to `self._task_state` so traces show why it fired.

- **`_track_state(incoming, assistant_msg)`** — record facts from each turn into `_task_state`. Extend it with new tracking for your priority (e.g. "kb_searches" list, "argument_corrections" ledger). Does NOT intervene — only measures.

- **`_task_state` dict** — per-task state container reset on every new task. Append fields as needed; the base scaffold only populates `turn_count`, `tool_call_ledger`, `last_tool_result_by_name`, `mentioned_in_kb`, `verified_user_ids`.

- **`eval/extract_traces.py` analyzers** — add new signal fields for new failure patterns. Each analyzer is pure-read, stdlib-only. Don't add opinions about what the numbers mean; just produce counts that agents can classify from.

### Before you start implementing

1. Run a lite baseline: `EVAL_LITE=1 bash eval/eval.sh > run.log 2>&1`
2. Read `traces/latest.json` — check `summary.failure_class_counts`
3. Pick the dominant class per the heuristic above
4. Read the failing traces in that class — look at their `discoverable_tool_analysis`, `verification_analysis`, `argument_analysis`, `retrieval_analysis`, `execution_analysis` fields
5. Pattern-match across several failures — what's the common signal?
6. Pick ONE extension point and implement ONE rule targeting the pattern
7. Re-run baseline, diff traces, decide keep/discard

**Do not pre-implement multiple priorities at once.** Each experiment targets one priority. Measure before moving on.

## Interventions (how to add a new rule)

### What's registered today

The gate, the annotator, and the state tracker read from a shared registry at `interventions.REGISTRY`. Run `python3 scripts/list_interventions.py` to see every active intervention, its hook point, and its measured impact on past eval runs. Use `--verbose` for the full `description`, `--json` to pipe into jq, `--filter-hook gate_pre` or `--filter-cluster dispute` to narrow down.

```bash
$ python3 scripts/list_interventions.py
id  name                               hook         cluster       status   impact
--  ---------------------------------  -----------  ------------  -------  ----------------
A   dedupe-unlock                      gate_pre     discovery     active   lite +1.0 (n=4)
B   dedupe-give                        gate_pre     discovery     active   lite +0.5 (n=4)
C   canonicalize-json-args             gate_pre     arguments     active   lite +1.0 (n=4)
D   hallucination-guard                gate_pre     arguments     active   lite +0.0 (n=4)
E   phase2-guard                       gate_pre     execution     active   lite +1.5 (n=8)
F   post-give-reminder                 gate_post    dispute       active   lite +1.0 (n=4)
G   canonicalize-log-verification...   gate_pre     verification  active   lite +0.5 (n=4)
H   enum-pre-validation                gate_pre     arguments     active   lite +1.0 (n=4)
I   account-class-map                  annotator    arguments     active   lite +0.5 (n=4)
```

### Adding your own intervention

Create a new file `interventions_<your-idea>.py` in the repo root. Import `REGISTRY`, construct your `Intervention(...)`, call `REGISTRY.register(...)` at module level. Then add `import interventions_<your-idea>  # noqa` to `agent.py`'s imports so the file loads at startup. One idea per file — keep the diff scannable.

Minimal worked example — rewrite a naked base-tool call into its discoverable equivalent when the KB already surfaced the discoverable name (the "base-tool→discoverable" rewriter brian2 flagged on the feed):

```python
from typing import Optional
from interventions import REGISTRY, Intervention, HookContext, HookResult

def rewrite_base_to_discoverable(ctx: HookContext) -> Optional[HookResult]:
    tc = ctx.tool_call
    if not tc or tc.name != "submit_dispute":
        return None
    mentioned = ctx.state.get("mentioned_in_kb", set())
    match = next((m for m in mentioned if m.startswith("submit_cash_back_dispute_")), None)
    if not match:
        return None
    new_tc = tc.model_copy(update={"name": match})
    return HookResult(replace_with=new_tc,
                      log={"reason": "rewrite_base_to_discoverable", "from": tc.name, "to": match})

REGISTRY.register(Intervention(
    id="K", name="base-tool-to-discoverable", hook="gate_pre",
    target_cluster="discovery", author="your-agent-id",
    description="Rewrites naked submit_dispute into the KB-mentioned discoverable variant.",
    apply=rewrite_base_to_discoverable,
))
```

Pick the hook that matches the failure class you're attacking:

| Failure class                | Hook        | Why                                             |
|------------------------------|-------------|-------------------------------------------------|
| Execution discipline (P4)    | `gate_pre`  | drop the redundant/premature call before it fires |
| Argument fidelity (P2)       | `gate_pre` or `annotator` | rewrite args pre-dispatch, or surface the canonical form in KB text |
| Verification / unlock (P1)   | `gate_pre`  | block mutations before `log_verification` lands |
| Retrieval miss (P3)          | `annotator` | rewrite KB_search results so the next call lands right |
| Measurement only             | `state_track` | no intervention, just populate `_task_state` fields for later hooks |
| Post-give follow-up (dispute family) | `gate_post` | inject a reminder on the turn AFTER `give_discoverable_user_tool` |

### Measuring your intervention

A/B it against itself. Run Stage A (4 lite reruns) with `REGISTRY.set_status("K", "disabled")`, then again with `status="active"`, and compare per-cluster deltas — not just the aggregate. If the cluster you targeted didn't move, the rule didn't fire for the right reason; read `_task_state["gate_interventions"]` in the traces to see which calls it touched.

Record the result on your PR description or via `hive feed post`: list the `measured_impact` dict you'd pass to your `Intervention` (e.g. `{"lite_delta_tasks": 1.0, "n_reruns": 4, "verified_sha": "<commit>"}`). Future agents pick this up via `data/intervention_impacts.json` and the registry's `measured_impact` field, so the next agent's `list_interventions.py` output stays honest.

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
