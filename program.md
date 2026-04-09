# τ³-bench Customer Service Agent

Improve a customer service agent to maximize pass^1 accuracy on τ³-bench across four domains.

## Setup

1. **Create your branch**: `git checkout -b hive/<your-agent-id>` from current main.
2. **Read the in-scope files**:
   - `agent.py` — router that dispatches to domain agents. Rarely modified.
   - `domains/base.py` — shared logic (LLM calling, retry, parsing). Changes here affect ALL domains — requires full 4-domain eval.
   - `domains/airline.py` — airline agent (evolved from tau2, proven at 0.76)
   - `domains/retail.py` — retail agent (evolved from tau2)
   - `domains/telecom.py` — telecom agent (evolved from tau2)
   - `domains/banking.py` — banking knowledge agent (clean baseline, highest leverage)
   - `eval/eval.sh` — runs evaluation. Do not modify.
   - `eval/run_eval.py` — evaluation runner. Do not modify.
   - `prepare.sh` — installs τ²-bench. Do not modify.
3. **Run prepare**: `bash prepare.sh` to install τ²-bench with knowledge extras.
4. **Initialize results.tsv**: Create `results.tsv` with just the header row.
5. **Confirm and go**.

## The benchmark

τ³-bench evaluates customer service agents across four domains:
- **Airline** (20 tasks) — flight booking, cancellations, policy enforcement
- **Retail** (40 tasks) — returns, exchanges, order management
- **Telecom** (40 tasks) — connectivity issues, account management
- **Banking Knowledge** (97 tasks) — document retrieval, dispute resolution, account actions requiring policy lookup across 698 documents (~195K tokens)

Each task is a multi-turn conversation with a simulated customer.

## Domain-split architecture

The agent is split into isolated domain files so changes to one domain CANNOT regress another:

```
agent.py              ← thin router (detect domain → dispatch)
domains/
  base.py             ← shared: message conversion, LLM calling, retry
  airline.py          ← domain-specific: instructions, annotator, tool strategy
  retail.py           ← domain-specific
  telecom.py          ← domain-specific
  banking.py          ← domain-specific (highest leverage)
```

**Rules for the split:**
- To improve a specific domain, ONLY edit `domains/<domain>.py`
- To improve shared logic, edit `domains/base.py` — but this requires full 4-domain eval
- The `agent.py` router is rarely changed

## Staged evaluation (2-phase)

For fast iteration, use the DOMAIN env var to run a single domain:

```bash
# Phase 1: fast — only the domain you're changing
DOMAIN=banking_knowledge bash eval/eval.sh > run.log 2>&1

# Phase 2: regression gate — only if Phase 1 improved
bash eval/eval.sh > run.log 2>&1
```

For quick sampling: `SAMPLE_FRAC=0.2 DOMAIN=banking_knowledge bash eval/eval.sh`

## What you CAN modify

- `domains/airline.py` — instructions, annotator, tool_choice strategy
- `domains/retail.py` — instructions, annotator
- `domains/telecom.py` — instructions, annotator, loop-breaker threshold
- `domains/banking.py` — instructions, annotator, retrieval strategy, base instructions override
- `domains/base.py` — shared logic (requires full eval pass)

## What you CANNOT modify

- `eval/`, `prepare.sh`, or τ²-bench source code
- User simulator model
- Cannot install new packages

## Goal

Maximize aggregate pass^1 accuracy. The eval prints per-domain breakdowns:

```
airline_pass1:    60.0% (12/20)
retail_pass1:     80.0% (32/40)
telecom_pass1:    80.0% (32/40)
banking_knowledge_pass1: 10.0% (10/97)
---
accuracy:         0.436700
correct:          86
total:            197
cost_usd:         5.23
```

## Domain-specific guidance

### Airline, Retail, Telecom (evolved from τ²)
These carry over proven optimizations from 15 τ² experiments. Improvements are incremental.

Key learnings baked in:
- Telecom loop-breaker at 10 is CRITICAL (τ² exp4: removing it → massive regression)
- Retail annotations were REMOVED — they hurt telecom (τ² exp12)
- Airline annotations for cancellation rules and basic economy are net positive

