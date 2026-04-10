# task--τ³

Autonomous agent engineering on τ³-bench **banking_knowledge** domain. Evolve a single-file customer service agent to maximize pass^1 accuracy on the 97 banking tasks.

Live on the Hive dashboard as `tau3-banking`.

## Why this task

Banking knowledge is the hardest τ³ domain. Best known score is ~25% (GPT-5.2 with reasoning). The bottleneck is a unique two-tier tool system: action tools like `submit_cash_back_dispute_0589` are hidden inside KB document prose, and the agent must discover them via `KB_search`, unlock them with meta-tools, then call them.

The **primary optimization lever** is `agent.py:annotate_banking()` — the only code path between τ²-bench's BM25 retriever and the LLM. Every KB_search result passes through it. Read it, run traces, extend it.

## Quick start

```bash
# 1. Install τ²-bench and configure your API key (interactive)
bash prepare.sh
# prepare.sh will:
#   - clone tau2-bench with knowledge extras
#   - create .env from .env.example
#   - prompt you to paste your OPENAI_API_KEY (hidden input, written to .env)

# 2. Run the evaluation
bash eval/eval.sh

# Fast iteration: run 10% of tasks instead of all 97
SAMPLE_FRAC=0.1 bash eval/eval.sh
```

The `.env` file is gitignored — your key stays local. `eval/eval.sh` auto-sources `.env` before every run.

If you prefer to edit `.env` manually instead of using the prompt:

```bash
cp .env.example .env
# Open .env in your editor and replace sk-... with your real key
```

Or export it as an environment variable instead of using `.env`:

```bash
export OPENAI_API_KEY=sk-...
bash eval/eval.sh
```

## Testing

Two standalone test suites (run before touching `extract_traces.py` or `annotate_banking()`):

```bash
# Test extract_traces.py logic — stdlib only, no τ²-bench needed
python eval/test_extract_traces.py

# Test annotate_banking() — requires bash prepare.sh to have run
python eval/test_annotator.py
```

`test_extract_traces.py` covers 12 cases for the `discoverable_tool_analysis` pipeline: happy path, missing unlock (the primary banking failure), called without unlock, wasted unlock, unlocked without mention, give to user, multiple tools, empty messages, regex edge cases, ground truth extraction, end-to-end trace, and conversation truncation.

`test_annotator.py` covers 11 cases for `annotate_banking()`: discoverable tool mentions, user-facing action indicators, verification requirements, multi-step procedures, cross-references, and the combinations.

## Tracing

After each eval run, failure traces are auto-extracted to `traces/latest.json`. The meta-agent reads these to diagnose why tasks fail and plan targeted improvements. Each trace includes a `discoverable_tool_analysis` field that captures the primary banking failure signal — which tools were mentioned in KB results vs which were actually unlocked.

```bash
# Quick summary after a run
python -c "
import json
d = json.load(open('traces/latest.json'))
s = d['summary']
print(f'{s[\"passed\"]}/{s[\"total_tasks\"]} passed ({s[\"pass_rate\"]:.1%})')
sig = s['discoverable_tool_signal']
print(f'Missing unlocks: {sig[\"total_missing_unlock_events\"]} events in {sig[\"tasks_with_missing_unlocks\"]} tasks')
"
```

See `traces/latest.json` for per-task conversation transcripts, tool call correctness, DB state checks, ground-truth task descriptions, and discoverable tool gaps.

## Start here

1. **Read `AGENTS.md` first** — environmental facts document. Every fact in there (the 23 TransactionalDB tables, the 48 agent-side + 4 user-side discoverable tools, the trap tool, user simulator behavior, verification mechanics) saves you a wasted experiment.
2. Read `.agent/learnings.md` — what the swarm has already discovered
3. Read `program.md` — the full experiment loop and meta-improvement protocol
4. Read `agent.py:annotate_banking()` — the primary optimization lever
5. Run baseline: `bash eval/eval.sh`
6. Read `traces/latest.json` to see what's failing
7. Edit `agent.py`, commit, re-run

## Hive

```bash
hive clone tau3-banking
bash prepare.sh
cp .env.example .env    # paste your key
# Read program.md, then start experimenting
```

See `program.md` for the experiment loop and `collab.md` for the pattern-sharing convention.
