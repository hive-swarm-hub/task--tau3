# Research Notes

Reference findings from research sprints. Findings are acted on —
this doc is historical context, not a TODO list.

## Concurrency

`max_concurrency` was dropped from 12 to 8 in `eval/run_eval.py`. At c=12 the
run saturated the full 2M TPM ceiling (75/89 rate-limit errors hit exactly
2,000,000 TPM used), causing 27/97 tasks to be silently excluded as
infrastructure errors. c=8 targets ~1.33M TPM (67% ceiling), leaving headroom
for litellm's 4-attempt retry. Wall time goes from ~20 min to ~25 min but all
97 tasks complete cleanly.

## Cross-domain reusability

Only `banking_knowledge` uses `@is_discoverable_tool`. The other five domains
(mock, airline, retail, telecom, telecom-workflow) use plain `@is_tool` with
fully visible toolsets from turn 0. Tool counts: airline 14/0, retail 16/0,
telecom 13/30, banking 59(15+44)/10(6+4) (assistant/user, visible+discoverable).
The catalog/unlock machinery in `compass.py` is banking-only in practice; the
prompt/playbook and canonicalization helpers are genuinely domain-agnostic.

## Brian2's techniques (10/97)

Brian2 reached 10/97 from baseline 7/97 using three techniques. (1) **Enum
pre-validation gate** (Intervention H): validates `call_discoverable_agent_tool`
arg values against `COMPASS.enum_constraints()` before submission -- adopted,
covers 8 tools. (2) **KB-mined account_class map** (Intervention I): extracts
`account_type -> account_class` from doc filenames/JSON to fix
`open_bank_account_4821` failures (task_058, task_075) -- adopted with
modified implementation reading doc JSON `title` fields instead of brittle
filename regex. (3) **Phase-2 guard tightening** (`user_calls >= len(candidates)`
instead of `>= 1`): reverted by brian2 because candidate count doesn't always
match oracle's expected submission count, causing indefinite stalls -- skipped.

## Rerun protocol origin

GPT deep research analysis showed R=15 reruns needed for a statistically
significant +2 task lift given observed noise (three identical runs gave 8, 7,
6 out of 97). This led to the Stage A/B rerun protocol in `program.md` and the
`eval/rerun_harness.sh` + `eval/rerun_analysis.py` tooling.

## Known unaddressed failure modes

- **Execution discipline** (task_091, task_087): long-sequence multi-card
  workflows (25 expected actions) where the LLM loses state mid-procedure.
  Neither enum gates nor account_class maps help; bottleneck is LLM working
  memory.
- **Adversarial compliance** (task_005): oracle expects agent to comply with a
  social-engineering bypass code (`log_verification` with all fields =
  "9K2X7M4P1N8Q3R5T6A"). Our agent correctly refuses, which counts as a
  failure. No generic intervention can solve this.
- **Base-tool arg validation** (task_024): `apply_for_credit_card` is a base
  tool (not discoverable), so the enum gate does not intercept it. A separate
  pre-validation path for base tool enums would be needed.

## Interventions Registry (shipped 2026-04-12)

The 9 existing inline interventions in `agent.py` (A through I, grep'd from
`# Intervention` comments) were extracted into `interventions_banking.py` and
wired through a new `InterventionRegistry` framework at `interventions.py`.
The gate and gate_post dispatch loops now iterate `REGISTRY.for_hook(...)`
instead of an elif cascade. See `docs/interventions_inventory.md` for the
full per-intervention spec.

Hook types: `prompt`, `annotator`, `gate_pre`, `gate_post`, `state_track`,
`tool_result`. The annotator hook is defined in the framework but existing
annotator signals remain inline in `annotate_banking` — registering them as
metadata-only (`apply=None`) was rejected because it provides false
discoverability without dispatch. Future work: extract each annotator signal
into a callable and register it under `hook="annotator"`.

Two new experimental interventions landed alongside the framework:
- **J (prefer-discoverable-reads)**: rewrites base-tool reads to unlock the
  discoverable variant when it's been mentioned in KB. Targets brian2's
  49-occurrence action-match miss on `get_bank_account_transactions_9173`.
- **K (verify-before-mutate)**: blocks mutation calls when `verified_user_ids`
  is empty. Written by a fresh agent (charlie) using only framework docs —
  validated the new-agent onboarding path.

The framework rejects duplicate IDs, validates hook/status at registration,
and the CLI `scripts/list_interventions.py` auto-imports all
`interventions_*.py` files for standalone discoverability.

## Retrieval swap — empirically closed (2026-04-12)

