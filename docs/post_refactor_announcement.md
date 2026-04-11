# Slack announcement — compass split + two-stage rerun protocol

Three drafts at different lengths so junjie can pick the one that fits the thread energy.

---

## Short version (single message)

> two things shipped on tau3 main today:
>
> - **compass split** (PR #1, hive-swarm-hub/task--tau3): `compass.py` is now a generic τ²-bench scaffold, `compass_banking.py` is the worked banking example. other domains can drop in their own `compass_<domain>.py` and reuse `agent.py` unchanged. published as hive skills `compass-framework` + `compass-banking-extension`.
> - **two-stage rerun protocol**: old "avg of 4 runs" can't defend a +2-task claim at 7/97 baseline. new flow is Stage A = 4 reruns to screen, Stage B = top up to 15 + z-test to confirm. tools in `eval/rerun_analysis.py` and `eval/rerun_harness.sh`. lite eval stays as the dev-loop signal, unchanged.

---

## Medium version (two short paragraphs)

> two things landed on tau3 main today, both aimed at making the swarm's work more shareable and more defensible.
>
> **compass framework/banking split** — PR #1 at hive-swarm-hub/task--tau3 is merged. `compass.py` is now a domain-agnostic τ²-bench scaffold (catalog parser, playbook engine, fuzzy tool validator, prompt rendering, plug-in extension hooks). `compass_banking.py` is a reference example showing how to wire banking data into the framework. if you're working on airline/retail/telecom, you can fork `compass_banking.py` as a template — same method shape, same `agent.py`, no other code changes. published as two hive skills you can pull: `hive skill search compass --task tau3` gets you `compass-framework` and `compass-banking-extension`. verification: 134/134 unit tests, canary 4/4 on lite, full 97-eval at 7/97 baseline parity.
>
> **two-stage rerun protocol** — GPT deep research just confirmed our old habit of averaging 4 runs is not enough to back a +2-task claim: at 7/97 baseline with ±2-task noise, 4 reruns can only detect lifts of ~+4 tasks or larger. new protocol is Stage A (screen, 4 reruns per variant — accept on Δ ≥ +4, reject on Δ ≤ 0) and Stage B (confirm, top up to 15 reruns + two-proportion z-test, accept on p < 0.05). `eval/rerun_analysis.py` computes Wilson CIs / z-tests / min-R for any scenario; `eval/rerun_harness.sh N` runs and aggregates N full evals. lite eval stays separate — that's still the dev-loop fast signal, not a statistical claim.

---

## Long version (detailed message — use if someone asks for the full picture)

> hey swarm — two things shipped on tau3 main today. both are cross-cutting so worth a quick read.
>
> **1. compass framework/banking split (PR #1 at hive-swarm-hub/task--tau3, merged)**
>
> up until now `compass.py` had banking logic baked in, which made it awkward for anyone exploring airline/retail/telecom to reuse. refactor is done:
>
> - `compass.py` = generic τ²-bench scaffold. catalog parser, playbook engine, fuzzy tool validator, prompt rendering, plug-in extension system. no banking assumptions.
> - `compass_banking.py` = worked reference example. shows exactly how to plug a domain (data, policies, playbooks) into the generic framework.
> - `agent.py` is unchanged — any new `compass_<domain>.py` that matches the method shape just drops in.
> - published as two hive skills so you don't have to read the PR to use it:
>
> ```bash
> hive skill search compass --task tau3
> # returns compass-framework + compass-banking-extension
> # (skill IDs to be filled in once hive registry syncs)
> ```
>
> verification: 134/134 tests pass, canary 4/4 on lite eval, full 97-eval at 7/97 baseline parity. the refactor is safe to build on — no task-level regressions vs. pre-split main. **if you're on airline/retail/telecom, fork `compass_banking.py` as your template and keep the same method names.** that gives you skill portability for free.
>
> **2. two-stage rerun protocol for defensible eval claims**
>
> GPT deep research just proved what we half-suspected: the "run 4 evals and average" habit is not enough to back a +2-task claim. at 7/97 baseline with ±2-task noise, a 4-run average can only reliably detect lifts of ~+4 tasks or bigger — below that the confidence interval straddles zero. anything we posted as "+2" on 4 runs was under-powered.
>
> new protocol is two-stage:
>
> - **Stage A (screen)**: 4 reruns per variant. accept on Δ ≥ +4 tasks (clear win), reject on Δ ≤ 0 (clear no). if it lands in the middle, go to Stage B.
> - **Stage B (confirm)**: top up to 15 reruns per variant, two-proportion z-test, accept on p < 0.05.
>
> tools in the repo:
>
> ```bash
> python eval/rerun_analysis.py   # Wilson CIs, z-tests, min-R for any baseline/target
> bash eval/rerun_harness.sh 4    # Stage A: run 4 full evals sequentially + aggregate
> bash eval/rerun_harness.sh 15   # Stage B: top up to 15
> ```
>
> **important: the lite eval stays separate.** it's still the dev-loop fast signal for "did my change break a cluster," not a statistical claim. don't use lite numbers to defend a PR — use the rerun harness.
>
> tl;dr: pull the skills, fork `compass_banking.py` if you're on a non-banking domain, and run `bash eval/rerun_harness.sh 4` before claiming a lift. questions in thread.

---

## Why three versions

- **Short** for "fire and forget" — just surfaces that the two things exist + one-line why-you-care each
- **Medium** for when junjie actually wants to communicate what changed — explains the plug-in pattern and the rerun math in one sentence each, and invites people to pull the skills
- **Long** if someone asks for details in the thread — has the split architecture, the math reference, the `hive skill search` command, the harness usage, the verification numbers, and the explicit fork-banking invite for other domains

I'd default to **medium**. it surfaces both shipments with enough reason-to-care that people actually pull the skills, but isn't the wall-of-text that got flagged last time. short is fine if the channel is already busy; long is for the follow-up thread.
