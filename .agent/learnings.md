# Accumulated banking_knowledge learnings

Append-only file. When you discover a pattern (positive or negative), add a one-line entry with evidence and the commit SHA. Other swarm agents read this before starting work.

Format: `- <description>: <evidence> (discovered by <agent> in commit <sha>)`

## Discoverable tool patterns

- *Seed: submit_*_NNNN / update_*_NNNN naming convention for discoverable tools mentioned in KB docs — captured by `_DISCOVERABLE_TOOL_PATTERN` in agent.py (baseline scaffold)*

## Retrieval patterns

- *Seed: KB_search uses BM25 lexical matching (no semantic search). Queries must contain exact product/procedure names, not vague terms. (from program.md baseline)*

## Verification patterns

- *Seed: log_verification must be called before any account mutation. "verify identity" phrase in KB results triggers annotator flag. (from agent.py baseline)*

## Multi-step procedure patterns

- *Seed: Step markers like "step 1", "first,", "then,", "finally," indicate sequential procedures where the agent must not stop partway. (from agent.py baseline)*

## Cross-domain patterns

*(to be filled in as the swarm discovers them)*

## Discarded approaches

*(list of things that didn't work, with the reason why, so other agents don't repeat them)*

## Meta-improvements

*(notes on changes to program.md itself, what triggered them, what the effect was)*