Six retrieval variants tested on our stack (gpt-4.1-mini + 8 interventions):

| Variant | Lite score | vs BM25 |
|---|---|---|
| BM25 (baseline) | 6-9/20 (noise band) | — |
| `golden_retrieval` (oracle perfect docs) | 9/20 | ±0 — **ceiling** |
| `openai_embeddings` | 8/20 | −1 (lexical loss on canary tokens) |
| `bm25_reranker` (+LLM reranker on BM25) | 6/20 | ±0 (noise) |
| `terminal_use` | 2/20 | −7 (annotator incompatible) |

**Retrieval is not the bottleneck for our model tier.** `golden_retrieval` —
eliminating retrieval error entirely — produced no improvement. Not tested:
`qwen_embeddings` (needs OPENROUTER_API_KEY we don't have), `full_kb` (would
blow context window), `bm25_grep` (additive, can't exceed golden ceiling).

## Failure bottleneck classification (2026-04-12)

13 failures from a lite run, manually re-classified per brian2's corrected
rubric (the stock P1 classifier is unreliable — 0 true P1s in the sample):

| Root cause | Count | % | Fix layer |
|---|---|---|---|
| Retrieval-bound | 0 | 0% | — |
| Execution discipline (stopped early, wrong order) | 4 | 31% | gate / annotator prompt |
| Argument fidelity (nested JSON, enum mismatch) | 3 | 23% | enum gate / canonicalization |
| Verification / two-sided unlock lifecycle | 3 | 23% | interventions |
| Model ceiling (task_024 wrong card, task_091 25 steps) | 2 | 15% | stronger model |
| Oracle-divergent (task_005 fake bypass code) | 1 | 8% | unfixable |

**77% agent-bound, 15% model-bound, 0% retrieval-bound.** Matches the
`golden_retrieval = BM25` finding.

## Model upgrade paths (2026-04-12)

Plumbing already exists: `SOLVER_MODEL` env var flows into
`litellm.completion(model=...)`. Only needs a new API key in `.env`.
Verified via `litellm.get_model_info` that all candidate models resolve.

| Model | litellm ID | Env var | Lite cost | Paper BM25 | Paper + Gold |
|---|---|---|---|---|---|
| Claude Sonnet 4.5 | `claude-sonnet-4-5` | ANTHROPIC_API_KEY | ~$3 | 14-17% | 32-35% |
| Claude Opus 4.5 | `claude-opus-4-5` | ANTHROPIC_API_KEY | ~$5.5 | 17-18% | 38-40% |
| Claude Haiku 4.5 | `claude-haiku-4-5` | ANTHROPIC_API_KEY | ~$1 | not in paper | — |
| GPT-5.2 | `gpt-5.2` | OPENAI_API_KEY (have) | ~$2.7 | 9.5% | 32.7% |
| Gemini 3 Pro | `gemini/gemini-3-pro-preview` | GEMINI_API_KEY | ~$2.5 | 13.7% | 33.3% |
| Gemini 3 Flash | `gemini/gemini-3-flash-preview` | GEMINI_API_KEY | ~$0.6 | 18.6% | 36.3% |

OpenRouter (`openrouter/anthropic/claude-sonnet-4-5` etc.) works with a
single `OPENROUTER_API_KEY` — one key, all 6 providers, ~5% credit markup.

Reasoning mode not currently wired — `llm_args_agent={"temperature": 0.0}`
only. Paper scores likely used extended thinking; replicating requires
adding `{"thinking": {"type": "enabled", "budget_tokens": 8000}}` or
similar to `run_eval.py`. Without it, expect 50-70% of headline scores.

See `docs/action_plan.md` for the current-recommended experiment sequence.

## Historical artifacts (discarded)

The following docs were consolidated and deleted: `program_md_review.md` (6
contradictions found and fixed in program.md), `sijun_slack_reply.md` (three
draft variants of a slack post about the lite eval), `gpt_deep_research_prompt.md`
(the self-contained prompt used to generate the rerun protocol analysis),
`hive_announcement.md` (draft skill/feed announcements for compass.py),
`INVENTORY_INDEX.md` (duplicate of interventions_inventory.md),
`hook_type_audit.md` (the audit's findings are reflected in the final hook
set), `intervention_impact_methodology.md` (one-paragraph; methodology lives
in program.md's Stage A/B section), `new_agent_experience_report.md`
(charlie's journey — friction points addressed in program.md docs),
`registry_integration_report.md` (pre-fix integration report superseded by
the fixes in this commit), `INTERVENTIONS_SUMMARY.txt` (root-level noise).
