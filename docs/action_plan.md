# Action Plan — what to do next

## TL;DR

**Retrieval is NOT our bottleneck. Our agent logic is.** Empirical proof: under `RETRIEVAL_VARIANT=golden_retrieval` (oracle-selected docs handed to the agent for free), we scored **9/20 — identical to BM25's 9/20**. The next experiment should target the 77% of failures that are agent-bound (execution discipline, argument fidelity, two-sided discoverable-tool lifecycle), not retrieval.

## Where we are

- Baseline: **6-9/20 lite** (6/97-7/97 full), `gpt-4.1-mini` + BM25
- Leaderboard-canonical: our `0d3e76a` at 7/97; brian2's `7edf50cc` at 10/97
- Empirical ceiling under perfect retrieval: **9/20 (same as BM25)** — per `docs/retrieval_swap_analysis.md`
- Failure root causes per `docs/actual_bottleneck_analysis.md` (13 failures classified):
  - **0/13 retrieval-bound** — every failed task already retrieved the right doc
  - **10/13 agent-bound** (4 execution, 3 argument, 3 verification/unlock lifecycle) — fixable via gate/annotator
  - **2/13 model-ceiling** (task_024 wrong-card selection, task_091 25-step cascade)
  - **1/13 oracle-divergent** (task_005 social-engineering bypass)

## Three paths, ranked by ROI

### Path 1 — Iterate the agent logic (highest ROI, cheapest)

**Expected score**: +2-5 tasks on lite, +3-8 on full, based on the 77% agent-bound fraction.

**Engineering cost**: 2-4 hours per iteration cycle. First step is free: flip intervention J (prefer-discoverable-reads) and K (verify-before-mutate) from `experimental` to `active` — zero new code, just status change. Both plug-ins already shipped.

**Money cost**: ~$0.20 per lite cycle, ~$2 per full cycle. Stage A is 4 reruns × $2 = $8 per variant.

**Prerequisites**: none. Environment already set up.

**How to start**:
```bash
sed -i '' 's/status="experimental"/status="active"/' interventions/prefer_discoverable_reads.py
EVAL_LITE=1 bash eval/eval.sh > run_j_active.log 2>&1
# If canary 4/4 + clusters move → bash eval/rerun_harness.sh 4 for Stage A
```

If J plateaus, iterate on R2 (execution-completeness loop guard) or R3 (schema-discipline few-shots for `call_discoverable_agent_tool` nested `arguments`).

### Path 2 — Swap to a stronger model (medium ROI, medium cost)

**Expected score**: Paper says `claude-sonnet-4-5 + BM25 = ~14-17%` (vs our 7%). On full eval: ~14-16/97, roughly **+7 tasks**. Significantly higher with reasoning mode (~20-25%).

**Engineering cost**: 30 minutes (env var + `.env` edit + optional `llm_args_agent` tweak for reasoning mode).

**Money cost**: $3 per lite eval, ~$16 per full eval on Sonnet 4.5. ~5x our current spend.

**Prerequisites**: `ANTHROPIC_API_KEY` from `console.anthropic.com/settings/keys` (user must obtain).

**How to start**:
```bash
echo 'ANTHROPIC_API_KEY=sk-ant-api03-...' >> .env
EVAL_LITE=1 SOLVER_MODEL=claude-sonnet-4-5 bash eval/eval.sh > run_sonnet_lite.log 2>&1
```

Per `docs/model_upgrade_recipes.md`: no code changes needed — plumbing already routes `SOLVER_MODEL` straight into `litellm.completion`. Exception: if running full eval, check `eval/eval.sh`'s OPENAI_API_KEY guard (it hard-errors today; needs a branch to also accept ANTHROPIC_API_KEY only for non-OpenAI models).

### Path 3 — Cheapest sanity check first (lowest risk)

**Expected score**: `gemini-3-flash` is in the same weight class as `gpt-4.1-mini`; paper doesn't have it but Gemini 3 Flash on BM25 is ~10-11% for similar-tier models. This is the "is the scaffold leaking score?" check.

**Engineering cost**: 30 minutes (OpenRouter setup) OR 10 minutes (Google direct).

**Money cost**: **$0.40 per lite, ~$2 per full**. Same as our current spend.

**Prerequisites**: `GEMINI_API_KEY` from `aistudio.google.com/apikey` OR `OPENROUTER_API_KEY` for multi-provider.

**How to start**:
```bash
echo 'GEMINI_API_KEY=...' >> .env
EVAL_LITE=1 SOLVER_MODEL=gemini/gemini-3-flash-preview bash eval/eval.sh > run_gemini_flash.log 2>&1
```

If Flash matches our BM25 score (~7-10%), it confirms the scaffold is extracting maximum value from this model tier — the ceiling really is the model, and Path 2 is the only way up. If Flash significantly underperforms, our scaffold has a gpt-4.1-mini-specific asset we shouldn't give up.

## Anti-recommendations

**Don't rewrite for `terminal_use` on gpt-4.1-mini.** The engineering cost is ~30 LOC to wire `interventions/shell_output_parser.py` into `annotate_banking` + a ~100-word `TERMINAL_PROMPT_SECTION` update. Expected lift is **0 tasks** — golden_retrieval empirically proved our model is saturated post-retrieval. Terminal helps frontier models because they convert better docs into better policy adherence; our model can't do that conversion.

**Don't burn $16-27 on Claude Opus 4.5.** Paper delta over Sonnet is only 1-2 points. Sonnet is the strictly dominant value pick.

**Don't spend engineering time on `openai_embeddings`.** Empirically regressed 2 canary tasks (lexical match on customer token overlap beats semantic search on banking jargon). Net -1 on lite.

## What the user decides

Decision tree:

- **Budget-constrained + fast iteration** → Path 1 (flip J active, zero cost, fastest signal)
- **Willing to add $10-30 API budget to test model ceiling** → Path 2 (Claude Sonnet 4.5 direct)
- **Want a cheap sanity check before committing to Anthropic** → Path 3 (Gemini 3 Flash on Google)

Recommended sequence: **Path 1 first** (costs nothing, runs in 5 min). If it plateaus, Path 2 (tells us if the ceiling is us or the model). Skip Path 3 unless Path 1 shows a regression or the user wants a "no I swear it's not our scaffold" control.

## Concrete first command

```bash
# Path 1 (recommended, zero cost):
sed -i '' 's/status="experimental"/status="active"/' interventions/prefer_discoverable_reads.py
EVAL_LITE=1 bash eval/eval.sh > run_j_active.log 2>&1
# Then:
python3 scripts/compare_runs.py eval_runs/<baseline> eval_runs/<j_active>  # after Stage A sweeps
```

---

**Key evidence sources:**
- `docs/actual_bottleneck_analysis.md` — 77% agent-bound classification
- `docs/retrieval_swap_analysis.md` — empirical golden_retrieval=9/20=BM25 result
- `docs/model_upgrade_recipes.md` — litellm-verified model IDs + exact costs
- `docs/model_swap_cost.md` — per-model summary + OpenRouter alternative
