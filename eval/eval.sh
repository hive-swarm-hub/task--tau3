#!/usr/bin/env bash
# Evaluate agent on τ³-bench.
#
# Usage:
#   bash eval/eval.sh                          # all 4 domains
#   DOMAIN=banking_knowledge bash eval/eval.sh  # single domain (fast iteration)
#   DOMAIN=airline bash eval/eval.sh            # single domain
#   SAMPLE_FRAC=0.2 bash eval/eval.sh           # 20% subset
set -euo pipefail

cd "$(dirname "$0")/.."
python eval/run_eval.py

# Auto-extract failure traces for meta-agent diagnosis
echo "" >&2
echo "=== EXTRACTING FAILURE TRACES ===" >&2
if [ -n "${DOMAIN:-}" ]; then
    python eval/extract_traces.py --domain "$DOMAIN"
else
    python eval/extract_traces.py
fi
