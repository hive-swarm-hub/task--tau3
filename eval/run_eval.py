"""Run τ³-bench evaluation on banking_knowledge domain and print accuracy."""

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

DOMAIN = "banking_knowledge"
SPLIT = "test"
NUM_TRIALS = 1
SAMPLE_FRAC = float(os.environ.get("SAMPLE_FRAC", "1.0"))  # e.g. 0.1 for 10%
MODEL = os.environ.get("SOLVER_MODEL", "gpt-4.1-mini")
USER_MODEL = os.environ.get("USER_MODEL", "gpt-4.1-2025-04-14")


def run_all():
    all_tasks = get_tasks(task_set_name=DOMAIN, task_split_name=SPLIT)
    n_sample = max(1, int(len(all_tasks) * SAMPLE_FRAC))
    random.seed(42)
    sampled = random.sample(all_tasks, n_sample)
    task_ids = [t.id for t in sampled]

    print(f"\n=== {DOMAIN.upper()} ({n_sample}/{len(all_tasks)} tasks) ===", file=sys.stderr)
    config = RunConfig(
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
    )
    results = run_domain(config)
    metrics = compute_metrics(results)

    n_tasks = len(results.tasks)
    pass1 = metrics.pass_hat_ks.get(1, 0.0)
    cost = metrics.avg_agent_cost * n_tasks
    correct = int(round(pass1 * n_tasks))

    print(f"  tasks: {n_tasks}, pass^1: {pass1:.4f}, cost: ${cost:.2f}", file=sys.stderr)

    print("---")
    print(f"accuracy:         {pass1:.6f}")
    print(f"correct:          {correct}")
    print(f"total:            {n_tasks}")
    print(f"cost_usd:         {cost:.2f}")


if __name__ == "__main__":
    run_all()
