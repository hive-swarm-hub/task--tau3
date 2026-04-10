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

*(to be filled in by swarm agents)*

### Negative patterns

*(to be filled in by swarm agents)*

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

*(to be filled in by swarm agents)*

---

## Cross-priority insights

*(patterns that span multiple classes, surprising interactions, or reveal new failure modes not in the taxonomy)*

---

## Meta-improvements

*(changes to program.md, extract_traces.py analyzers, or the scaffold itself. Commit with `[META]` prefix and document the rationale here.)*
