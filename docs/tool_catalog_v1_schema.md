# ToolCatalog v1 — Schema Specification

**Status:** Proposed · **Version:** 1.0 · **Scope:** tau2-bench tool metadata, domain-agnostic

## 1. Purpose

Replace the current AST parser in `compass.py` (which reads
`tau2-bench/src/tau2/domains/banking_knowledge/tools.py` as source text) with a
JSON artifact generated from the live tau2-bench `Environment` object. A
pre-generated catalog fixes three fragilities of the AST approach:

1. `banking_knowledge` tools are assembled at runtime by `build_tools(variant,
   db, kb)` — the composed MixIn set depends on `--retrieval-config`. AST parsing
   can't see this; it only reads one file.
2. Decorator aliasing, imports, or conditionally-registered tools break AST
   walks.
3. New tool attributes in future tau2-bench releases require a version-tolerant
   contract on the consumer side.

## 2. Stability policy

The `catalog_version` field is the stability contract. Versions follow
semver-for-schemas:

- **Major** (`2.0`): breaking change to any required field. Consumers built for
  `1.x` MUST reject.
- **Minor** (`1.1`): additive — new optional fields, new enum values in
  `tool_type`, new toolkit kinds. `1.0` consumers MUST ignore unknown keys.
- **Patch** (`1.0.1`): documentation / metadata only.

The generator always stamps `catalog_version` as the schema it was built against.

## 3. Top-level schema

```json
{
  "catalog_version": "1.0",
  "domain_name": "banking_knowledge",
  "variant": "bm25",
  "toolkits": [
    {
      "env_type": "assistant",
      "toolkit_class": "BankingTools+BM25RetrievalMixin",
      "tools": [ /* tool entries — see §4 */ ]
    },
    {
      "env_type": "user",
      "toolkit_class": "KnowledgeUserTools",
      "tools": [ /* ... */ ]
    }
  ],
  "statistics": {
    "num_tools": 52,
    "num_discoverable": 48,
    "num_read": 18,
    "num_write": 28,
    "num_think": 1,
    "num_generic": 5
  },
  "metadata": {
    "generated_at": "2026-04-11T18:42:00Z",
    "generator": "compass.catalog_exporter v1",
    "tau2_bench_version": "1.0.0",
    "python_version": "3.11.9",
    "source_env_constructor": "tau2.domains.banking_knowledge.environment.get_environment"
  }
}
```

- **`variant`** is a free-form string (or `null` for domains without variants)
  that uniquely identifies a catalog when the same domain has multiple
  compositions. The generator is responsible for choosing a variant key — e.g.,
  the retrieval variant name for `banking_knowledge`, `null` for airline/retail.
- **`toolkits`** is a list (not a map) so a single JSON file can describe
  multiple `env_type` groups without forcing a particular reader convention.
- **`statistics`** mirrors `ToolKitBase.get_statistics()` and is computed
  per-catalog as a convenience for dashboards.

## 4. Tool entry schema

```json
{
  "name": "update_transaction_rewards_3847",
  "discoverable": true,
  "tool_type": "write",
  "mutates_state": true,
  "doc": "Update the rewards_earned field on a credit card transaction.\n\nArgs:\n    transaction_id (string): The unique identifier...",
  "short_desc": "Update the rewards_earned field on a credit card transaction.",
  "params_schema": {
    "type": "object",
    "properties": {
      "transaction_id": {"type": "string", "description": "..."},
      "new_rewards_earned": {"type": "string", "description": "..."}
    },
    "required": ["transaction_id", "new_rewards_earned"]
  },
  "returns_schema": {"type": "string"},
  "source": {
    "module": "tau2.domains.banking_knowledge.tools",
    "qualname": "BankingTools.update_transaction_rewards_3847"
  }
}
```

### Field-by-field rationale

