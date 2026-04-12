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

## Historical artifacts (discarded)

The following docs were consolidated and deleted: `program_md_review.md` (6
contradictions found and fixed in program.md), `sijun_slack_reply.md` (three
draft variants of a slack post about the lite eval), `gpt_deep_research_prompt.md`
(the self-contained prompt used to generate the rerun protocol analysis),
`hive_announcement.md` (draft skill/feed announcements for compass.py).
