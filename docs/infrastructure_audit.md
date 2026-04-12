# Infrastructure Audit: tau3-bench banking_knowledge scaffold

Auditor: claude (requested by junjie)
Date: 2026-04-11
Baseline: 7/97 (commit 0d3e76ac). Brian2 fork: 10/97 (single-run, unverified).

---

## Section 1: Infrastructure Gaps

### 1.1 Enum pre-validation is built but not wired into the gate

`compass.py:575-624` implements `enum_constraints(name)` — a proper per-parameter parser that walks the docstring Args section and extracts `'quoted_value'` enums scoped to each parameter. This is exactly the data brian2's Intervention H needs. However, the gate in `agent.py:734-1027` never calls it. The seven interventions (A through G) handle duplicate-meta-tool drops, hallucination guard, JSON canonicalization, phase-2 guard, post-give reminders, and log_verification canonicalization — but none of them validate enum argument values before a `call_discoverable_agent_tool` reaches the oracle.

The gap: when the LLM calls `call_discoverable_agent_tool(agent_tool_name="X", arguments='{"dispute_reason":"fraud"}')` but the docstring says `dispute_reason` must be one of `'unauthorized_fraudulent_charge'`, `'duplicate_charge'`, etc., our gate lets it through. The oracle's exact-equality comparator then fails the action. Brian2 added this as Intervention H and it is a genuine improvement — the method already exists in our compass, we just need to wire it.

Tasks likely to benefit: any task requiring enum-constrained discoverable tools. The `argument_analysis.arg_key_mismatches` field in traces would show these, but we currently do not break down "wrong args" into "wrong enum" vs "wrong format" vs "wrong value" subcategories (see 1.3 below).

### 1.2 No account_class validation

Brian2's Intervention I scans KB document filenames for `(account_type, account_class)` pairs and injects them into the prompt and gate. This is fragile (regex on filenames) but addresses a real failure mode: the LLM guesses `account_class` values that don't exist. Our scaffold has no equivalent. The compass catalog stores `params` and `doc` per tool but does not extract domain-specific value mappings from the KB corpus. This would need to be a `compass_banking.py` addition — not a framework feature.

### 1.3 Trace extractor lacks fine-grained argument failure categories

`eval/extract_traces.py:347-411` (`analyze_arguments`) counts `wrong_arg_events` and `correct_tool_wrong_args` but does not distinguish:
- **Enum mismatch** (value not in allowed set) vs **format mismatch** (right value, wrong format like "08/11/1997" vs "8/11/1997") vs **wrong value** (completely wrong ID)

Without this breakdown, an agent reading traces cannot tell whether Intervention H (enum gate) or Intervention G (format canonicalization) is the higher-priority fix. Adding a `arg_failure_type` field to the argument analyzer would cost ~30 LOC and make experiment selection significantly more targeted.

### 1.4 No "first-call-miss" diagnostic

The trace extractor classifies failures into P1-P4 but does not surface a "first-call-miss" signal: tasks where the agent's very first discoverable tool call was wrong (wrong name, wrong args, wrong role). This is useful because first-call-miss failures are often fixable by prompt engineering (the LLM had the right info but made the wrong choice), while multi-step failures require structural changes. Adding this signal to `analyze_execution` would be ~20 LOC.

### 1.5 LITE_TASK_CLUSTERS is missing an "enum_failure" cluster

The 7 clusters in `eval/run_eval.py:63-105` cover canary, playbook_trap, dispute_calculator, execution_discipline, variance_band, escalation, and recently_flipped. There is no cluster for tasks that fail specifically due to enum mismatches. Without one, an agent who adds Intervention H cannot use the lite eval to attribute the gain. Identifying 2-3 enum-sensitive tasks and adding an `enum_constraint` cluster would make the lite eval diagnostic for this failure class.

---

## Section 2: Self-Improvement Loop Assessment

### 2.1 Did brian2 follow the loop?

No. The documented loop (program.md lines 280-301) prescribes: lite eval after every change, per-cluster breakdown analysis, canary regression check, Stage A (4 reruns) before claiming improvement. Brian2's behavior (inferred from the context description):

- **4 runs, 3 different branches** — this is branch-hopping, not the iterative lite-then-full cycle the loop prescribes.
- **Single-run scores reported** — brian2 claims 10/97 from a single run. Program.md explicitly warns (line 11): "Single-run scores LIE about whether your change helped." At baseline noise of +-2 tasks, a 10/97 vs 7/97 difference is within 1.2 sigma of a single run — not statistically distinguishable from noise without reruns.
- **Reverted work between runs** — the phase-2 guard tightening was tried and reverted, suggesting trial-and-error without the diagnostic feedback the lite clusters provide.
- **No Stage A or Stage B** — no multi-run rerun protocol was used. The eval/rerun_harness.sh and eval/rerun_analysis.py tools exist and are documented but were not invoked.
- **Did not use the extension pattern** — hardcoded paths and inline logic rather than extending compass_banking.py via `register_extension`.

