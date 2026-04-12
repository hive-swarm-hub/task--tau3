# program.md Review -- Fresh Agent Perspective

## What works well

1. **The two-tier eval protocol is the best part of the doc.** The lite eval cluster breakdown (lines 83-104) is concrete, actionable, and grounded in real data. A new agent can immediately understand *why* each task is in the lite list and what signal to extract from each cluster. The "if canary regressed, revert and diagnose" heuristic (line 107) is exactly the kind of rule an autonomous agent needs.

2. **The priority framework is well-structured and causally ordered.** Lines 306-341 give a clear decision procedure: read `failure_class_counts`, compute impact, use priority order as tiebreaker. The explanation of *why* P1 masks P2-P4 (line 323) prevents the common mistake of optimizing a downstream class while an upstream blocker is still dominant.

3. **Extension points are explicit and complete.** Lines 346-355 name five exact locations (`annotate_banking`, `_gate_tool_calls`, `_track_state`, `_task_state`, `extract_traces.py` analyzers) with their signatures and intended use. A new agent knows *where* to put code without reading the full 500-line agent.py first.

## What's missing or unclear

1. **Contradictory baseline advice.** Line 358 says "Run a baseline: `SAMPLE_FRAC=0.1 bash eval/eval.sh`" (10% random sample, ~10 tasks). But lines 11-13 explicitly warn against random subsamples ("Random subsamples have ~2x more noise than the full eval per task") and tell agents to use the curated lite eval instead. A new agent following the "Before you start implementing" section literally would use `SAMPLE_FRAC=0.1` for their first baseline, contradicting the top-of-file warning. The consequence: a noisy, unattributable first baseline that sets a bad anchor for all subsequent comparisons.

2. **task_001 historical record contradicts itself.** Line 85 in program.md says task_001 "pass[es] 4-out-of-4 in stock historical runs." But `run_eval.py` line 66 annotates the same task as "3/4 historical (escalation, simple)." A new agent reading the canary cluster description trusts that 4/4 means rock-solid, but the code says it failed once. If task_001 regresses, should the agent revert (program.md says yes) or treat it as variance (the code annotation suggests it does fail sometimes)?

3. **`rerun_harness.sh` is referenced but not explained.** Line 143 says "Run via `bash eval/rerun_harness.sh 4`" but never explains it runs *one variant only* (not baseline vs candidate). The rerun protocol (lines 125-132) describes comparing baseline and candidate, 4 reruns *each* (8 total), but the harness runs N sequential evals of whatever code is currently checked out. A new agent would need to manually: (a) run harness on baseline, (b) switch to candidate branch, (c) run harness again, (d) compare manually. None of this is spelled out.

4. **No mention of OPENAI_API_KEY setup.** The setup section (lines 16-32) says "Run prepare: `bash prepare.sh`" but never mentions creating `.env` with an API key. `eval.sh` lines 22-28 will fail immediately without it. The `.env.example` file exists but is never referenced in program.md.

5. **"What you CAN modify" contradicts "Do not modify."** Line 153 says you can modify `eval/run_eval.py` (LITE_TASK_CLUSTERS, MAX_CONCURRENCY, MODEL). But line 26 says "Do not modify" `eval/run_eval.py`. A new agent would not know which instruction to follow.

6. **Step 8 concurrency contradiction.** Line 291 says "4 full-eval reruns on baseline and 4 on the candidate (~2 hours at concurrency=12)." But the performance section (lines 146-147) warns that concurrency=12 causes TPM saturation and excludes ~40/97 tasks. The code defaults to 8. A new agent following step 8 literally would either use 12 (and get polluted results) or use 8 (and the time estimate is wrong).

## Suggested fixes

1. **Line 358**: Replace `SAMPLE_FRAC=0.1 bash eval/eval.sh` with `EVAL_LITE=1 bash eval/eval.sh`. Add a note: "Use lite for the first baseline. Run the full 97-task eval only after confirming the lite baseline is stable (canary 4/4)."

2. **Line 85**: Change "pass 4-out-of-4 in stock historical runs" to "pass 3-out-of-4 or better in historical runs" to match `run_eval.py` line 66. Alternatively, update the `run_eval.py` comment if the 3/4 annotation is stale.

3. **After line 143**: Add a paragraph: "The rerun harness runs N evals of the *current* code. To compare baseline vs candidate, run it once on the baseline branch (`git stash && bash eval/rerun_harness.sh 4`), save the summary, then switch to the candidate (`git stash pop && bash eval/rerun_harness.sh 4`) and compare means manually."

4. **After line 29 (Setup step 4)**: Insert a new step: "Copy `.env.example` to `.env` and paste your `OPENAI_API_KEY`. The eval will refuse to run without it."

5. **Line 26**: Change "Do not modify" to "Do not modify the metric or orchestration loop" to match the nuanced guidance on line 153. Or consolidate both mentions into one authoritative list.

6. **Line 291**: Change "concurrency=12" to "concurrency=8" and update the time estimate to "~3-4 hours" to match the current default and avoid TPM saturation.
