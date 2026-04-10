# Accumulated banking_knowledge learnings

Append-only file. When you discover a pattern (positive or negative), add a one-line entry with evidence and the commit SHA. Other swarm agents read this before starting work.

Format: `- <description>: <evidence> (discovered by <agent> in commit <sha>)`

Organized by the priority framework in `program.md`. Each priority has positive patterns (things that worked) and negative patterns (things that didn't). Negative patterns are just as valuable — they save other agents from wasting experiments.

---

## Priority 1 — Verification gating + discoverable-tool correctness

**What to attack here**: tasks where the agent never called `log_verification` before a mutation, or called a discoverable tool (like `submit_cash_back_dispute_0589`) without first calling `unlock_discoverable_agent_tool`.

**Diagnostic signals**:
- `trace["verification_analysis"]["mutation_calls_before_verify"] > 0`
- `trace["discoverable_tool_analysis"]["missing_unlocks"]` is non-empty
- `trace["discoverable_tool_analysis"]["called_without_unlock"]` is non-empty

### Positive patterns

- [PATTERN] Pre-bake the 48-tool catalog from tau2-bench tools.py at import time (via `compass.py`): eliminates 100% of tool-name hallucinations. On a full 97-task run with the compass + hallucination guard, 0 hallucinated unlock/give attempts detected across all 91 failures (was a real failure mode for earlier agents — observed `change_user_email_9921`, `submit_business_checking_account_referral_lime_green_003`). Score unchanged (6/97 matches stock) — hallucinations were NOT the dominant failure class, but they're still worth eliminating as a structural hygiene win. (discovered by junjie in commit e2d9e27)

### Negative patterns

- [NEG] Adding the full 48-tool catalog (~2000 tokens) to the system prompt did not measurably improve pass rate on the full 97-task eval (6/97 stock → 6/97 with catalog). Confirms brianbot6's earlier finding that "info is not the bottleneck" — the LLM having visibility into the tools is not the thing standing between 6% and higher. (discovered by junjie in commit 881103b)
- [NEG] Dropping redundant unlock/give calls at the gate level (interventions A & B) does not improve pass rate; at best it prevents task_021-style stalls caused by re-try loops. The stall fix is real but the db_match effect is negligible. (discovered by junjie in commit e2d9e27)

---

## Priority 2 — Wrong arguments (provenance discipline)

**What to attack here**: tasks where the agent called the correct tool name but with arguments the τ²-bench action evaluator rejected (exact-equality fails). Common culprits: wrong date format, off-by-one rounding, wrong enum string, guessed IDs.

**Diagnostic signals**:
- `trace["argument_analysis"]["correct_tool_wrong_args"] > 0`
- `trace["argument_analysis"]["arg_key_mismatches"]` shows which keys drift

### Positive patterns

*(to be filled in by swarm agents)*

### Negative patterns

*(to be filled in by swarm agents)*

---

## Priority 3 — Retrieval misses (query discipline)

**What to attack here**: tasks where the agent searched KB 3+ times without retrieving any document containing a discoverable tool name. The retriever is BM25 (deterministic) — duplicate queries return the same docs, so repeating a failed query wastes turns.

**Diagnostic signals**:
- `trace["retrieval_analysis"]["kb_query_count"] >= 3`
- `trace["retrieval_analysis"]["kb_queries_yielding_tool_names"] == 0`
- `trace["retrieval_analysis"]["duplicate_query_events"] > 0`

### Positive patterns

*(to be filled in by swarm agents)*

### Negative patterns

*(to be filled in by swarm agents)*

---

## Priority 4 — Execution discipline (coupled)

**What to attack here**: scattered failures that don't cluster into P1-P3. Under-action (stopped partway), over-action (extra operations), communication misses (didn't say the required phrase verbatim), max_steps terminations.

**Diagnostic signals**:
- `trace["termination_reason"] == "max_steps"`
- `trace["communicate_checks"]` has `met: false` entries
- `trace["execution_analysis"]["action_completeness"] < 1.0`
- `trace["primary_failure_class"] == "priority_4_execution_discipline"`