| Field            | Required | Source (tau2-bench API)                              | Why                                                      |
|------------------|----------|------------------------------------------------------|----------------------------------------------------------|
| `name`           | yes      | `Tool.name` / `toolkit.tools` key                    | Primary identifier; hallucination check                  |
| `discoverable`   | yes      | `toolkit.is_discoverable(name)`                      | Distinguishes prompt-included vs unlock-required tools   |
| `tool_type`      | yes      | `toolkit.tool_type(name).value`                      | Drives prompt construction (read/write/think/generic)    |
| `mutates_state`  | yes      | `toolkit.tool_mutates_state(name)`                   | Needed for eval replay semantics                         |
| `doc`            | opt      | `Tool.__doc__` (non-discoverable) / `func.__doc__`   | Full docstring for enum/constraint extraction            |
| `short_desc`     | opt      | `Tool.short_desc`                                    | Cached first-line — avoids re-parsing doc on consumers   |
| `params_schema`  | opt      | `Tool.params.model_json_schema()`                    | JSON-schema-compatible param model                       |
| `returns_schema` | opt      | `Tool.returns.model_json_schema()`                   | Return-type hint for agents                              |
| `source`         | opt      | `inspect.getmodule(func).__name__`, `func.__qualname__` | Debugging / jump-to-definition in IDE                 |

**Critical gotcha, discovered in `toolkit.py`:** `get_tools()` returns
`Dict[str, Tool]` (wrapped pydantic objects that expose `params.model_json_schema()`
via `_serialize_params`), but `get_discoverable_tools()` returns
`Dict[str, Callable]` — **raw bound methods, not `Tool` wrappers**. The
exporter therefore has to wrap discoverable callables with `as_tool(func)`
(importable from `tau2.environment.tool`) if the consumer wants
`params_schema` for discoverable entries. The schema accommodates this by
making `params_schema` **optional**.

## 5. Consumption pattern

The existing `ToolCompass.__init__(tools_path=..., docs_dir=...)` takes a path
to `tools.py` and AST-parses it. The alternate constructor sketched here reads
the JSON instead:

```python
class ToolCompass:
    @classmethod
    def from_catalog_json(
        cls,
        catalog_path: Path,
        docs_dir: Path = _TAU2_DOCS_DIR,
    ) -> "ToolCompass":
        """Build a compass from a ToolCatalog v1 JSON file.

        Falls back to the AST parser if catalog_path does not exist, so
        existing `COMPASS = ToolCompass()` singletons still work when the
        catalog hasn't been generated yet.
        """
        inst = cls.__new__(cls)
        inst._domain = None  # filled from JSON
        inst._docs_dir = docs_dir
        inst._extensions = {}
        with open(catalog_path) as fp:
            data = json.load(fp)
        assert data["catalog_version"].startswith("1."), "ToolCatalog v1 required"
        inst._catalog = _catalog_from_json(data)   # shape-compat with _parse_catalog
        inst._tool_to_docs = None
        inst._scenario_index = None
        return inst
```

`_catalog_from_json` flattens the toolkit list into the `{agent, user, by_name}`
shape the rest of `compass.py` already consumes — a ~40-LOC adapter, no
changes to any other method.

## 6. Example: first 5 `banking_knowledge` entries

```json
[
  {
    "name": "example_agent_tool_0000",
    "discoverable": true,
    "tool_type": "write",
    "mutates_state": true,
    "doc": "An example agent discoverable tool placeholder.",
    "source": {"module": "tau2.domains.banking_knowledge.tools",
               "qualname": "BankingTools.example_agent_tool_0000"}
  },
  {
    "name": "update_transaction_rewards_3847",
    "discoverable": true,
    "tool_type": "write",
    "mutates_state": true,
    "doc": "Update the rewards_earned field on a credit card transaction.",
    "params_schema": {"type": "object",
                      "properties": {"transaction_id": {"type": "string"},
                                     "new_rewards_earned": {"type": "string"}},
                      "required": ["transaction_id", "new_rewards_earned"]}
  },
  {
    "name": "initial_transfer_to_human_agent_0218",
    "discoverable": true,
    "tool_type": "generic",
    "mutates_state": false,
    "doc": "Initiate a transfer request to a human agent..."
  },
  {
    "name": "initial_transfer_to_human_agent_1822",
    "discoverable": true,
    "tool_type": "generic",
    "mutates_state": false,
    "doc": "Initiate a transfer request to a human agent..."
  },
  {
    "name": "emergency_credit_bureau_incident_transfer_1114",
    "discoverable": true,
    "tool_type": "generic",
    "mutates_state": false,
    "doc": "Emergency escalation tool for the 11/14 credit bureau reporting incident."
  }
]
```

