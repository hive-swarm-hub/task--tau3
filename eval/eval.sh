#!/usr/bin/env bash
# Evaluate agent.py on τ³-bench banking_knowledge domain (97 tasks).
# Prints accuracy summary and auto-extracts failure traces for diagnosis.
#
# Usage:
#   bash eval/eval.sh                   # full eval
#   SAMPLE_FRAC=0.1 bash eval/eval.sh   # 10% subset for fast iteration
#
# Requires OPENAI_API_KEY in .env (copy .env.example to .env first).
set -euo pipefail

cd "$(dirname "$0")/.."

# Auto-load .env if it exists — lets users paste their key into .env once
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "ERROR: OPENAI_API_KEY is not set." >&2
    echo "" >&2
    echo "Set it one of these ways:" >&2
    echo "  1. cp .env.example .env && edit .env to paste your key" >&2
    echo "  2. export OPENAI_API_KEY=sk-... before running this script" >&2
    exit 1
fi

# τ²-bench v1.0.0 refuses to overwrite existing results.json and prompts
# interactively to resume. Since every experiment is a fresh run (agent.py
# changes between runs), we always delete the previous results first.
#
# tau2-bench is pip-installed, so the data/simulations dir it actually writes to
# lives next to the installed source tree (e.g. /Users/.../tau3/tau2-bench/),
# NOT the local submodule. Discover the path dynamically from the package.
TAU2_ROOT="$(python3 -c '
import os, tau2
d = os.path.dirname(tau2.__file__)
for _ in range(6):
    if os.path.isdir(os.path.join(d, "data", "simulations")):
        print(d); break
    parent = os.path.dirname(d)
    if parent == d: break
    d = parent
' 2>/dev/null || true)"

if [ -n "${TAU2_ROOT}" ]; then
    STALE_RESULTS="${TAU2_ROOT}/data/simulations/eval_banking_knowledge"
else
    STALE_RESULTS="tau2-bench/data/simulations/eval_banking_knowledge"
fi

if [ -d "$STALE_RESULTS" ]; then
    if ! rm -rf "$STALE_RESULTS"; then
        echo "[eval] WARNING: could not remove $STALE_RESULTS — tau2-bench may prompt interactively and hang" >&2
    fi
fi
# Belt + suspenders: also wipe the local submodule path in case it ever gets used
LOCAL_STALE="tau2-bench/data/simulations/eval_banking_knowledge"
if [ -d "$LOCAL_STALE" ] && [ "$LOCAL_STALE" != "$STALE_RESULTS" ]; then
    rm -rf "$LOCAL_STALE" || true
fi

echo "[eval] RETRIEVAL_VARIANT=${RETRIEVAL_VARIANT:-bm25}  SOLVER_MODEL=${SOLVER_MODEL:-gpt-4.1-mini}  EVAL_LITE=${EVAL_LITE:-0}" >&2

python eval/run_eval.py

# Auto-extract failure traces for meta-agent diagnosis
echo "" >&2
echo "=== EXTRACTING FAILURE TRACES ===" >&2
python eval/extract_traces.py