### Banking Knowledge (clean baseline — highest leverage)
~25% best score. Completely different optimization surface:
- **Retrieval**: Agent has KB_search (BM25) and grep to search 698 documents
- **Discoverable tools**: Tool names are hidden in document prose — agent must find them, then use meta-tools to unlock
- **Multi-step execution**: 15+ dependent tool calls with exact computed values
- **Iterate here**: RAG strategy, query formulation, document parsing, procedure chaining

## Logging results

```
commit	accuracy	airline	retail	telecom	banking	cost_usd	status	description
```

Include per-domain pass^1 percentages (e.g. 60%A/80%R/80%T/10%B).

## Failure diagnosis with traces

After every eval run, `eval/eval.sh` automatically extracts failure traces to `traces/latest.json`. This is your diagnostic tool — use it to understand WHY tasks fail, not just which ones.

### Reading traces

```bash
# After a run, traces are auto-extracted. Read the summary:
python -c "import json; d=json.load(open('traces/latest.json')); s=d['summary']; print(json.dumps(s, indent=2))"

# Read specific failure traces (worst first):
python -c "
import json
traces = json.load(open('traces/latest.json'))['failure_traces']
for t in traces[:5]:  # top 5 worst
    print(f\"\\n{'='*60}\")
    print(f\"Task: {t['task_id']} | Domain: {t['domain']} | Reward: {t['reward']}\")
    print(f\"Termination: {t['termination_reason']} | Turns: {t['num_turns']}\")
    if 'actions_expected' in t:
        print(f\"Actions: {t['actions_matched']}/{t['actions_expected']} correct\")
    if 'action_details' in t:
        for a in t['action_details']:
            status = 'OK' if a['matched'] else 'MISS'
            print(f\"  [{status}] {a['expected_tool']}({a['expected_args']})\")
"

# Extract only banking failures for focused work:
python eval/extract_traces.py --domain banking_knowledge --top 10
```

### What to look for in traces

Each trace contains:
- `task_id`, `domain`, `reward` — which task, how badly it failed
- `termination_reason` — `agent_stop` (agent finished but wrong), `max_steps` (ran out of turns), `too_many_errors`, `agent_error`
- `actions_expected` / `actions_matched` — how many tool calls were right vs wrong
- `action_details` — per-action: expected tool name + args, whether it matched
- `db_match` — whether final DB state was correct
- `communicate_checks` — required info the agent should have told the user
- `conversation` — truncated transcript of the full multi-turn conversation
- `review_errors` — LLM-judge identified errors with severity

### Failure classification

Group failures by root cause before choosing an experiment:
1. **Retrieval miss** — agent never searched KB, or searched with wrong terms
2. **Discoverable tool error** — didn't unlock before calling, or gave agent tool to user (or vice versa)
3. **Verification skip** — acted on account without identity verification
4. **Wrong arguments** — called the right tool but with wrong values
5. **Over-action** — did something the policy doesn't allow
6. **Under-action** — stopped before completing all required steps
7. **Communication miss** — did the right actions but didn't tell the user required info
8. **Max steps** — ran out of turns (usually from search loops or repeated failures)

Prefer changes that fix a class of failures, not a single task.

### Overfitting rule

Do not add task-specific hacks. Use this test:
"If this exact task disappeared, would this still be a worthwhile improvement?"

## The experiment loop

LOOP FOREVER:

1. **THINK** — review results.tsv and per-domain breakdowns. Identify the weakest domain.
2. **DIAGNOSE** — read `traces/latest.json`. Classify the top 5-10 failures by root cause. Pick the most common failure class.
3. Choose a domain to improve. Edit `domains/<domain>.py`.
4. git commit
5. **Phase 1**: `DOMAIN=<domain> bash eval/eval.sh > run.log 2>&1` — target domain only
6. Read results: `grep "pass1:\|accuracy:" run.log`
7. **COMPARE TRACES** — re-read `traces/latest.json`. Check: did previously-failing tasks now pass? Did any passing tasks regress?
8. If target domain regressed or flat → discard immediately (`git reset --hard HEAD~1`)
9. **Phase 2**: `bash eval/eval.sh > run.log 2>&1` — full eval as regression gate
10. If aggregate improved and no domain dropped significantly → keep
11. Record in results.tsv (do not commit results.tsv)

**Timeout**: 60 min for single-domain, 90 min for full eval.

**NEVER STOP**: Once the loop begins, do NOT pause to ask the human. You are autonomous.
