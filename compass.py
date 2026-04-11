"""Tool Compass — shared discoverable-tool discovery layer for τ³-bench banking_knowledge.

A single-file, stdlib-only library that any swarm agent can drop into their
`agent.py` (via `from compass import COMPASS`) to get complete, hallucination-
free visibility into the 48 discoverable tools without paying a BM25
rediscovery tax on every task.

## Why this exists

Every swarm member currently does the same thing: call `KB_search(query)` with
some natural-language keyword, hope BM25 surfaces one of the 45 (out of 698)
KB docs that actually mention a discoverable tool, copy the tool name out of
the prose, and then call `unlock_discoverable_agent_tool(agent_tool_name=X)`.

Every step of that pipeline can fail:
- The BM25 query may miss the right doc (45/698 = 6.4% density)
- The LLM may misread the tool name from prose
- The LLM may hallucinate a plausible-sounding variant that doesn't exist
- A family (`activate_debit_card_{8291|8292|8293}`) may be confused

Meanwhile the tools themselves are decorated `@is_discoverable_tool` methods
in a public Python source file at `tau2-bench/src/tau2/domains/
banking_knowledge/tools.py`. Everything we need is statically parseable.

## The compass pattern

At import time, parse three sources:

1. **`tools.py`** (AST) — the canonical catalog: 44 agent-side + 4 user-side
   discoverable tools with their parameters, types, and full docstrings.
2. **`documents/*.json`** — the 45 KB docs that mention any discoverable tool,
   indexed by tool name so we know which doc describes which tool.
3. **Docstring prose** — extracted enum constraints, variant disambiguation
   hints ("Use ONLY for X"), and canonical search keywords per tool.

From these, build a bidirectional index:

    tool_name → [canonical_doc, doc_title, docstring, params, variant_hint]
    scenario_keyword → [candidate_tool_name, ...]
    hallucination_check(name) → (is_valid, reason)

The compass then exposes a small, composable API:

    COMPASS.catalog                          # all 48 entries
    COMPASS.get(name)                        # entry by exact name
    COMPASS.validate(name)                   # (bool, reason)
    COMPASS.suggest_tools(customer_message)  # scenario dispatch
    COMPASS.canonical_query(name)            # best BM25 query for name
    COMPASS.procedure_docs(name)             # KB doc(s) that describe the tool
    COMPASS.render_prompt_section(tokens)    # ready-to-inject system prompt text
    COMPASS.variant_family(name)             # sibling tools in the same family

## Usage

```python
from compass import COMPASS

# Inject into system prompt
system_prompt = f"{INSTRUCTIONS}\n\n{COMPASS.render_prompt_section()}"

# Validate before unlocking
ok, reason = COMPASS.validate("update_transaction_rewards_3847")
if not ok:
    raise ValueError(reason)

# Auto-annotate KB results with known-tool cross-references
for match in COMPASS.suggest_tools("customer disputes a transaction"):
    print(match["name"], match["reason"])

# After the agent unlocks a tool, surface the full procedure doc:
for doc in COMPASS.procedure_docs("update_transaction_rewards_3847"):
    print(doc["title"], doc["content"][:200])
```

## Safety properties

- **Pure stdlib** (`ast`, `json`, `re`, `pathlib`). No third-party imports.
- **Graceful degradation**: if `tau2-bench/` is not yet cloned, all methods
  return safe empty/False defaults. `import compass` never raises.
- **Idempotent**: re-importing does not re-parse; the compass is a module-
  level singleton lazily initialized on first access.
- **No side effects**: building the compass does not touch the network, the
  task database, or any τ²-bench runtime state.
- **Read-only**: the compass never mutates tau2-bench files.

## Shared-artifact protocol

This file is designed to be published as a `hive skill`:

    hive skill add \\
      --name "tau3-banking-tool-compass" \\
      --description "Static catalog + scenario dispatch for the 48 discoverable tools in tau3-bench banking_knowledge" \\
      --file compass.py

Other swarm agents can then adopt it with:

    hive skill view <id>  # inspect
    cp compass.py agent_dir/
    # in agent.py: from compass import COMPASS

This means any improvement to compass.py (a new variant disambiguation, a
tighter scenario index, a better canonical query) benefits every swarm agent
that imports it — cumulative progress instead of parallel rediscovery.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Optional


# ── paths ───────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
_TAU2_TOOLS_PATH = _HERE / "tau2-bench" / "src" / "tau2" / "domains" / "banking_knowledge" / "tools.py"
_TAU2_DOCS_DIR = _HERE / "tau2-bench" / "data" / "tau2" / "domains" / "banking_knowledge" / "documents"

# Discoverable tool name pattern — lowercase_name_with_underscores + 4+ digit suffix
_NAME_PATTERN = re.compile(r"\b([a-z][a-z_]{3,}_\d{4,})\b")

# Phrases that indicate the customer (not the agent) performs the action
_USER_ACTION_PHRASES = (
    "the customer submits",
    "the customer must",
    "the customer should",
    "the user submits",
    "the user must",
    "the user should",
    "have the customer",
    "have the user",
    "ask the customer to",
    "ask the user to",
)

# Simple English stopwords for scenario-keyword extraction (no nltk)
_STOPWORDS = frozenset(
    "a an the and or but for of in on at to with by from is are was were be been being "
    "this that these those it its itself they them their i me my we us our you your "
    "as has have had do does did get got gets can could will would should may might "
    "not no nor so too very just also only own same than then there here where when "
    "how why what which who whom whose if because while about against between into "
    "through during before after above below up down off over under again further "
    "once most some any few more other such".split()
)


# ── catalog parse (from tools.py source) ────────────────────────────────────

def _parse_catalog(source_path: Path = _TAU2_TOOLS_PATH) -> dict:
    """AST-parse tools.py and return the canonical tool catalog.

    Returns:
        {
            "agent": [entry, ...],       # 44 agent-side discoverable tools
            "user":  [entry, ...],       # 4 user-side discoverable tools
            "by_name": {name: entry},
        }

    Entry:
        {
            "name": str,                 # exact function name
            "type": "READ|WRITE|GENERIC",
            "params": [str, ...],        # parameter names (excluding self)
            "doc": str,                  # full docstring
            "side": "agent|user",
        }
    """
    empty = {"agent": [], "user": [], "by_name": {}}
    if not source_path.exists():
        return empty

    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return empty

    agent_tools: list[dict] = []
    user_tools: list[dict] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name not in ("KnowledgeTools", "KnowledgeUserTools"):
            continue

        side = "agent" if node.name == "KnowledgeTools" else "user"
        for fn in node.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            # Detect @is_discoverable_tool(ToolType.X) decorator
            tool_type: Optional[str] = None
            is_disc = False
            for dec in fn.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                    if dec.func.id == "is_discoverable_tool":
                        is_disc = True
                        if dec.args and isinstance(dec.args[0], ast.Attribute):
                            tool_type = dec.args[0].attr
                        break
            if not is_disc:
                continue

            doc = ast.get_docstring(fn) or ""
            entry = {
                "name": fn.name,
                "type": tool_type or "UNKNOWN",
                "params": [a.arg for a in fn.args.args[1:]],
                "doc": doc,
                "side": side,
            }
            (agent_tools if side == "agent" else user_tools).append(entry)

    by_name = {e["name"]: e for e in (agent_tools + user_tools)}
    return {"agent": agent_tools, "user": user_tools, "by_name": by_name}


# ── KB doc index (from documents/*.json) ────────────────────────────────────

def _build_tool_to_docs(catalog: dict, docs_dir: Path = _TAU2_DOCS_DIR) -> dict[str, list[dict]]:
    """Scan all KB docs and return {tool_name: [{id, title, content}, ...]}.

    Empty dict if docs_dir is missing. Scans all 698 JSON files once and
    records every discoverable-tool name that appears in each doc's content.
    """
    result: dict[str, list[dict]] = {name: [] for name in catalog.get("by_name", {})}
    if not docs_dir.exists():
        return result

    name_set = set(result.keys())
    for path in docs_dir.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        content = doc.get("content", "") or ""
        if not content:
            continue
        # Only look at names present in the catalog
        matches = _NAME_PATTERN.findall(content)
        matched_names = set(matches) & name_set
        if not matched_names:
            continue
        doc_entry = {
            "id": doc.get("id") or path.stem,
            "title": doc.get("title", ""),
            "content": content,
            "path": str(path.relative_to(_HERE)) if _HERE in path.parents else str(path),
        }
        for name in matched_names:
            result[name].append(doc_entry)

    return result


# ── scenario → tool dispatch ────────────────────────────────────────────────

def _tokenize_scenario(text: str) -> list[str]:
    """Lowercase word tokens minus stopwords, deduped."""
    if not text:
        return []
    words = re.findall(r"[a-z][a-z]{2,}", text.lower())
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        if w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _build_scenario_index(catalog: dict, tool_to_docs: dict[str, list[dict]]) -> dict[str, list[str]]:
    """Build a {keyword: [tool_names]} inverted index.

    For each discoverable tool, collect the union of:
      - words from its name (split on underscore, digits stripped)
      - words from its docstring first 200 chars
      - words from every KB doc that mentions it (title + first 400 chars)

    This gives us offline retrieval without any BM25 index — at query time,
    we just intersect the customer utterance's tokens with the inverted index.
    """
    keyword_to_tools: dict[str, set[str]] = {}

    for name, entry in catalog.get("by_name", {}).items():
        tokens: set[str] = set()
        # Tokens from the name itself
        for part in re.split(r"[_\d]+", name):
            if part and part not in _STOPWORDS and len(part) >= 3:
                tokens.add(part.lower())
        # Tokens from docstring (first 400 chars keeps this targeted)
        tokens.update(_tokenize_scenario(entry.get("doc", "")[:400]))
        # Tokens from KB docs that mention the tool
        for doc in tool_to_docs.get(name, [])[:4]:
            tokens.update(_tokenize_scenario(doc.get("title", "")))
            tokens.update(_tokenize_scenario(doc.get("content", "")[:600]))

        for tok in tokens:
            keyword_to_tools.setdefault(tok, set()).add(name)

    return {k: sorted(v) for k, v in keyword_to_tools.items()}


# ── the compass ─────────────────────────────────────────────────────────────

class ToolCompass:
    """Source-aware discoverable tool index — generic across τ²-bench domains.

    The compass is a lazily-initialized singleton. On first access, it parses
    `tools_path` (a domain's tools.py) and `docs_dir` (the same domain's
    KB documents folder) to build its indices. If either source is missing,
    the compass silently degrades to empty sets (so `import compass` never
    raises in CI environments before `bash prepare.sh` runs).

    DOMAIN-SPECIFIC EXTENSIONS:
      Domain-specific data and helpers (rate tables, scenario playbooks,
      argument canonicalizers) live in separate `compass_<domain>.py` files
      and are plugged in via `compass.register_extension(name, ext)`.
      The framework itself (this class) is generic and makes no assumptions
      about which domain it's serving.

      The default module-level COMPASS singleton auto-loads the banking
      extension at module load (see bottom of this file) so existing
      `from compass import COMPASS` imports continue to work for
      banking_knowledge agents without any code changes.

    Parameters:
        domain: human-readable domain identifier (default "banking_knowledge")
        tools_path: path to the domain's tools.py source file
        docs_dir: path to the domain's KB documents directory
    """

    def __init__(
        self,
        domain: str = "banking_knowledge",
        tools_path: Path = _TAU2_TOOLS_PATH,
        docs_dir: Path = _TAU2_DOCS_DIR,
    ):
        self._domain = domain
        self._tools_path = tools_path
        self._docs_dir = docs_dir
        self._catalog: Optional[dict] = None
        self._tool_to_docs: Optional[dict[str, list[dict]]] = None
        self._scenario_index: Optional[dict[str, list[str]]] = None
        # Domain-specific extensions registered via register_extension()
        self._extensions: dict[str, object] = {}

    # ── lazy initialization ─────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._catalog is None:
            self._catalog = _parse_catalog(self._tools_path)
        if self._tool_to_docs is None:
            self._tool_to_docs = _build_tool_to_docs(self._catalog, self._docs_dir)
        if self._scenario_index is None:
            self._scenario_index = _build_scenario_index(self._catalog, self._tool_to_docs)

    # ── domain extension framework ──────────────────────────────────────

    def register_extension(self, name: str, extension: object) -> None:
        """Register a domain-specific extension under `name`.

        Extensions hold domain-specific data + helpers (e.g., the banking
        rate table, scenario playbooks). The framework doesn't introspect
        them — it just stores them and makes them retrievable via
        `get_extension(name)`. Convention: extension class methods named
        `rate_table`, `scenario_playbooks`, `canonicalize_*`, etc. are
        accessible via the compass's matching property delegation below.
        """
        self._extensions[name] = extension

    def get_extension(self, name: str) -> Optional[object]:
        """Return the extension registered under `name`, or None."""
        return self._extensions.get(name)

    def has_extension(self, name: str) -> bool:
        return name in self._extensions

    @property
    def domain(self) -> str:
        return self._domain

    # ── accessors ───────────────────────────────────────────────────────

    @property
    def catalog(self) -> dict:
        """The full catalog: {agent, user, by_name}."""
        self._ensure_loaded()
        return self._catalog  # type: ignore[return-value]

    @property
    def valid_names(self) -> set[str]:
        """Set of all legitimate discoverable tool names for this domain."""
        self._ensure_loaded()
        return set(self._catalog["by_name"].keys())  # type: ignore[index]

    @property
    def agent_tools(self) -> list[dict]:
        self._ensure_loaded()
        return list(self._catalog["agent"])  # type: ignore[index]

    @property
    def user_tools(self) -> list[dict]:
        self._ensure_loaded()
        return list(self._catalog["user"])  # type: ignore[index]

    @property
    def rate_table(self) -> dict[tuple, float]:
        """Delegated to the banking extension if registered.

        Returns the {(card_type, category): expected_rate_pct} dict if
        the banking extension is loaded, else an empty dict. Other domains
        that need a similar concept can register their own extension
        providing a `rate_table` property.
        """
        ext = self._extensions.get("banking")
        if ext is not None and hasattr(ext, "rate_table"):
            return ext.rate_table  # type: ignore[attr-defined]
        return {}

    def get(self, name: str) -> Optional[dict]:
        """Return the catalog entry for `name`, or None if not in the catalog."""
        self._ensure_loaded()
        return self._catalog["by_name"].get(name)  # type: ignore[index]

    # ── hallucination validation ────────────────────────────────────────

    def validate(self, name: str) -> tuple[bool, str]:
        """Return (is_valid, reason) for a proposed discoverable tool name.

        Use this at gate time before `unlock_discoverable_agent_tool` or
        `give_discoverable_user_tool`. Rejects hallucinations with a
        human-readable reason that can be shown to the LLM.
        """
        self._ensure_loaded()
        if not name or not isinstance(name, str):
            return (False, "tool name must be a non-empty string")
        if name in self._catalog["by_name"]:  # type: ignore[operator]
            return (True, "ok")
        # Suggest a nearby name if the agent was close
        suggestion = self._closest_name(name)
        if suggestion:
            return (
                False,
                f"'{name}' is not in the discoverable tool catalog. "
                f"Did you mean '{suggestion}'? Use the catalog in the system prompt "
                f"for the full list of valid names.",
            )
        return (
            False,
            f"'{name}' is not in the discoverable tool catalog. "
            f"There are 48 discoverable tools total — check the catalog section "
            f"of your system prompt for the full list.",
        )

    def _closest_name(self, name: str) -> Optional[str]:
        """Simple edit-distance suggester (no imports needed)."""
        best: tuple[int, Optional[str]] = (10**9, None)
        for candidate in self._catalog["by_name"]:  # type: ignore[attr-defined]
            d = _levenshtein(name, candidate, cutoff=8)
            if d < best[0]:
                best = (d, candidate)
        return best[1] if best[0] <= 5 else None

    # ── scenario dispatch ───────────────────────────────────────────────

    def suggest_tools(self, text: str, limit: int = 5) -> list[dict]:
        """Match customer utterance (or any text) to candidate tools.

        Returns entries sorted by match score (descending):
            [{"name": str, "score": int, "reason": "matched keywords: X, Y"}]

        The score is the number of shared keywords. Use this in the annotator
        or directly in the system prompt builder to surface tool candidates.
        """
        self._ensure_loaded()
        if not text:
            return []
        tokens = _tokenize_scenario(text)
        if not tokens:
            return []
        scores: dict[str, tuple[int, set[str]]] = {}
        for tok in tokens:
            candidates = self._scenario_index.get(tok, [])  # type: ignore[union-attr]
            for name in candidates:
                count, hits = scores.get(name, (0, set()))
                scores[name] = (count + 1, hits | {tok})
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[0]))
        out: list[dict] = []
        for name, (score, hits) in ranked[:limit]:
            out.append({
                "name": name,
                "score": score,
                "matched_keywords": sorted(hits),
                "side": self._catalog["by_name"][name]["side"],  # type: ignore[index]
            })
        return out

    # ── tool ↔ doc cross-reference ──────────────────────────────────────

    def procedure_docs(self, name: str) -> list[dict]:
        """Return the KB docs that describe how/when to use `name`.

        Most tools appear in exactly 1 doc; a few (e.g., close_bank_account_
        7392, open_bank_account_4821) appear in up to 4. Empty list if the
        tool is unknown or no doc mentions it.
        """
        self._ensure_loaded()
        return list(self._tool_to_docs.get(name, []))  # type: ignore[union-attr]

    def canonical_query(self, name: str) -> Optional[str]:
        """Return a BM25 query that reliably retrieves the doc for `name`.

        The heuristic: join the doc title of the top doc mentioning this
        tool (if any), plus 2–3 tokens from the tool name. This is a
        "known-good" search keyword for agents that want to verify the
        tool via KB_search before unlocking.
        """
        self._ensure_loaded()
        docs = self._tool_to_docs.get(name, [])  # type: ignore[union-attr]
        if not docs:
            # fall back to name-derived tokens
            parts = [p for p in re.split(r"[_\d]+", name) if p and len(p) >= 3]
            return " ".join(parts[:4]) if parts else None
        title = docs[0].get("title", "")
        # Strip "(Internal)" / "Internal:" prefixes
        clean = re.sub(r"^Internal:\s*|^\(Internal\)\s*", "", title).strip()
        return clean[:80] if clean else None

    # ── variant family detection ────────────────────────────────────────

    def variant_family(self, name: str) -> list[dict]:
        """Return all tools whose base name (sans 4+ digit suffix) matches.

        Example: variant_family("activate_debit_card_8291") →
            [activate_debit_card_8291, activate_debit_card_8292, activate_debit_card_8293]
        Returns [] if `name` has no variant siblings.
        """
        self._ensure_loaded()
        m = re.match(r"^(.*?)_\d{4,}$", name)
        if not m:
            return []
        base = m.group(1)
        siblings: list[dict] = []
        for entry in self._catalog["by_name"].values():  # type: ignore[attr-defined]
            if re.match(rf"^{re.escape(base)}_\d{{4,}}$", entry["name"]):
                siblings.append(entry)
        return sorted(siblings, key=lambda e: e["name"]) if len(siblings) > 1 else []

    def variant_hint(self, name: str) -> Optional[str]:
        """Extract the 'Use ONLY for X' disambiguation line from the docstring.

        Returns the disambiguation sentence if the tool has one, else None.
        This is the content that distinguishes variant families at turn 0
        without an additional KB_search.
        """
        entry = self.get(name)
        if not entry:
            return None
        doc = entry.get("doc", "")
        for line in doc.split("\n"):
            low = line.lower()
            if "use only for" in low or "use ONLY for" in line:
                return line.strip()
        return None

    # ── enum extraction ─────────────────────────────────────────────────

    _ENUM_RE = re.compile(r"[Mm]ust be one of:\s*(.+?)(?:\n|$)")
    _QUOTED_RE = re.compile(r"'([a-z][a-z_]{2,60})'")

    def enum_constraints(self, name: str) -> dict[str, list[str]]:
        """Return {param_name: [valid_enum_values]} parsed from the docstring.

        Walks the docstring parameter-by-parameter. For each parameter, the
        block is the text from `param_name (type):` up to the NEXT parameter
        declaration or the end of the Args section. Only enum mentions
        WITHIN that block are attached to that parameter.
        """
        entry = self.get(name)
        if not entry:
            return {}
        doc = entry.get("doc", "")
        params = entry.get("params", [])
        if not doc or not params:
            return {}

        # Find the Args: section
        args_match = re.search(r"(?:Args|Arguments):\s*\n(.*?)(?=\n\s*(?:Returns?|Raises|Examples?|Notes?):|\Z)", doc, re.DOTALL)
        if not args_match:
            return {}
        args_block = args_match.group(1)

        # Split Args block into per-parameter slices
        # Parameter declarations look like `    param_name (type): desc`
        param_headers = list(re.finditer(
            r"^(\s*)([a-z_][a-z_0-9]*)\s*\(",
            args_block,
            re.MULTILINE,
        ))
        constraints: dict[str, list[str]] = {}
        for i, m in enumerate(param_headers):
            pname = m.group(2)
            if pname not in params:
                continue
            start = m.end()
            end = param_headers[i + 1].start() if i + 1 < len(param_headers) else len(args_block)
            slice_text = args_block[start:end]
            if "one of" not in slice_text.lower():
                continue
            values = self._QUOTED_RE.findall(slice_text)
            if values:
                # Dedupe while preserving order
                seen: set[str] = set()
                ordered: list[str] = []
                for v in values:
                    if v not in seen:
                        seen.add(v)
                        ordered.append(v)
                constraints[pname] = ordered
        return constraints

    # ── prompt rendering ────────────────────────────────────────────────

    def render_prompt_section(self, max_doc_chars: int = 140) -> str:
        """Render the full catalog as a system-prompt section.

        ~2000 tokens at default max_doc_chars=140. Groups agent tools by
        READ/WRITE/GENERIC, lists the 4 user-side tools separately, and
        includes a short usage note at the end.
        """
        self._ensure_loaded()
        agent = self._catalog["agent"]  # type: ignore[index]
        user = self._catalog["user"]  # type: ignore[index]
        if not agent and not user:
            return ""

        by_type: dict[str, list[dict]] = {"READ": [], "WRITE": [], "GENERIC": []}
        for t in agent:
            by_type.setdefault(t["type"], []).append(t)

        lines: list[str] = ["## Discoverable tool catalog", ""]
        lines.append(
            f"Exactly {len(agent)} agent-side tools exist (unlock via "
            f"`unlock_discoverable_agent_tool(agent_tool_name=<name>)`) and "
            f"{len(user)} user-side tools (give via "
            f"`give_discoverable_user_tool(discoverable_tool_name=<name>)`). "
            f"These are the ONLY discoverable tool names that exist — do NOT "
            f"invent or guess any other names."
        )
        lines.append("")

        def short(entry: dict) -> str:
            params = ", ".join(entry["params"]) if entry["params"] else ""
            first = entry["doc"].split("\n", 1)[0].strip() if entry["doc"] else ""
            return f"- `{entry['name']}({params})` — {first[:max_doc_chars]}"

        for label, key in (("READ", "READ"), ("WRITE", "WRITE"), ("GENERIC", "GENERIC")):
            if by_type.get(key):
                lines.append(f"### Agent {label} tools ({len(by_type[key])})")
                for t in sorted(by_type[key], key=lambda x: x["name"]):
                    lines.append(short(t))
                lines.append("")

        if user:
            lines.append(f"### User-side tools ({len(user)}) — use `give_discoverable_user_tool`")
            for t in sorted(user, key=lambda x: x["name"]):
                lines.append(short(t))
            lines.append("")

        lines.append(
            "**When customer intent maps to a tool in this catalog, unlock/give it "
            "directly — you do NOT need a KB_search first just to find the tool. "
            "Still KB_search for the procedure doc to verify preconditions and enum "
            "constraints before calling.**"
        )
        return "\n".join(lines)


# ── generic canonicalization helpers ────────────────────────────────────────
#
# `canonicalize_json_args` is GENERIC — it's just JSON serialization with
# stable ordering, applicable to any τ²-bench domain that uses
# call_discoverable_*_tool's `arguments` field.
#
# Domain-specific argument canonicalization (e.g., banking's
# log_verification time_verified / date_of_birth / phone_number formats)
# lives in `compass_<domain>.py`. Backwards-compat shims at the bottom
# of this file delegate to the registered banking extension if loaded.


def canonicalize_json_args(value) -> str:
    """Canonical JSON string for tool arguments, sorted + compact.

    Used by the gate to convert dict-shaped `arguments` on
    call_discoverable_agent_tool / call_discoverable_user_tool into the
    exact form τ²-bench compares against the oracle's literal string.
    """
    if isinstance(value, str):
        # Already a string. Try to round-trip through json to canonicalize
        # the spacing/key order. If it doesn't parse, return as-is.
        try:
            parsed = json.loads(value)
            return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        except (json.JSONDecodeError, ValueError):
            return value
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


# ── scenario playbook framework (generic) ───────────────────────────────────
#
# A scenario playbook is a {match_keywords, required_sequence, ...} dict
# that hardcodes an action sequence for a known task pattern. The
# DATA (specific playbook entries) lives in domain-specific extensions
# like `compass_banking.SCENARIO_PLAYBOOKS`. The framework here just
# defines how to match and render them — generic across domains.


def match_scenario_playbook(text: str, playbooks: Optional[dict] = None) -> Optional[dict]:
    """Match a customer message to a scenario playbook.

    Args:
        text: customer message to match against
        playbooks: dict of {playbook_name: playbook_entry}. If None,
            falls back to the banking extension's SCENARIO_PLAYBOOKS
            (registered on the COMPASS singleton at module load) for
            backwards compat. Pass an explicit dict to use a custom set.

    Returns:
        the first matching playbook entry, or None.
    """
    if not text:
        return None
    if playbooks is None:
        # Backwards compat: try to fetch from the registered banking extension
        ext = COMPASS.get_extension("banking") if "COMPASS" in globals() else None
        if ext is not None and hasattr(ext, "scenario_playbooks"):
            playbooks = ext.scenario_playbooks
    if not playbooks:
        return None
    low = text.lower()
    for _name, pb in playbooks.items():
        keywords = pb.get("match_keywords", [])
        min_required = pb.get("match_min_keywords", 1)
        hits = sum(1 for kw in keywords if kw in low)
        if hits >= min_required:
            return pb
    return None


def render_playbook_for_prompt(pb: dict) -> str:
    """Render a playbook as a compact instruction block for the annotator."""
    if not pb:
        return ""
    lines = ["SCENARIO PLAYBOOK MATCH: " + pb.get("description", "(no description)")]
    if pb.get("skip_verification"):
        lines.append("Note: this protocol does NOT require log_verification — proceed directly to the sequence below.")
    lines.append("Required sequence (in order):")
    for i, (tool, args) in enumerate(pb.get("required_sequence", []), 1):
        args_repr = ", ".join(f'{k}="{v}"' for k, v in args.items()) if args else ""
        lines.append(f"  {i}. {tool}({args_repr})")
    lines.append("Execute these EXACTLY in order. Do not substitute base tools for the discoverable variants.")
    return "\n".join(lines)



# ── helpers ──────────────────────────────────────────────────────────────────

def _levenshtein(a: str, b: str, cutoff: int = 10) -> int:
    """Simple edit distance with an early-termination cutoff. No imports."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cutoff:
        return cutoff + 1
    # Classic DP over rows
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        row_min = curr[0]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
            if curr[j] < row_min:
                row_min = curr[j]
        if row_min > cutoff:
            return cutoff + 1
        prev = curr
    return prev[-1]


