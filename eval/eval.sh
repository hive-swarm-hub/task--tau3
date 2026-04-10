#!/usr/bin/env bash
# Evaluate agent.py on τ³-bench banking_knowledge domain (97 tasks).
# Prints accuracy summary and auto-extracts failure traces for diagnosis.
#
# Usage:
#   bash eval/eval.sh                   # full eval
#   SAMPLE_FRAC=0.1 bash eval/eval.sh   # 10% subset for fast iteration
set -euo pipefail

cd "$(dirname "$0")/.."
python eval/run_eval.py

# Auto-extract failure traces for meta-agent diagnosis
echo "" >&2
echo "=== EXTRACTING FAILURE TRACES ===" >&2
python eval/extract_traces.py
