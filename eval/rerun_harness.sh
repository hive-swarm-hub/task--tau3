#!/usr/bin/env bash
# Run N full τ³-bench evals sequentially and aggregate pass counts.
#
#   bash eval/rerun_harness.sh [N]     # N defaults to 4 (Stage A screen)
#
# tau2-bench v1.0.0 can only run one simulation dir at a time, so the
# loop is strictly sequential. Per-run logs land under
# eval_runs/rerun_<ts>/run_<i>.log and are never deleted.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

N="${1:-4}"
if ! [[ "${N}" =~ ^[1-9][0-9]*$ ]]; then
    echo "usage: bash eval/rerun_harness.sh [N]  (N must be positive integer)" >&2
    exit 2
fi

OUT_DIR="eval_runs/rerun_$(date +%Y%m%d%H%M%S)"
mkdir -p "${OUT_DIR}"
SUMMARY="${OUT_DIR}/summary.txt"

# tee helper: send to both stdout and summary file
_t() { printf '%s\n' "$*" | tee -a "${SUMMARY}"; }

_t "rerun harness: N=${N}, out=${OUT_DIR}"
_t ""

declare -a COUNTS=()
SWEEP_START="$(date +%s)"

for i in $(seq 1 "${N}"); do
    LOG="${OUT_DIR}/run_${i}.log"
    RUN_START="$(date +%s)"
    _t "[run ${i}/${N}] starting"

    set +e
    bash eval/eval.sh > "${LOG}" 2>&1
    set -e

    ELAPSED=$(( $(date +%s) - RUN_START ))
    LINE="$(grep -E 'Summary: [0-9]+/[0-9]+ passed' "${LOG}" | tail -n 1 || true)"
    if [ -z "${LINE}" ]; then
        _t "[run ${i}/${N}] FAILED: no Summary line after ${ELAPSED}s"
        COUNTS+=("-1")
        continue
    fi
    PASSED="$(echo "${LINE}" | sed -nE 's/.*Summary:[[:space:]]*([0-9]+)\/([0-9]+).*/\1/p')"
    TOTAL="$(echo "${LINE}" | sed -nE 's/.*Summary:[[:space:]]*([0-9]+)\/([0-9]+).*/\2/p')"
    _t "[run ${i}/${N}] ${PASSED}/${TOTAL} (${ELAPSED}s)"
    COUNTS+=("${PASSED}")
    TASKS="${TOTAL}"
done

SWEEP_ELAPSED=$(( $(date +%s) - SWEEP_START ))

_t ""
_t "aggregate (wall: ${SWEEP_ELAPSED}s)"
python3 - "${TASKS:-97}" "${COUNTS[@]}" <<'PYEOF'
import math, sys
tasks = int(sys.argv[1])
counts = [int(c) for c in sys.argv[2:] if int(c) >= 0]
if not counts:
    print("  no successful runs")
    sys.exit(1)
mean = sum(counts) / len(counts)
sd = math.sqrt(sum((c - mean) ** 2 for c in counts) / max(1, len(counts) - 1))
print(f"  runs:   {counts}")
print(f"  mean:   {mean:.2f}/{tasks}  ({100*mean/tasks:.2f}%)")
print(f"  stddev: {sd:.2f}")
print(f"  min:    {min(counts)}/{tasks}")
print(f"  max:    {max(counts)}/{tasks}")
PYEOF

_t ""
_t "summary: ${SUMMARY}"