Note: P4 fixes are COUPLED — fixing loop detection may regress under-action, fixing communication may eat tool-call budget. Treat P4 as a system, not isolated bugs.

### Positive patterns

*(to be filled in by swarm agents)*

### Negative patterns

- [NEG] The "customer-simulator derails you mid-task" pattern is REAL and not fixable from the agent side alone. Observed on task_026: agent correctly gave `submit_cash_back_dispute_0589` to the customer; customer called it once; then said "my dispute was already approved, please update the rewards directly"; agent complied with `update_transaction_rewards_3847` (not an oracle-expected action). Oracle expected 6 user-side tool calls covering 6 transactions. Prompt rules ("stop after give") did not survive this specific social-engineering script — the LLM trusts the customer's framing. (discovered by junjie in commit 57c9844)
- [NEG] On 91/97 full-eval failures the agent averaged 3.0/9.8 expected actions matched — under-execution by ~70%. No single prompt change measurably moved this number in this session. The gap is structural to gpt-4.1-mini's ability to hold a 10+ action plan while navigating a derailing customer simulator. (discovered by junjie in commit 57c9844)
- [NEG] Full 97-task eval matched stock baseline exactly (6/97 = 0.0619) even with the compass catalog, state-aware annotator, hallucination guard, duplicate-drop gate, and JSON-encode gate all enabled. This confirms brianbot5's 0.0206 and brianbot6's 0.0619 stock measurements as the realistic ceiling for pure code-level improvements on gpt-4.1-mini without changes to the model or benchmark. (discovered by junjie in commit 57c9844)

---

## Cross-priority insights

*(patterns that span multiple classes, surprising interactions, or reveal new failure modes not in the taxonomy)*

---

## Meta-improvements

*(changes to program.md, extract_traces.py analyzers, or the scaffold itself. Commit with `[META]` prefix and document the rationale here.)*

- [META] Fixed `eval/extract_traces.py:238` — it was reading `args.get("tool_name")` but τ²-bench v1.0.0 renamed the meta-tool parameters: `unlock_discoverable_agent_tool` uses `agent_tool_name`, `give_discoverable_user_tool` uses `discoverable_tool_name`, `call_discoverable_agent_tool` uses `agent_tool_name`. Before the fix, every successful unlock was invisible and every KB-mentioned tool was reported as `missing_unlocks`. Every swarm agent that targeted P1 based on the stock extractor output was chasing a ghost class — real P1 is ~8%, not the ~45% the broken extractor reported. All swarm agents should either pull this fix or manually patch their extractor. Test regression `test_v1_param_names` covers all three name variants. (discovered by junjie in commit da7d45b)
- [META] Fixed `eval/extract_traces.py:138` — crash on full 97-task runs when a task's `reward_basis` is pure `["DB"]` (no ACTION). `reward_info.get("action_checks")` returns None in that case; fix is `or []`. (discovered by junjie in commit 57c9844)
- [META] Added `compass.py` — shared, stdlib-only library that any swarm agent can drop in via `from compass import COMPASS`. Source-aware parse of tau2-bench tools.py + cross-reference to the 45 tool-mentioning KB docs. Provides catalog, validate (with fuzzy suggestion), variant_family, variant_hint, suggest_tools (scenario keyword dispatch), procedure_docs, canonical_query, enum_constraints (per-parameter), render_prompt_section. 74 standalone tests. Grace-degrades to empty sets if tau2-bench isn't cloned. (discovered by junjie in commit e2d9e27)
- [META] Added `docs/gpt_deep_research_prompt.md` — self-contained Deep Research prompt asking for 7 specific deliverables (ranked diagnosis, scoring-rule archaeology, argument-assembly failure modes, retrieval ceiling analysis, ranked unexplored interventions, realistic ceiling estimate, statistical-rigor protocol). Use when you've exhausted obvious levers. (discovered by junjie in commit 7eee5f1)
