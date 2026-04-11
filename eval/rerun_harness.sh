#!/usr/bin/env bash
# Run N full tau3-bench banking_knowledge evals sequentially and aggregate
# the per-run "Summary: X/97 passed" counts for the two-stage rerun protocol.
#
# Stage A (screen):   N=4  -> bash eval/rerun_harness.sh 4
# Stage B (confirm):  N=15 -> bash eval/rerun_harness.sh 15
#
# Usage
# -----
#   bash eval/rerun_harness.sh [N] [--baseline X]
#
#     N           Number of full evals to run sequentially. Defaults to 4.
#     --baseline  Optional claimed baseline pass count (out of 97). When
#                 supplied, the script runs a two-proportion z-test of the
#                 harness mean vs the baseline using eval/rerun_analysis.py.
#
# Notes
# -----
# - Runs are strictly sequential: tau2-bench v1.0.0 only supports one
#   simulation directory at a time, and eval/eval.sh deletes
#   tau2-bench/data/simulations/eval_banking_knowledge before every run. Two
#   concurrent runs would race on that path and corrupt each other.
# - Per-run logs are never deleted: auditability requires them.
# - Exit 0 on success; non-zero if N <= 0 or any run log is missing the
#   "Summary:" line (which indicates the eval itself failed).
set -euo pipefail

# ---------------------------------------------------------------------------
# Locate repo root and cd there, so relative paths inside eval/eval.sh work.
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# ---------------------------------------------------------------------------
# Argument parsing: first positional is N, then optional --baseline X.
# ---------------------------------------------------------------------------
N="${1:-4}"
BASELINE=""

