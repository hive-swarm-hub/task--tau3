"""Run τ³-bench evaluation with per-domain and full-suite support.

Usage:
  python eval/run_eval.py                          # all 4 domains
  DOMAIN=banking_knowledge python eval/run_eval.py  # single domain (fast iteration)
  DOMAIN=airline python eval/run_eval.py            # single domain
  SAMPLE_FRAC=0.2 python eval/run_eval.py           # 20% of tasks (quick check)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import CustomAgent

import random

from tau2.registry import registry
from tau2.run import run_domain, get_tasks
from tau2.data_model.simulation import RunConfig
from tau2.metrics.agent_metrics import compute_metrics

# Register our custom agent
registry.register_agent(CustomAgent, "custom")

ALL_DOMAINS = ["airline", "retail", "telecom", "banking_knowledge"]
SPLIT = "test"
NUM_TRIALS = 1
SAMPLE_FRAC = float(os.environ.get("SAMPLE_FRAC", "1.0"))
MODEL = os.environ.get("SOLVER_MODEL", "gpt-4.1-mini")
USER_MODEL = os.environ.get("USER_MODEL", "gpt-4.1-2025-04-14")

# DOMAIN env var: run a single domain for fast iteration
DOMAIN_FILTER = os.environ.get("DOMAIN", "").strip()
DOMAINS = [DOMAIN_FILTER] if DOMAIN_FILTER else ALL_DOMAINS


def run_all():
    total_tasks = 0
    total_correct = 0
    total_cost = 0.0
    per_domain = {}

    for domain in DOMAINS:
        all_tasks = get_tasks(task_set_name=domain, task_split_name=SPLIT)
        n_sample = max(1, int(len(all_tasks) * SAMPLE_FRAC))
        random.seed(42)
        sampled = random.sample(all_tasks, n_sample)
        task_ids = [t.id for t in sampled]

        print(f"\n=== {domain.upper()} ({n_sample}/{len(all_tasks)} tasks) ===", file=sys.stderr)
        config = RunConfig(
            domain=domain,
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
            save_to=f"eval_{domain}",
            log_level="WARNING",
        )
        results = run_domain(config)
        metrics = compute_metrics(results)

        n_tasks = len(results.tasks)
        pass1 = metrics.pass_hat_ks.get(1, 0.0)
        cost = metrics.avg_agent_cost * n_tasks
        correct = int(round(pass1 * n_tasks))

        per_domain[domain] = {"pass1": pass1, "correct": correct, "total": n_tasks, "cost": cost}
        print(f"  tasks: {n_tasks}, pass^1: {pass1:.4f}, cost: ${cost:.2f}", file=sys.stderr)
        total_tasks += n_tasks
        total_correct += correct
        total_cost += cost

    accuracy = total_correct / total_tasks if total_tasks > 0 else 0.0

    # Per-domain breakdown
    for domain, d in per_domain.items():
        pct = d["pass1"] * 100
        print(f"{domain}_pass1:   {pct:.1f}% ({d['correct']}/{d['total']})")

    # Aggregate
    print("---")
    print(f"accuracy:         {accuracy:.6f}")
    print(f"correct:          {total_correct}")
    print(f"total:            {total_tasks}")
    print(f"cost_usd:         {total_cost:.2f}")


if __name__ == "__main__":
    run_all()