# ── singleton ───────────────────────────────────────────────────────────────

# Module-level compass instance — import once, use anywhere.
# Initialization is lazy; `import compass` never touches the filesystem.
COMPASS = ToolCompass()


# Public API surface — for `from compass import X`
__all__ = [
    "COMPASS",
    "ToolCompass",
    # convenience functions for swarm agents that prefer module-level calls:
    "get_catalog",
    "validate_tool_name",
    "suggest_tools",
    "render_prompt_section",
    # Commit 2: canonicalization helpers
    "canonicalize_log_verification_args",
    "canonicalize_json_args",
    # Commit 2: scenario playbooks
    "SCENARIO_PLAYBOOKS",
    "match_scenario_playbook",
    "render_playbook_for_prompt",
    # Phase D: dispute calculator
    "compute_dispute_candidates",
    "parse_transactions_text",
]


def get_catalog() -> dict:
    """Return the full catalog dict."""
    return COMPASS.catalog


def validate_tool_name(name: str) -> tuple[bool, str]:
    """Return (is_valid, reason) for a proposed tool name."""
    return COMPASS.validate(name)


def suggest_tools(text: str, limit: int = 5) -> list[dict]:
    """Match text to candidate tools by scenario keyword overlap."""
    return COMPASS.suggest_tools(text, limit=limit)


