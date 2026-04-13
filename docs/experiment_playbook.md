# Experiment Playbook

This is the 1-page guide to running experiments on tau3.
Read `program.md` for the protocol; read this for the commands.
Every recipe assumes you've completed `prepare.sh` and have a valid `.env`.

## Decision tree

```
Do you have a failing task you want to debug?
  -> show_task.py  (§Recipe 2)

Do you have a hypothesis for a new intervention?
  -> program.md §Interventions + Recipe 4 -> Recipe 5 (A/B test)

Do you want to test a config swap (retrieval, model)?
  -> Recipe 6

Do you want to reproduce a past run?
  -> Recipe 7

Browse what's wired up?        -> Recipe 1
Experimental interventions?    -> Recipe 8
```

## Recipes

### Recipe 1 — See what's already registered

Lists every intervention in the stack with hook, target cluster, status, and measured impact. Run this before adding anything so you don't duplicate an existing rule.

```bash
python3 scripts/list_interventions.py
python3 scripts/list_interventions.py --filter-hook gate_pre
python3 scripts/list_interventions.py --filter-cluster dispute
# Expected: table of A..I rows (see program.md §Interventions for the reference set).
```

### Recipe 2 — Inspect a specific failing task

Dumps conversation, tool calls, and diagnostic fields for one task. Use when the traces summary flagged something and you want to see what the agent actually did.

```bash
python3 scripts/show_task.py task_058
python3 scripts/show_task.py task_058 --only messages
python3 scripts/show_task.py --list     # enumerate task_ids in latest results.json
```

Caveats: reads the most recent eval's `results.json`. Run an eval first (`EVAL_LITE=1 bash eval/eval.sh`) if none exists.

### Recipe 3 — Ablate an intervention (does H actually help?)

Disables one or more interventions via env var and reruns Stage A. Compare the 4-run mean against the stock-stack Stage A baseline to decide if the intervention is pulling its weight.

```bash
# Stage A with intervention H off:
DISABLED_INTERVENTIONS=H bash eval/rerun_harness.sh 4
# Multiple at once (comma-separated):
DISABLED_INTERVENTIONS=H,E bash eval/rerun_harness.sh 4
# Expected: eval_runs/rerun_<ts>/ with 4 sub-runs and an aggregate summary.
```

Caveats: use `list_interventions.py` to get the exact IDs. Stage A (n=4) screens for ±4-task swings, not ±2; see `program.md` for when to escalate to Stage B (n=15).

### Recipe 4 — Add a new intervention

See `program.md §Interventions (how to add a new rule)` for the full walkthrough including the hook-type table and a worked example. In short:

```bash
# 1. Write interventions/my_idea.py (~30 LOC, calls REGISTRY.register(...))
# 2. Add to agent.py imports:
#      from interventions import my_idea as _  # noqa
# 3. Unit-test first:
python3 -m pytest eval/test_interventions.py -q
# 4. Lite smoke:
EVAL_LITE=1 bash eval/eval.sh > run_lite.log 2>&1
# 5. Measure (Stage A):
bash eval/rerun_harness.sh 4
```

### Recipe 5 — A/B test your change

Baselines your current branch HEAD vs your working-tree change. `git stash` toggles between them so both arms use otherwise identical code and environment.

```bash
git stash                                  # park your change
bash eval/rerun_harness.sh 4               # -> eval_runs/rerun_<ts_baseline>/
BASELINE=eval_runs/rerun_<ts_baseline>

git stash pop                              # restore your change
bash eval/rerun_harness.sh 4               # -> eval_runs/rerun_<ts_candidate>/
CANDIDATE=eval_runs/rerun_<ts_candidate>

python3 scripts/compare_runs.py "$BASELINE" "$CANDIDATE"
```

Caveats: commit or stash *everything* (including untracked new files — use `git stash -u`) before the baseline run, otherwise the two arms aren't actually identical.

### Recipe 6 — Try a different retrieval mode

Swaps the retriever via env var. `bm25` is default; `terminal_use` gives a shell-like retrieval loop; `golden_retrieval` injects the oracle docs.

```bash
RETRIEVAL_VARIANT=terminal_use     EVAL_LITE=1 bash eval/eval.sh > run_terminal.log 2>&1
RETRIEVAL_VARIANT=golden_retrieval EVAL_LITE=1 bash eval/eval.sh > run_golden.log   2>&1
```

Caveats: `agent.py` is BM25-tuned. Terminal WILL score lower until the annotator is adapted. Golden is the ceiling study (~40% pass^1) — use it to isolate retrieval-vs-execution failures, not for leaderboard runs.

### Recipe 7 — Reproduce a past run

`scripts/reproduce.py` reads `last_config.json` (env, commit, flags) and prints the exact command that produced the most recent eval. Use `--run` to execute it.

```bash
python3 scripts/reproduce.py          # print-only (diff against current state)
python3 scripts/reproduce.py --run    # execute the printed command
```

Caveats: requires the commit referenced in `last_config.json` to be reachable in git. `git fetch` first if you're reproducing another agent's run.

### Recipe 8 — Enable experimental interventions

Interventions registered with `status="experimental"` are skipped by default. Flip them on globally, lite-eval, and diff against a fresh baseline.

```bash
EVAL_LITE=1 bash eval/eval.sh > run_baseline.log 2>&1
BASELINE=eval_runs/$(ls -1t eval_runs | head -1)
ENABLE_EXPERIMENTAL=1 EVAL_LITE=1 bash eval/eval.sh > run_exp.log 2>&1
EXP=eval_runs/$(ls -1t eval_runs | head -1)
python3 scripts/compare_runs.py "$BASELINE" "$EXP"
```

Caveats: lite is a screening signal, not a statistical claim. Promote to Stage A before committing — see `program.md §Eval rerun protocol`.

## When you want to submit a run

After Stage A shows a clear win (or Stage B confirms at p<0.05), tag the parent commit and submit:

```bash
hive run submit --parent <sha> --score <X>
```

`<sha>` is the commit you ran the eval on (7-char short hash is fine); `<X>` is the aggregate accuracy (e.g. `0.2474`). The harness will cross-check against your local `results.tsv` line.
