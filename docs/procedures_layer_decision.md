# Procedures Layer Decision

**Decision: DEFER — do not build a procedures layer now. Revisit if and only if a second domain (`compass_airline.py`, `compass_retail.py`, etc.) ships a real playbook that would duplicate code already in `compass_banking.py`.**

## Evidence from current code

The existing `scenario_playbooks` + extension-hook pattern already provides every structural piece Kimi's proposal names, keyed to domain-specific data rather than a central framework:

1. **`match_scenario_playbook(text, playbooks=...)`** at `compass.py:724` is already generic — it accepts an explicit `playbooks` dict so any domain can pass its own. The default fallback reads `ext.scenario_playbooks` from whichever extension is registered (`compass.py:741-743`). This is exactly "cross-domain reusable matcher" without a procedures layer.
2. **`BankingExtension.hook_on_tool_result`** at `compass_banking.py:504-521` runs the dispute calculator as a side effect of `get_credit_card_transactions_by_user` landing, and stores results under `state_field_*()` names the extension itself defines. `agent.py:728-734` calls this hook blindly with no banking knowledge. That IS the "procedure step execution" surface Kimi describes — it's just already in place, living on the extension instead of in a separate `procedures/` package.
3. **`SCENARIO_PLAYBOOKS["payment_not_reflected_incident"]`** at `compass_banking.py:163-204` already encodes `required_sequence`, `match_keywords`, and `skip_verification` — Kimi's proposed `steps` / `required_tools` fields under a different name. `render_playbook_for_prompt` at `compass.py:756-768` already renders `required_sequence` as a numbered instruction block.

## ROI analysis

**Gain from building now:** zero concrete wins. We have one domain, one playbook, one calculator. A `procedures/dispute.py` wrapping `compute_dispute_candidates` would simply re-export it at a new import path. A `procedures/verification.py` would wrap a single `log_verification` check that already lives in `annotate_banking` at `agent.py:343-349`. None of this reduces code or unlocks new behavior — it just adds one more layer of indirection the next reader has to traverse.

**Cost of building now:**
- A new package (`compass/procedures/`) turns `compass.py` from a two-file module into a package, breaking every existing `from compass import X` site.
- Kimi's pitch assumes "airline/retail/telecom can reuse verification" — but `docs/framework_reusability_audit.md` already confirmed that those three domains use zero `@is_discoverable_tool` decorators, expose all tools from turn 0, and have no discoverable-tool flow for a verification procedure to wrap. The hypothetical consumer does not exist in this benchmark.
- A procedures layer invented against a single consumer will be shaped by banking's quirks (dispute arithmetic, `submit_cash_back_dispute_0589` post-give reminder). The "reusable" abstraction will need a rewrite the moment a second domain actually arrives — so building it now is worse than waiting.

**Cost of deferring:** essentially nothing. If a second playbook needs to be added tomorrow (either banking or another domain), the cost is copying the `BankingExtension` shape — which already demonstrates the pattern — into a new extension class. The refactor to extract a shared base will be cheap because both consumers will be concrete at that point.

## Trigger condition for revisiting

Revisit when **both** of these are true:
1. A second file named `compass_<domain>.py` exists with its own scenario playbooks or calculator-style hook.
2. That file is about to copy-paste more than ~30 lines from `BankingExtension` (e.g., its own `hook_on_tool_result`, `get_dispute_candidates`, `format_*_message` scaffold).

At that point the duplication will be visible in diff form, the second consumer will constrain the abstraction honestly, and a `compass/procedures.py` will be justifiable in 50-100 lines instead of speculative.

Until then: YAGNI. The existing `scenario_playbooks` + extension hooks are the procedures layer, just named differently.