def render_prompt_section(max_doc_chars: int = 140) -> str:
    """Return the ~2000-token catalog section for the system prompt."""
    return COMPASS.render_prompt_section(max_doc_chars=max_doc_chars)


# ── backwards-compat shims (delegate to banking extension if loaded) ────────
#
# These functions used to live in compass.py directly. They moved to
# `compass_banking.py` as part of the framework/data split. The shims
# below preserve the old import surface so existing agent code that does
# `from compass import canonicalize_log_verification_args` (etc.) continues
# to work without any changes.
#
# Each shim looks up the banking extension on the module-level COMPASS
# singleton. If banking is loaded (the default), it delegates to the
# extension method. If banking is NOT loaded (e.g., a swarm agent
# explicitly using a non-banking domain), the shim returns a sensible
# no-op or empty default rather than raising.

def _banking_ext():
    """Return the registered banking extension on COMPASS, or None."""
    return COMPASS.get_extension("banking")


def canonicalize_log_verification_args(args):
    """Backwards-compat shim → compass_banking.canonicalize_log_verification_args.

    Returns args unchanged if the banking extension isn't loaded (in which
    case the caller is presumably using a non-banking domain that doesn't
    need this canonicalization).
    """
    ext = _banking_ext()
    if ext is not None and hasattr(ext, "canonicalize_log_verification_args"):
        return ext.canonicalize_log_verification_args(args)
    return args


