"""Run τ³-bench evaluation on banking_knowledge domain and print accuracy."""

import sys
import os
import json
import subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import create_custom_agent

import random

from tau2.registry import registry
from tau2.run import run_domain, get_tasks
# τ²-bench v1.0.0: RunConfig is now a Union type; use TextRunConfig for text/half-duplex
from tau2.data_model.simulation import TextRunConfig
from tau2.metrics.agent_metrics import compute_metrics

# Repo root — used for writing the local snapshot and discovering the tau2
# results directory alongside it.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Config-snapshot helpers ─────────────────────────────────────────────────
#
# Every eval run writes a JSON blob to `eval_runs/last_config.json` (repo-local)
# AND to `{save_dir}/config_snapshot.json` (alongside tau2's results.json) so
# future agents can see the EXACT command that produced any past run — git
# SHA, env vars, intervention stack, task list. See `scripts/reproduce.py` for
# the companion tool that reconstructs the shell command from a snapshot.
#
# All snapshotting is wrapped in try/except — observability never breaks eval.


def _git_sha_or_dirty() -> str:
    """Return the current HEAD SHA, appending '-dirty' if the worktree isn't clean."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=_REPO_ROOT,
        ).decode().strip()
        # `git diff --quiet HEAD` exits 0 if tree matches HEAD, nonzero if dirty.
        dirty = subprocess.call(
            ["git", "diff", "--quiet", "HEAD"],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            cwd=_REPO_ROOT,
        ) != 0
        return f"{sha}{'-dirty' if dirty else ''}"
    except Exception:
        return "unknown"


def _git_current_branch_or_detached() -> str:
    """Return the current branch name, or 'HEAD' if detached, or 'unknown' on failure."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=_REPO_ROOT,
        ).decode().strip()
    except Exception:
        return "unknown"


def _snapshot_interventions() -> list[dict]:
    """Snapshot registered interventions with id/name/hook/status/author.

    Returns [] if the registry isn't importable yet (e.g. framework not built).
    """
    try:
        from interventions import REGISTRY
        # Ensure plug-ins have loaded by importing the canonical banking module.
        # agent.py imports this at startup anyway, but we're defensive here.
        try:
            from interventions import banking as _  # noqa: F401
        except Exception:
            pass
        return [
            {
                "id": i.id,
                "name": i.name,
                "hook": i.hook,
                "status": i.status,
                "author": i.author,
            }
            for i in REGISTRY.list(include_disabled=True)
        ]
    except Exception:
        return []


def _tau2_version_if_discoverable() -> str:
    """Return the installed tau2 package version, or 'unknown'."""
    try:
        import tau2
        return getattr(tau2, "__version__", "unknown")
    except Exception:
        return "unknown"


def _discover_save_dir() -> str:
    """Best-effort: find the directory where tau2 will save results.json.

    Mirrors the path-walking logic in eval/eval.sh — walks up from the tau2
    package dir to find `data/simulations`, then appends `eval_{DOMAIN}`.
    Falls back to the local tau2-bench submodule path if discovery fails.
    """
    try:
        import tau2
        d = Path(tau2.__file__).parent
        for _ in range(6):
            cand = d / "data" / "simulations"
            if cand.is_dir():
                return str(cand / f"eval_{DOMAIN}")
            if d.parent == d:
                break
            d = d.parent
    except Exception:
        pass
    return str(Path(_REPO_ROOT) / "tau2-bench" / "data" / "simulations" / f"eval_{DOMAIN}")


def _write_snapshot(snapshot: dict) -> None:
    """Write snapshot to eval_runs/last_config.json AND {save_dir}/config_snapshot.json.

    NEVER raises — observability is nice-to-have.
    """
    # 1) repo-local, gitignored — the canonical "last run" pointer
    try:
        local_dir = Path(_REPO_ROOT) / "eval_runs"
        local_dir.mkdir(parents=True, exist_ok=True)
        with open(local_dir / "last_config.json", "w") as f:
            json.dump(snapshot, f, indent=2, default=str)
    except Exception as e:
        print(f"[eval] warning: could not write eval_runs/last_config.json: {e}", file=sys.stderr)
    # 2) alongside tau2's results.json — survives archival of a run dir
    try:
        save_dir = Path(_discover_save_dir())
        save_dir.mkdir(parents=True, exist_ok=True)
        with open(save_dir / "config_snapshot.json", "w") as f:
            json.dump(snapshot, f, indent=2, default=str)
    except Exception as e:
        print(f"[eval] warning: could not write config_snapshot.json to save_dir: {e}", file=sys.stderr)


