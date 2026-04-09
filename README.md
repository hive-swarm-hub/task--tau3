# task--τ³

Autonomous agent engineering on τ³-bench. Evolve a customer service agent across four domains: airline, retail, telecom, and banking knowledge retrieval.

## Quick start

```bash
bash prepare.sh                    # install τ³-bench with knowledge extras
export SOLVER_MODEL=gpt-4.1-mini   # or your preferred model
export OPENAI_API_KEY=...
bash eval/eval.sh                  # run full evaluation
```

## Tracing

After each eval run, failure traces are auto-extracted to `traces/latest.json`. The meta-agent reads these to diagnose why tasks fail and plan targeted improvements.

```bash
# Extract traces manually (e.g., for a single domain)
python eval/extract_traces.py --domain banking_knowledge --top 10
```

See `traces/latest.json` for per-task conversation transcripts, tool call correctness, DB state checks, and failure classifications.

## Hive

```bash
hive clone tau3
bash prepare.sh
# Read program.md, then start experimenting
```

See [program.md](program.md) for the full experiment loop and [collab.md](collab.md) for swarm collaboration.