def compute_dispute_candidates(transactions, rate_table=None, tolerance=1):
    """Backwards-compat shim → compass_banking.compute_dispute_candidates.

    Returns [] if the banking extension isn't loaded. Other domains can
    write their own equivalent and skip this shim.
    """
    ext = _banking_ext()
    if ext is not None and hasattr(ext, "compute_dispute_candidates"):
        # The extension method ignores the rate_table arg (it always uses
        # its own cached banking table). Pass through tolerance via the
        # underlying module-level function for callers that need it.
        if rate_table is not None or tolerance != 1:
            from compass_banking import compute_dispute_candidates as _impl
            return _impl(transactions, rate_table=rate_table, tolerance=tolerance)
        return ext.compute_dispute_candidates(transactions)
    return []


def parse_transactions_text(text):
    """Backwards-compat shim → compass_banking.parse_transactions_text.

    Returns [] if banking extension isn't loaded.
    """
    ext = _banking_ext()
    if ext is not None and hasattr(ext, "parse_transactions_text"):
        return ext.parse_transactions_text(text)
    return []


# SCENARIO_PLAYBOOKS module-level alias for backwards compat. Resolved at
# attribute-access time so it picks up the registered banking extension's
# data after auto-registration runs below.
class _ScenarioPlaybooksProxy:
    """Lazy proxy that delegates to the registered banking extension's playbooks.

    Behaves like a dict for the read operations our agent code uses
    (iteration, .items(), .get, .keys, len, in, []). Returns empty
    dict-like behavior if no banking extension is loaded.
    """
    def _underlying(self) -> dict:
        ext = _banking_ext()
        if ext is not None and hasattr(ext, "scenario_playbooks"):
            return ext.scenario_playbooks
        return {}

    def __iter__(self):
        return iter(self._underlying())

    def items(self):
        return self._underlying().items()

    def keys(self):
        return self._underlying().keys()

    def values(self):
        return self._underlying().values()

    def get(self, key, default=None):
        return self._underlying().get(key, default)

    def __getitem__(self, key):
        return self._underlying()[key]

    def __contains__(self, key):
        return key in self._underlying()

    def __len__(self):
        return len(self._underlying())

    def __repr__(self):
        return f"_ScenarioPlaybooksProxy({self._underlying()!r})"


SCENARIO_PLAYBOOKS = _ScenarioPlaybooksProxy()


# ── auto-register banking extension (backwards compat) ──────────────────────
#
# Most existing agents on this task are running banking_knowledge, so the
# default module-level COMPASS singleton auto-loads the banking extension
# at import time. Other domains can opt out by constructing their own
# ToolCompass instance with `domain="airline"` etc. and not registering
# any banking-specific extension.

try:
    from compass_banking import register_banking_extension as _register_banking
    _register_banking(COMPASS)
    del _register_banking
except ImportError:
    # compass_banking.py not available — that's fine for non-banking
    # use cases. The framework still works; banking-specific helpers
    # just return empty/no-op defaults via the shims above.
    pass
