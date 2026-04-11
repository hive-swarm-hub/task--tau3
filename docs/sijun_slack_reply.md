# Slack reply for Sijun — re: subset / lite version

Three drafts at different lengths so you can pick the one that fits the conversation.

---

## Short version (single message)

> done — went with a curated 20-task subset (not random), each task labeled by failure cluster (canary, dispute family, escalation, etc.). takes ~2 min and ~$0.20 per run instead of 16 min for the full 97. when the score moves you can tell which kind of bug got fixed instead of just seeing a different number. shipped on hive/junjie c5aa2cf, swarm post #12 has the protocol writeup. ready to run.

---

## Medium version (two messages, more context)

> done — curated 20-task subset, ~2 min ~$0.20 per run vs 16 min for full 97. went with curated instead of `SAMPLE_FRAC=0.2` because random subsamples actually have ~2x MORE noise per task than the full eval, so single-run signal would be below the noise floor. measured this earlier today: three identical runs of the same code gave 8/97, 7/97, 6/97 — even task_001 (the most stable pass) failed in one. random fast samples lie to you more often, not less.
>
> the curated 20 has each task labeled by what kind of failure it represents — canary (always-pass regression detector), dispute family (Phase D's target), escalation, multi-step over-execution, etc. when the score moves you see WHICH cluster moved, not just a number. that lets you attribute changes to bug categories. shipped on hive/junjie c5aa2cf, also bumped concurrency to 12 (4x speedup on full eval too), swarm post #12 has the full writeup. ready for the group to run.

---

## Long version (one detailed message — use if Sijun wants to understand the design)

> hey sijun — went with a slightly different cut than 20 random. quick context on why and what i shipped.
>
> the problem with `SAMPLE_FRAC=0.2` random subsampling is that smaller samples have ~2x more variance per task than the full 97. i measured the noise on our current branch this morning: three identical runs of the same code gave 8/97, then 7/97, then 6/97. even task_001 — the most reliably-passing task in the whole benchmark — failed in one of those three runs. that's OpenAI's `system_fingerprint` drifting at temp=0 across the ~2000 LLM calls per run, not a code bug. on a 20-task random sample those drifts dominate any real signal — you'd be reading a number that lies to you ±20% of the time.
>
> so i went with a curated 20-task list instead of random. each task is hand-picked from cross-tabulated pass/fail data across 4 full evals, and labeled by what failure pattern it represents:
>
> - **canary (4 tasks)**: tasks that pass 4-out-of-4 historically. early-warning regression detector.
> - **playbook_trap (1)**: task_033 (the 11/13 backend incident, our scenario playbook target)
> - **dispute_calculator (5)**: task_017/018/021/026/040 — the cash-back family Phase D was built for
> - **execution_discipline (3)**: over/under-action failures
> - **variance_band (3)**: tasks that drift 2/4 historically — used to track noise level itself
> - **escalation (2)**: customer derailment and DOB-mismatch escalation
> - **recently_flipped (2)**: tasks that started passing in v5
>
> total = 20 tasks. takes ~2 min wall clock at concurrency=12 (also bumped that — full eval is now 16 min not 60). costs ~$0.20 per run. when it finishes it prints a per-cluster breakdown so you can see WHICH category moved instead of just an aggregate number:
>
> ```
> Per-cluster breakdown:
>   canary               4/4  [task_001✓, task_004✓, task_007✓, task_076✓]
>   playbook_trap        1/1  [task_033✓]
>   dispute_calculator   1/5  [task_017✓, task_018✗, ...]
>   execution_discipline 0/3
>   variance_band        2/3
>   escalation           0/2
>   recently_flipped     0/2
> ```
>
> protocol is two-tier: lite (~2 min) for inner-loop dev after every code change, full eval (~16 min) only when lite shows a ≥2 task improvement worth confirming. plus periodic 4-rerun averages on the full eval for noise-debiased verdicts. budget-wise that's ~10-20 lite runs per full run, so the cost picture is actually CHEAPER than running random subsamples blindly.
>
> shipped on hive/junjie c5aa2cf:
> - `eval/run_eval.py` has the `LITE_TASK_CLUSTERS` dict + `EVAL_LITE=1` toggle
> - `program.md` has the full two-tier protocol writeup + variance picture
> - swarm feed post #12 announces it for other agents to pull
>
> usage:
> ```bash
> EVAL_LITE=1 bash eval/eval.sh > run.log 2>&1     # 2 min, inner loop
> bash eval/eval.sh > run.log 2>&1                  # 16 min, full verdict
> ```
>
> ready to run. let me know if the cluster picks need to change — they're grounded in current data so they should be reasonable, but i'm happy to add/swap tasks if there's a category we haven't covered.

---

## Why three versions

- **Short** for "send and move on" if Sijun is just unblocking the group
- **Medium** if Sijun asks "what did you actually do" — gives the noise reasoning + the curated framing
- **Long** if Sijun wants the design rationale to evaluate before approving — has all the numbers and tradeoffs

Pick whichever matches the energy of the thread. I'd default to **medium** because it answers the implicit "is this defensible" question without being a wall.
