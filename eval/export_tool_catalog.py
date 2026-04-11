"""Domain-agnostic tool catalog exporter for tau2-bench environments.

Iterates over every domain registered in ``tau2.registry`` and emits a JSON
catalog of the tools exposed by each environment's assistant and user
toolkits. Uses the live ``ToolKitBase`` interface (``get_tools()`` and
``get_discoverable_tools()``) rather than AST-parsing source files, so it
picks up all the metadata the framework already knows about (including the
subset of banking_knowledge tools that varies per ``--retrieval-config``).

Usage:
    python3 eval/export_tool_catalog.py > eval_runs/tool_catalog_all.json

The output is a list of per-domain records. Each record has the shape::

    {
      "domain_name": "banking_knowledge",
      "variant": "bm25",                  # optional, only when set
      "toolkit_counts": {
        "assistant_nondiscoverable": N,
        "assistant_discoverable":    N,
        "user_nondiscoverable":      N,
        "user_discoverable":         N
      },
      "toolkits": [
        {"env_type": "assistant", "tools": [ {name, discoverable, tool_type,
                                              mutates_state, doc}, ... ]},
        {"env_type": "user",      "tools": [ ... ]}
      ]
    }

Domains that fail to construct are recorded as
``{"domain_name": d, "error": "..."}`` so a single broken environment does
not crash the whole dump.
"""

from __future__ import annotations

import inspect
import json
import sys
import traceback
from typing import Any

from tau2.registry import registry


def _tool_doc(toolkit: Any, name: str) -> str:
    """Best-effort extraction of a tool's human-readable docstring.

    ``get_tools()`` returns ``Tool`` wrappers which carry ``short_desc`` /
    ``long_desc`` / ``__doc__``; ``get_discoverable_tools()`` returns the raw
    bound method whose ``__doc__`` holds the docstring. This helper tries
    each of those sources in a safe order and falls back to the empty string.
    """
    # Prefer a framework-supplied getter if the toolkit ever grows one.
    getter = getattr(toolkit, "get_tool_doc", None)
    if callable(getter):
        try:
            doc = getter(name)
            if doc:
                return str(doc).strip()
        except Exception:
            pass

    # Look up the live attribute (either a Tool wrapper or a bound method).
    obj = None
    try:
        tools_dict = toolkit.get_tools()
        if name in tools_dict:
            obj = tools_dict[name]
    except Exception:
        obj = None
    if obj is None:
        try:
            disc_dict = toolkit.get_discoverable_tools()
            if name in disc_dict:
                obj = disc_dict[name]
        except Exception:
            obj = None
    if obj is None:
        return ""

    # Tool wrapper exposes structured descriptions.
    short = getattr(obj, "short_desc", None)
    long = getattr(obj, "long_desc", None)
    if short or long:
        return "\n\n".join(p for p in (short, long) if p).strip()

    # Raw callable: fall back to the function's docstring.
    doc = getattr(obj, "__doc__", None)
    if doc:
        return inspect.cleandoc(doc)
    return ""


def _tool_type_value(toolkit: Any, name: str) -> str:
    """Return the tool type as the enum's string value (e.g. ``"read"``).

    Some tools may expose the enum, others a raw string — normalize both.
    """
    try:
        tt = toolkit.tool_type(name)
    except Exception:
        return ""
    value = getattr(tt, "value", tt)
    return str(value) if value is not None else ""


def _mutates_state(toolkit: Any, name: str) -> bool:
    try:
        return bool(toolkit.tool_mutates_state(name))
    except Exception:
        return False


def _collect_toolkit(toolkit: Any) -> list[dict[str, Any]]:
    """Build a sorted list of tool metadata dicts for a single toolkit."""
    if toolkit is None:
        return []

    rows: dict[str, dict[str, Any]] = {}

    try:
        nondisc = toolkit.get_tools()
    except Exception:
        nondisc = {}
    for name in nondisc.keys():
        rows[name] = {
            "name": name,
            "discoverable": False,
            "tool_type": _tool_type_value(toolkit, name),
            "mutates_state": _mutates_state(toolkit, name),
            "doc": _tool_doc(toolkit, name),
        }

    try:
        disc = toolkit.get_discoverable_tools()
    except Exception:
        disc = {}
    for name in disc.keys():
        # Discoverable tools should never overlap with non-discoverable ones,
        # but guard anyway so a single bug doesn't silently drop an entry.
        rows[name] = {
            "name": name,
            "discoverable": True,
            "tool_type": _tool_type_value(toolkit, name),
            "mutates_state": _mutates_state(toolkit, name),
            "doc": _tool_doc(toolkit, name),
        }

    return [rows[k] for k in sorted(rows.keys())]


def _build_env(domain_name: str):
    """Construct an Environment for ``domain_name``.

    ``banking_knowledge`` needs an explicit retrieval variant; every other
    registered domain uses a zero-arg constructor.
    """
    env_ctor = registry.get_env_constructor(domain_name)
    if domain_name == "banking_knowledge":
        return env_ctor(retrieval_variant="bm25"), "bm25"
    return env_ctor(), None


def build_catalog() -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for domain_name in registry.get_domains():
        try:
            env, variant = _build_env(domain_name)
        except Exception as exc:
            catalog.append(
                {
                    "domain_name": domain_name,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
            )
            continue

        assistant_tools = _collect_toolkit(getattr(env, "tools", None))
        user_tools = _collect_toolkit(getattr(env, "user_tools", None))

        counts = {
            "assistant_nondiscoverable": sum(
                1 for t in assistant_tools if not t["discoverable"]
            ),
            "assistant_discoverable": sum(
                1 for t in assistant_tools if t["discoverable"]
            ),
            "user_nondiscoverable": sum(
                1 for t in user_tools if not t["discoverable"]
            ),
            "user_discoverable": sum(1 for t in user_tools if t["discoverable"]),
        }

        record: dict[str, Any] = {"domain_name": domain_name}
        if variant is not None:
            record["variant"] = variant
        record["toolkit_counts"] = counts
        record["toolkits"] = [
            {"env_type": "assistant", "tools": assistant_tools},
            {"env_type": "user", "tools": user_tools},
        ]
        catalog.append(record)

    return catalog


def main() -> int:
    catalog = build_catalog()
    json.dump(catalog, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