## 7. Integration plan

### Phase 1 — Alternate constructor (non-breaking)

- **Files touched:** `compass.py` (add `from_catalog_json` classmethod +
  `_catalog_from_json` adapter). No changes to `compass_banking.py`, `agent.py`,
  or any call sites.
- **LOC estimate:** ~60 lines added, 0 lines removed.
- **Test changes:** one new smoke test that builds `ToolCompass.from_catalog_json`
  against a checked-in fixture JSON and asserts `len(compass.valid_names) == 48`.
  The existing AST-path tests stay unchanged.
- **Fallback:** if `catalog_path.exists()` is False, `from_catalog_json` falls
  back to `ToolCompass(tools_path=...)` — i.e. the AST parser. This keeps CI
  green before the catalog is generated.

### Phase 2 — Build-time generation

- **Files touched:** new `eval/build_catalog.py` (the sibling agent is writing
  `eval/export_tool_catalog.py` — same thing, different filename; we align),
  plus `prepare.sh` to invoke it, plus a `data/tool_catalogs/` directory for
  committed outputs.
- **LOC estimate:** ~120 lines in the build script (constructs `Environment`,
  walks `env.tools` and `env.user_tools`, calls `get_tools()` +
  `get_discoverable_tools()`, serializes to JSON). `prepare.sh` gains 1 line.
- **Test changes:** CI adds a "catalog freshness" check — re-generate at build
  time, diff against the committed JSON, fail if they drift.

### Phase 3 — AST parser retirement

- **Only after all consumers have migrated.** Delete `_parse_catalog` and the
  `tools_path` parameter from `ToolCompass.__init__`. Deprecation shim can stay
  for one release.
- **Files touched:** `compass.py` (~150 LOC removed), README.
- **LOC estimate:** net −90.
- **Do NOT do this in Phase 1.** Back-compat is the whole point of Phase 1.

## 8. Universal vs conditional `discoverable` field

**Decision:** keep the field universal — always emit `"discoverable": <bool>`
for every tool, including `airline`, `retail`, and any future domain where the
value is always `false`.

**Reasoning:**
- Grep of `tau2-bench/src/tau2/domains/**` confirms Kimi's claim: only
  `banking_knowledge` currently uses `@is_discoverable_tool`. But this is a
  factual observation about tau2-bench 1.0, not a schema invariant.
- The field costs ~25 bytes per tool after gzip. A catalog with 200 tools is
  maybe 5 KB of overhead — negligible.
- A conditional field forces the consumer to write defensive `.get("discoverable",
  False)` everywhere. A universal field makes the default the schema's
  responsibility, not the reader's.
- If tau2-bench 1.1 adds `@is_discoverable_tool` to a second domain,
  conditional schemas break silently; universal schemas just start emitting
  `true` with no consumer changes.
- Forward-compat > compactness for a metadata catalog. This file is not on a
  hot path; it's loaded once at process start.

**Summary:** small, mechanical, cheap to carry; large benefit to consumers;
future-proof. Keep it universal.

---

*See also: `docs/gpt_deep_research_prompt.md` (origin of this proposal),
`docs/framework_reusability_audit.md` (broader compass-framework roadmap),
`tau2-bench/src/tau2/environment/toolkit.py` (source of truth for the APIs
referenced above).*