def _print_snapshot_summary(snapshot: dict) -> None:
    """One-line summary to stderr at the start of the run."""
    try:
        sha_raw = snapshot.get("git_sha", "unknown")
        sha_short = sha_raw[:7] + ("-dirty" if sha_raw.endswith("-dirty") else "")
        branch = snapshot.get("git_branch", "unknown")
        env = snapshot.get("env", {}) or {}
        mode = env.get("RETRIEVAL_VARIANT", "bm25")
        lite = env.get("EVAL_LITE", "0") == "1"
        mode_str = f"{mode}{' (lite)' if lite else ''}"
        interventions = snapshot.get("interventions", []) or []
        n_active = sum(1 for i in interventions if i.get("status") == "active")
        n_experimental = sum(1 for i in interventions if i.get("status") == "experimental")
        print(
            f"[eval] config snapshot: SHA={sha_short}, branch={branch}, "
            f"mode={mode_str}, interventions: {n_active} active + {n_experimental} experimental",
            file=sys.stderr,
        )
    except Exception:
        pass

# Register our custom agent factory (tau2 v1.0.0 factory-function API)
registry.register_agent_factory(create_custom_agent, "custom")

DOMAIN = "banking_knowledge"
SPLIT = "test"
NUM_TRIALS = 1
SAMPLE_FRAC = float(os.environ.get("SAMPLE_FRAC", "1.0"))  # e.g. 0.1 for 10%
MODEL = os.environ.get("SOLVER_MODEL", "gpt-4.1-mini")
USER_MODEL = os.environ.get("USER_MODEL", "gpt-4.1-2025-04-14")
# Retrieval variant — tau2-bench has 19 options.
# Default is terminal_use (shell-based search) which gives the agent direct
# filesystem access to grep/cat the 698-doc knowledge base. Cross-model
# trajectory analysis on 8 frontier models shows terminal_use retrieval
# correlates with 2x+ higher pass^1 vs embedding-based methods:
#   terminal_use models:      20-25% pass^1
#   embedding-based models:   10-12% pass^1
# Override to compare alternatives:
#   RETRIEVAL_VARIANT=bm25              — BM25 lexical search (previous default)
#   RETRIEVAL_VARIANT=golden_retrieval  — perfect retrieval (ceiling test)
#   RETRIEVAL_VARIANT=openai_embeddings — semantic search
RETRIEVAL_VARIANT = os.environ.get("RETRIEVAL_VARIANT", "terminal_use")
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
    # Cross-model data: these pass for 5-7/8 frontier models (Tier 1 tasks)
    "canary": [
        "task_001",  # 100% for Opus/Sonnet/GPT-high; 0% for GLM/Qwen (discriminator)
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
    # 2 high-discrimination tasks from cross-model trajectory analysis
    # These have the highest variance across 8 frontier models — moves here are real signal
    "cross_model_discriminator": [
        "task_012",  # highest variance (0.228): 100% Opus/Sonnet/GLM/Qwen, 0% Flash/Pro
        "task_032",  # high variance (0.179): 100% for 4 models, 0% for GLM
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
        retrieval_config=RETRIEVAL_VARIANT,
        save_to=f"eval_{DOMAIN}",
        log_level="WARNING",
        max_concurrency=MAX_CONCURRENCY,
    )

    # ── Snapshot config BEFORE run_domain fires so a crash still leaves
    # eval_runs/last_config.json behind for post-mortem reproduction.
    try:
        snapshot = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "git_sha": _git_sha_or_dirty(),
            "git_branch": _git_current_branch_or_detached(),
            "env": {
                "RETRIEVAL_VARIANT": os.environ.get("RETRIEVAL_VARIANT", "bm25"),
                "SOLVER_MODEL": os.environ.get("SOLVER_MODEL", "gpt-4.1-mini"),
                "USER_MODEL": os.environ.get("USER_MODEL", "gpt-4.1-2025-04-14"),
                "EVAL_CONCURRENCY": os.environ.get("EVAL_CONCURRENCY", "8"),
                "EVAL_LITE": os.environ.get("EVAL_LITE", "0"),
                "SAMPLE_FRAC": os.environ.get("SAMPLE_FRAC", "1.0"),
                "DISABLED_INTERVENTIONS": os.environ.get("DISABLED_INTERVENTIONS", ""),
                "ENABLE_EXPERIMENTAL": os.environ.get("ENABLE_EXPERIMENTAL", "0"),
            },
            "config": {
                "domain": DOMAIN,
                "split": SPLIT,
                "num_trials": NUM_TRIALS,
                "max_concurrency": MAX_CONCURRENCY,
                "task_ids": list(task_ids),
                "n_tasks": len(task_ids),
            },
            "interventions": _snapshot_interventions(),
            "python_version": sys.version.split()[0],
            "tau2_version": _tau2_version_if_discoverable(),
        }
        _write_snapshot(snapshot)
        _print_snapshot_summary(snapshot)
    except Exception as e:  # pragma: no cover — observability MUST not fail eval
        print(f"[eval] warning: config snapshot failed: {e}", file=sys.stderr)

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
