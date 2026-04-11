"""Run τ³-bench evaluation on banking_knowledge domain and print accuracy."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import create_custom_agent

import random

from tau2.registry import registry
from tau2.run import run_domain, get_tasks
# τ²-bench v1.0.0: RunConfig is now a Union type; use TextRunConfig for text/half-duplex
from tau2.data_model.simulation import TextRunConfig
from tau2.metrics.agent_metrics import compute_metrics

# Register our custom agent factory (tau2 v1.0.0 factory-function API)
registry.register_agent_factory(create_custom_agent, "custom")

DOMAIN = "banking_knowledge"
SPLIT = "test"
NUM_TRIALS = 1
SAMPLE_FRAC = float(os.environ.get("SAMPLE_FRAC", "1.0"))  # e.g. 0.1 for 10%
MODEL = os.environ.get("SOLVER_MODEL", "gpt-4.1-mini")
USER_MODEL = os.environ.get("USER_MODEL", "gpt-4.1-2025-04-14")
# τ²-bench's stock max_concurrency is 3 (set in config.py). The eval is
# API-bound (not CPU-bound) so we can run many simulations in parallel
# without contention. Concurrency=8 keeps peak TPM ~1.3M (2/3 of the 2M
# gpt-4.1-mini ceiling), so retries absorb spikes and no tasks get
# excluded as infra errors; full 97-task eval ~24min. Override via the
# EVAL_CONCURRENCY env var.
# NOTE: concurrency=12 (the previous value) is ~16min wall time but hits
# TPM saturation — run_generic_full.log had 27/97 tasks excluded as
# infra errors (TPM RateLimit) — so the pass^1 denominator was silently
# shrunk. If OpenAI raises the org's TPM limit, revert to 12.
# MAX_CONCURRENCY = int(os.environ.get("EVAL_CONCURRENCY", "12"))
MAX_CONCURRENCY = int(os.environ.get("EVAL_CONCURRENCY", "8"))

# ── CURATED LITE EVAL (the 20-task fast inner loop) ─────────────────────────
#
# A full 97-task eval takes ~16 min at concurrency=12. For inner-loop dev
# (between code edits) that's still too expensive — you can only do ~4
# experiments per hour. The lite eval below is a curated 20-task subset
# that runs in ~3 min and costs ~$0.20 per run, but unlike a random
# SAMPLE_FRAC=0.2 sample, every task in the lite list is picked
# deliberately to represent a known failure pattern.
#
# The structure is {cluster_label: [task_ids]}. When the lite eval
# reports a score change, you can attribute it to a specific cluster:
# if `dispute_calculator` improved, your change helped the dispute
# family; if `canary` regressed, you broke a stable code path.
#
# The picks are grounded in actual session data: 4 full evals were
# cross-tabulated to identify which tasks are stable, which are in the
# variance band, and which represent specific failure modes. Tasks
# 005/018/021/036/040/087/091/100 have NEVER passed in any session
# run — they're in the lite list as DIAGNOSTIC tasks (changes that
# move them are real signal).
#
# Toggle: EVAL_LITE=1 bash eval/eval.sh

LITE_TASK_CLUSTERS: dict[str, list[str]] = {
    # 4 always-pass canaries — regression detector
    "canary": [
        "task_001",  # 3/4 historical (escalation, simple)
        "task_004",  # 4/4 historical (account ownership dispute)
        "task_007",  # 4/4 historical (simple lookup)
        "task_076",  # 4/4 historical (simple resolution)
    ],
    # 1 trap-tool/playbook target — Phase C scenario playbook fired here
    "playbook_trap": [
        "task_033",  # 11/13 backend incident — needs trap-pair sequence
    ],
    # 5 tasks in the dispute family — Phase D calculator's primary target
    "dispute_calculator": [
        "task_017",  # 1/4 (Phase D fires + customer submits)
        "task_018",  # 0/4 (Phase D fires but agent gives up before give)
        "task_021",  # 0/4 (Phase D fires but agent stalls in search loop)
        "task_026",  # 0/4 (Phase 2 derailment + calculator over-execution)
        "task_040",  # 0/4 (15 expected actions, complexity overload)
    ],
    # 3 multi-step over/under-execution failures
    "execution_discipline": [
        "task_036",  # over-execution (15 calls vs 3 expected)
        "task_087",  # long incomplete (144 turns, 9/20 actions)
        "task_100",  # wrong variant family
    ],
    # 3 variance-band tasks — track noise level itself
    "variance_band": [
        "task_006",  # 2/4 historical
        "task_016",  # 2/4 historical
        "task_035",  # 2/4 historical
    ],
    # 2 escalation/derailment tasks
    "escalation": [
        "task_005",  # 0/4 (placeholder/dispute, customer-derailment)
        "task_091",  # 0/4 (DOB mismatch escalation, 25 expected actions)
    ],
    # 2 recently-flipped tasks (passed in v5 but not earlier)
    "recently_flipped": [
        "task_019",  # 1/4 (passed only in v5)
        "task_024",  # 1/4 (passed only in v5)
    ],
}

# Flat list for the runner
LITE_TASK_IDS: list[str] = [
    tid for cluster in LITE_TASK_CLUSTERS.values() for tid in cluster
]

EVAL_LITE = os.environ.get("EVAL_LITE", "0") == "1"


def run_all():
    all_tasks = get_tasks(task_set_name=DOMAIN, task_split_name=SPLIT)

    if EVAL_LITE:
        # Curated 20-task subset — every task labeled by failure cluster.
        # See LITE_TASK_CLUSTERS for the per-task rationale.
        task_ids = list(LITE_TASK_IDS)
        n_sample = len(task_ids)
        print(f"\n=== {DOMAIN.upper()} LITE ({n_sample}/{len(all_tasks)} curated tasks) ===", file=sys.stderr)
        print("Clusters:", file=sys.stderr)
        for label, tids in LITE_TASK_CLUSTERS.items():
            print(f"  {label:20s} {tids}", file=sys.stderr)
    else:
        n_sample = max(1, int(len(all_tasks) * SAMPLE_FRAC))
        random.seed(42)
        sampled = random.sample(all_tasks, n_sample)
        task_ids = [t.id for t in sampled]
        print(f"\n=== {DOMAIN.upper()} ({n_sample}/{len(all_tasks)} tasks) ===", file=sys.stderr)
    config = TextRunConfig(
        domain=DOMAIN,
        task_split_name=SPLIT,
        task_ids=task_ids,
        agent="custom",
        llm_agent=MODEL,
        llm_args_agent={"temperature": 0.0},
        user="user_simulator",
        llm_user=USER_MODEL,
        llm_args_user={"temperature": 0.0},
        num_trials=NUM_TRIALS,
        max_steps=200,
        max_errors=10,
        seed=300,
        save_to=f"eval_{DOMAIN}",
        log_level="WARNING",
        max_concurrency=MAX_CONCURRENCY,
    )
    results = run_domain(config)
    metrics = compute_metrics(results)

    n_tasks = len(results.tasks)
    pass1 = metrics.pass_hat_ks.get(1, 0.0)
    cost = metrics.avg_agent_cost * n_tasks
    correct = int(round(pass1 * n_tasks))

    print(f"  tasks: {n_tasks}, pass^1: {pass1:.4f}, cost: ${cost:.2f}", file=sys.stderr)

    # When EVAL_LITE is on, print a per-cluster breakdown so the agent can
    # see WHICH failure category moved (not just the aggregate number).
    # This is the whole point of the curated subset: signal per cluster.
    if EVAL_LITE:
        # Read raw results.json to get per-task pass/fail
        try:
            import json
            results_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "tau2-bench", "data", "simulations", f"eval_{DOMAIN}", "results.json",
            )
            r = json.load(open(results_path))
            sims = r.get("simulations", []) or []
            pass_by_tid = {
                s["task_id"]: (s.get("reward_info", {}) or {}).get("reward", 0.0) >= 0.99
                for s in sims
            }
            print("\n  Per-cluster breakdown:", file=sys.stderr)
            for label, tids in LITE_TASK_CLUSTERS.items():
                passed_in_cluster = sum(1 for t in tids if pass_by_tid.get(t, False))
                detail = ", ".join(
                    f"{t}{'✓' if pass_by_tid.get(t, False) else '✗'}" for t in tids
                )
                print(f"    {label:22s} {passed_in_cluster}/{len(tids)}  [{detail}]", file=sys.stderr)
        except (OSError, KeyError, json.JSONDecodeError) as e:
            print(f"  (could not read per-cluster breakdown: {e})", file=sys.stderr)

    print("---")
    print(f"accuracy:         {pass1:.6f}")
    print(f"correct:          {correct}")
    print(f"total:            {n_tasks}")
    print(f"cost_usd:         {cost:.2f}")


if __name__ == "__main__":
    run_all()
