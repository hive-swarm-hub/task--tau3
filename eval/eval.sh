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
# changes between runs), we always archive-then-delete the previous results
# first. Archiving preserves the raw per-task JSON for later auditing (the
# rerun protocol needs this for reproducibility claims).
STALE_RESULTS="tau2-bench/data/simulations/eval_banking_knowledge"
if [ -d "$STALE_RESULTS" ]; then
    ARCHIVE_ROOT="eval_runs/archive"
    ARCHIVE_TS="$(date +%Y%m%d%H%M%S)"
    ARCHIVE_DEST="${ARCHIVE_ROOT}/eval_banking_knowledge_${ARCHIVE_TS}"
    mkdir -p "${ARCHIVE_ROOT}"
    # `mv` is atomic on the same filesystem and avoids a copy. If it fails
    # (e.g. cross-device), fall back to rm so the eval can still proceed.
    if ! mv "$STALE_RESULTS" "${ARCHIVE_DEST}" 2>/dev/null; then
        echo "WARNING: could not archive $STALE_RESULTS to ${ARCHIVE_DEST}; deleting instead" >&2
        rm -rf "$STALE_RESULTS"
    fi
fi

python eval/run_eval.py

# Auto-extract failure traces for meta-agent diagnosis
echo "" >&2
echo "=== EXTRACTING FAILURE TRACES ===" >&2
python eval/extract_traces.py