# Shift past the positional N if one was provided.
if [ $# -ge 1 ]; then
    shift
fi

while [ $# -gt 0 ]; do
    case "$1" in
        --baseline)
            if [ $# -lt 2 ]; then
                echo "ERROR: --baseline requires an integer argument" >&2
                exit 2
            fi
            BASELINE="$2"
            shift 2
            ;;
        --baseline=*)
            BASELINE="${1#--baseline=}"
            shift
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "usage: bash eval/rerun_harness.sh [N] [--baseline X]" >&2
            exit 2
            ;;
    esac
done

# Validate N is a positive integer.
if ! [[ "${N}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: N must be a non-negative integer, got: '${N}'" >&2
    exit 2
fi
if [ "${N}" -lt 1 ]; then
    echo "ERROR: N must be >= 1 (refusing to run zero evals)" >&2
    exit 2
fi

# Validate --baseline if provided.
if [ -n "${BASELINE}" ]; then
    if ! [[ "${BASELINE}" =~ ^[0-9]+$ ]]; then
        echo "ERROR: --baseline must be a non-negative integer, got: '${BASELINE}'" >&2
        exit 2
    fi
fi

# ---------------------------------------------------------------------------
# Create timestamped output dir: eval_runs/rerun_<YYYYMMDDHHMMSS>/
# ---------------------------------------------------------------------------
TIMESTAMP="$(date +%Y%m%d%H%M%S)"
OUT_DIR="eval_runs/rerun_${TIMESTAMP}"
mkdir -p "${OUT_DIR}"

SUMMARY_FILE="${OUT_DIR}/summary.txt"

# Anything we echo via _emit lands in BOTH stdout and summary.txt.
_emit() {
    printf '%s\n' "$*" | tee -a "${SUMMARY_FILE}"
}

_emit "tau3-bench rerun harness"
_emit "========================"
_emit "timestamp:   ${TIMESTAMP}"
_emit "output dir:  ${OUT_DIR}"
_emit "N runs:      ${N}"
if [ -n "${BASELINE}" ]; then
    _emit "baseline:    ${BASELINE}/97 (will compute two-proportion z-test)"
fi
_emit ""

# ---------------------------------------------------------------------------
# Sequential run loop. Parse "Summary: X/97 passed" from each log.
# ---------------------------------------------------------------------------
declare -a PASS_COUNTS=()
declare -a TOTAL_COUNTS=()
FAILED_RUNS=0
SWEEP_START_EPOCH="$(date +%s)"

for i in $(seq 1 "${N}"); do
    LOG_FILE="${OUT_DIR}/run_${i}.log"
    RUN_START_EPOCH="$(date +%s)"
    _emit "[run ${i}/${N}] starting -> ${LOG_FILE}"

    # Run one full eval. We intentionally do not abort on a non-zero exit
    # here: the Summary parse below is the source of truth, and we want all
    # N runs attempted even if one fails.
    set +e
    bash eval/eval.sh > "${LOG_FILE}" 2>&1
    RUN_RC=$?
    set -e

    RUN_END_EPOCH="$(date +%s)"
    RUN_ELAPSED=$((RUN_END_EPOCH - RUN_START_EPOCH))

    # Find the Summary line. extract_traces.py writes it to stderr in the
    # form: "  Summary: <passed>/<total> passed". The log captures 2>&1.
    SUMMARY_LINE="$(grep -E 'Summary: [0-9]+/[0-9]+ passed' "${LOG_FILE}" | tail -n 1 || true)"

    if [ -z "${SUMMARY_LINE}" ]; then
        _emit "[run ${i}/${N}] FAILED: no 'Summary: X/Y passed' line in ${LOG_FILE} (rc=${RUN_RC}, ${RUN_ELAPSED}s)"
        PASS_COUNTS+=("-1")
        TOTAL_COUNTS+=("-1")
        FAILED_RUNS=$((FAILED_RUNS + 1))
        continue
    fi

    # Extract the X and Y from "Summary: X/Y passed".
    PASSED="$(printf '%s\n' "${SUMMARY_LINE}" | sed -nE 's/.*Summary:[[:space:]]*([0-9]+)\/([0-9]+)[[:space:]]+passed.*/\1/p')"
    TOTAL="$(printf '%s\n' "${SUMMARY_LINE}" | sed -nE 's/.*Summary:[[:space:]]*([0-9]+)\/([0-9]+)[[:space:]]+passed.*/\2/p')"

    if [ -z "${PASSED}" ] || [ -z "${TOTAL}" ]; then
        _emit "[run ${i}/${N}] FAILED: could not parse Summary line: '${SUMMARY_LINE}'"
        PASS_COUNTS+=("-1")
        TOTAL_COUNTS+=("-1")
        FAILED_RUNS=$((FAILED_RUNS + 1))
        continue
    fi

    PASS_COUNTS+=("${PASSED}")
    TOTAL_COUNTS+=("${TOTAL}")
    _emit "[run ${i}/${N}] done: ${PASSED}/${TOTAL} passed (rc=${RUN_RC}, ${RUN_ELAPSED}s)"
done

SWEEP_END_EPOCH="$(date +%s)"
SWEEP_ELAPSED=$((SWEEP_END_EPOCH - SWEEP_START_EPOCH))

# ---------------------------------------------------------------------------
# Aggregate. Only successful runs contribute to stats; PASS_COUNTS[i] == -1
# marks a failed run (missing Summary line).
# ---------------------------------------------------------------------------
_emit ""
_emit "Per-run results"
_emit "---------------"

GOOD_COUNT=0
SUM=0
SUMSQ=0
MIN_VAL=""
MAX_VAL=""
TOTAL_TASKS=""

for i in $(seq 0 $((N - 1))); do
    RUN_IDX=$((i + 1))
    P="${PASS_COUNTS[$i]}"
    T="${TOTAL_COUNTS[$i]}"
    if [ "${P}" = "-1" ]; then
        _emit "  run ${RUN_IDX}: FAILED (no Summary line; see run_${RUN_IDX}.log)"
        continue
    fi
    _emit "  run ${RUN_IDX}: ${P}/${T}"
    GOOD_COUNT=$((GOOD_COUNT + 1))
    SUM=$((SUM + P))
    SUMSQ=$((SUMSQ + P * P))
    if [ -z "${MIN_VAL}" ] || [ "${P}" -lt "${MIN_VAL}" ]; then
        MIN_VAL="${P}"
    fi
    if [ -z "${MAX_VAL}" ] || [ "${P}" -gt "${MAX_VAL}" ]; then
        MAX_VAL="${P}"
    fi
    if [ -z "${TOTAL_TASKS}" ]; then
        TOTAL_TASKS="${T}"
    fi
done

_emit ""
_emit "Aggregate"
_emit "---------"
_emit "  total runs attempted: ${N}"
_emit "  successful runs:      ${GOOD_COUNT}"
_emit "  failed runs:          ${FAILED_RUNS}"
_emit "  wall-clock (all N):   ${SWEEP_ELAPSED}s"

if [ "${GOOD_COUNT}" -ge 1 ]; then
    # Build a comma-separated list of pass counts, safely joined in bash.
    COUNTS_CSV=""
    for c in "${PASS_COUNTS[@]}"; do
        if [ -z "${COUNTS_CSV}" ]; then
            COUNTS_CSV="${c}"
        else
            COUNTS_CSV="${COUNTS_CSV},${c}"
        fi
    done

    # Delegate mean/stddev computation to Python so we get proper floats.
    STATS_LINE="$(
        python3 - <<PYEOF
import math
counts = [int(c) for c in "${COUNTS_CSV}".split(",") if c]
good = [c for c in counts if c >= 0]
tasks = ${TOTAL_TASKS:-97}
mean = sum(good) / len(good)
if len(good) >= 2:
    var = sum((c - mean) ** 2 for c in good) / (len(good) - 1)
    sd = math.sqrt(var)
else:
    sd = 0.0
pct = 100.0 * mean / tasks if tasks else 0.0
print(f"{mean:.4f} {sd:.4f} {pct:.4f}")
PYEOF
    )"
    MEAN="$(printf '%s\n' "${STATS_LINE}" | awk '{print $1}')"
    STDDEV="$(printf '%s\n' "${STATS_LINE}" | awk '{print $2}')"
    MEAN_PCT="$(printf '%s\n' "${STATS_LINE}" | awk '{print $3}')"

    _emit "  mean:                 ${MEAN}/${TOTAL_TASKS} (${MEAN_PCT}%)"
    _emit "  stddev (sample):      ${STDDEV}"
    _emit "  min:                  ${MIN_VAL}/${TOTAL_TASKS}"
    _emit "  max:                  ${MAX_VAL}/${TOTAL_TASKS}"
else
    _emit "  WARNING: no successful runs; skipping aggregate stats"
fi

# ---------------------------------------------------------------------------
# Optional two-proportion z-test vs a claimed baseline.
# ---------------------------------------------------------------------------
if [ -n "${BASELINE}" ] && [ "${GOOD_COUNT}" -ge 1 ]; then
    _emit ""
    _emit "Two-proportion z-test vs baseline"
    _emit "---------------------------------"
    _emit "  baseline:  ${BASELINE}/${TOTAL_TASKS} (claimed single-run)"

    # Scale: treat the N successful runs as one big bucket of
    # N*TOTAL_TASKS trials with SUM successes, and compare against the
    # baseline scaled to the same denominator.
    N1=$((GOOD_COUNT * TOTAL_TASKS))
    X1="${SUM}"
    N0="${N1}"
    X0=$((BASELINE * GOOD_COUNT))

    _emit "  scaled treatment: ${X1}/${N1}"
    _emit "  scaled baseline:  ${X0}/${N0}"

    if [ -f "eval/rerun_analysis.py" ]; then
        ZP_LINE="$(
            python3 - <<PYEOF
import sys
sys.path.insert(0, "eval")
try:
    from rerun_analysis import two_prop_z, two_prop_pvalue
except Exception as e:
    print(f"ERR {e}")
    sys.exit(0)
z = two_prop_z(${X1}, ${N1}, ${X0}, ${N0})
p2 = two_prop_pvalue(${X1}, ${N1}, ${X0}, ${N0}, two_sided=True)
p1 = two_prop_pvalue(${X1}, ${N1}, ${X0}, ${N0}, two_sided=False)
print(f"OK {z:.4f} {p2:.6f} {p1:.6f}")
PYEOF
        )"
        if [[ "${ZP_LINE}" == OK* ]]; then
            Z_VAL="$(printf '%s\n' "${ZP_LINE}" | awk '{print $2}')"
            P2="$(printf '%s\n' "${ZP_LINE}" | awk '{print $3}')"
            P1="$(printf '%s\n' "${ZP_LINE}" | awk '{print $4}')"
            _emit "  z:          ${Z_VAL}"
            _emit "  p (2-sided): ${P2}"
            _emit "  p (1-sided): ${P1}"
            # Accept if two-sided p < 0.05 AND mean pass > baseline.
            ACCEPT="$(
                python3 - <<PYEOF
p = float("${P2}")
mean = float("${MEAN:-0}")
base = float("${BASELINE}")
print("YES" if (p < 0.05 and mean > base) else "NO")
PYEOF
            )"
            _emit "  accept (p<0.05 and mean>baseline)? ${ACCEPT}"
        else
            _emit "  WARNING: rerun_analysis import failed: ${ZP_LINE}"
        fi
    else
        _emit "  NOTE: eval/rerun_analysis.py not found; skipping z-test."
        _emit "  (The --baseline flag requires eval/rerun_analysis.py)"
    fi
fi

_emit ""
_emit "summary file: ${SUMMARY_FILE}"

# ---------------------------------------------------------------------------
# Exit status: non-zero if any run failed to produce a Summary line.
# ---------------------------------------------------------------------------
if [ "${FAILED_RUNS}" -gt 0 ]; then
    exit 1
fi
exit 0