### 2.2 What broke in the loop for brian2?

Three things:

1. **The loop is buried in program.md.** The experiment loop is at line 280 — after 279 lines of context, setup, protocol docs, and failure taxonomy. An agent skimming program.md may never reach it. The most actionable instructions (the 10-step loop) should be near the top, not the bottom.

2. **The lite eval → cluster attribution step is implicit.** The loop says "READ THE PER-CLUSTER BREAKDOWN" (step 6) but does not show a worked example of what a good decision looks like. An agent that has never seen the breakdown format may not know what to look for.

3. **Stage A feels expensive and is easy to skip.** Stage A requires 8 full evals (~2 hours). An agent under time pressure will skip straight to `hive run submit` after a single full eval. Program.md warns about this but doesn't enforce it — there is no tooling that blocks submission without a rerun certificate.

### 2.3 Is program.md clear enough for a new agent?

Partially. The document is thorough but too long (~400 lines). Specific issues:

- **Good:** The two-tier eval protocol, the per-cluster lite breakdown, the overfitting rule, the failure taxonomy, and the extension points are all well-documented.
- **Bad:** The document reads like a reference manual, not a playbook. A new agent needs to know: (1) what to read first, (2) what to run first, (3) what to change first. The "Setup" section (line 17) sends agents to AGENTS.md, then back to program.md, then to learnings.md, then to compass.py. An agent following this literally will read 4 files before writing a single line of code.
- **Missing:** There is no "quick start for forking agents" section that says: "If you are building on commit X, here is what already works and here is what is known-broken."

### 2.4 Biggest gap between "what we built" and "what would make the next agent succeed"

The scaffold is excellent at **measuring** (trace extractors, priority classifiers, lite clusters) and **preventing regressions** (canary cluster, hallucination guard, dedup gate). What it lacks is **prescriptive guidance for the next high-impact intervention**.

Concretely: the traces show that P4 (execution discipline) dominates at ~70% under-execution, but program.md says "P4 fixes are COUPLED" and leaves it at that. An agent reading the traces sees "91/97 tasks fail, average 3.0/9.8 actions matched" and has no idea where to start. The learnings.md even says this gap is "structural to gpt-4.1-mini" — which may be true, but it discourages further work on the highest-volume failure class.

What would actually help: a **worked example** in program.md showing how to trace a specific P4 failure (e.g., task_026) from trace JSON → root cause → targeted fix → lite eval verification. This would teach the process, not just describe it.

---

## Section 3: Top 3 Recommended Actions

### 1. Wire `COMPASS.enum_constraints()` into the gate as Intervention H (~40 LOC, high impact)

The method exists at `compass.py:575-624`. The gate at `agent.py:886-898` already intercepts `call_discoverable_agent_tool` for JSON canonicalization. Add a parallel check: for each `call_discoverable_agent_tool`, extract the target tool name, call `COMPASS.enum_constraints(target)`, and for each constrained parameter, validate that the provided value is in the allowed set. If not, drop the call and inject a corrective note listing the valid values.

This is the single most impactful change because: (a) the data source already exists, (b) enum failures are deterministic (the oracle never accepts a wrong enum), (c) the corrective note gives the LLM a chance to self-correct on the next turn. Brian2 proved this works. Estimated effort: 40 LOC in `_gate_tool_calls`, plus 2-3 tasks added to LITE_TASK_CLUSTERS as an `enum_constraint` cluster.

### 2. Add argument failure sub-classification to the trace extractor (~30 LOC, medium impact)

In `eval/extract_traces.py:analyze_arguments`, after detecting `correct_tool_wrong_args`, classify each mismatch as `enum_mismatch`, `format_mismatch`, or `value_mismatch` by cross-referencing `COMPASS.enum_constraints()`. Add the sub-classification to `arg_key_mismatches`. This makes experiment selection data-driven: if 80% of P2 failures are enum mismatches, Intervention H is the right fix; if 80% are format mismatches, canonicalization work is the right fix.

### 3. Restructure program.md with a "Quick Start" section at the top (~0 LOC code, high compound impact)

Move the 10-step experiment loop to lines 1-30. Add a 5-line "if you are forking from commit X" section. Add a worked example of a P4 trace diagnosis. Cut the current 400-line document into two files: `program.md` (the playbook, <100 lines) and `program_reference.md` (the full reference with all the statistical protocol details). Every future agent reads program.md first — making those first 30 lines maximally useful has compounding returns across the entire swarm.
