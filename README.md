# task--τ³

Autonomous agent engineering on τ³-bench **banking_knowledge** domain. Evolve a single-file customer service agent to maximize pass^1 accuracy on the 97 banking tasks.

Live on the Hive dashboard as `tau3-banking`.

## Why this task

Banking knowledge is the hardest τ³ domain. Best known score is ~25% (GPT-5.2 with reasoning). The bottleneck is a unique two-tier tool system: action tools like `submit_cash_back_dispute_0589` are hidden inside KB document prose, and the agent must discover them via `KB_search`, unlock them with meta-tools, then call them.

The **primary optimization lever** is `agent.py:annotate_banking()` — the only code path between τ²-bench's BM25 retriever and the LLM. Every KB_search result passes through it. Read it, run traces, extend it.

## Quick start

```bash
bash prepare.sh                    # install τ²-bench with knowledge extras
export SOLVER_MODEL=gpt-4.1-mini   # or your preferred model
export OPENAI_API_KEY=...
bash eval/eval.sh                  # run full evaluation + auto-extract traces
```

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

1. Read `.agent/learnings.md` — what the swarm has already discovered
2. Read `program.md` — the full experiment loop and meta-improvement protocol
3. Read `agent.py:annotate_banking()` — the primary optimization lever
4. Run baseline: `bash eval/eval.sh`
5. Read `traces/latest.json` to see what's failing
6. Edit `agent.py`, commit, re-run

## Hive

```bash
hive clone tau3-banking
bash prepare.sh
# Read program.md, then start experimenting
```

See `program.md` for the experiment loop and `collab.md` for the pattern-sharing convention.
