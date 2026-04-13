"""Utility module for parsing shell tool output into annotator-friendly content.

Used when tau3-bench is run with ``RETRIEVAL_VARIANT=terminal_use`` and the
agent's tool results contain ``ls`` / ``cat`` / ``grep`` output instead of
the usual ``KB_search`` JSON chunks.

This is a stateless library. It is *not* registered as an Intervention — the
interventions that consume KB content (``interventions.banking`` etc.) call
into these helpers to extract text chunks and tool-name mentions from shell
results.

Stdlib only.
"""

from __future__ import annotations

import re


# Matches a discoverable tool name: lowercase snake_case followed by a
# 4-or-more digit suffix. e.g. ``submit_cash_back_dispute_0589``.
_TOOL_NAME_RE = re.compile(r"\b([a-z_][a-z_]*_\d{4,})\b")

# Matches a ``grep -n`` triple: path:lineno:text.
_GREP_N_RE = re.compile(r"^([^\s:][^:]*):(\d+):(.*)$", re.MULTILINE)

# Matches a bare filename on a line (ls output / grep -l output).
_FILENAME_LINE_RE = re.compile(r"^[\w./-]+\.(?:json|txt|md)$", re.MULTILINE)

# Matches the start of a KB-doc JSON object (on its own line or at offset 0).
_DOC_START_RE = re.compile(r'(?=\{\s*"(?:doc_)?id"\s*:)')


def is_shell_output(tool_name: str | None, content: str) -> bool:
    """Heuristic: does this ToolMessage content look like shell tool output?"""
    if tool_name == "shell":
        return True
    if not content:
        return False
    stripped = content.strip()
    # grep -n triples are a very strong signal.
    if _GREP_N_RE.search(content):
        return True
    # cat-dumped KB doc JSON: starts with { and has doc_id/id + content/title.
    if stripped.startswith("{") and (
        '"doc_id"' in stripped or '"id"' in stripped
    ) and ('"content"' in stripped or '"title"' in stripped):
        # Distinguish from KB_search JSON which typically wraps docs in a list
        # or has a ``results``/``hits`` key.
        if not stripped.startswith(('{"results"', '{"hits"', '[')):
            return True
    # ls / grep -l output: one filename per line, no JSON braces.
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if lines and all(_FILENAME_LINE_RE.match(ln) for ln in lines):
        return True
    return False


def extract_kb_docs(content: str) -> list[str]:
    """From cat/grep output, extract the text of each fetched KB doc."""
    if not content:
        return []
    stripped = content.strip()

    # grep -n output: return just the matched text excerpts, joined.
    grep_matches = _GREP_N_RE.findall(content)
    if grep_matches:
        excerpts = [text.strip() for _, _, text in grep_matches if text.strip()]
        return ["\n".join(excerpts)] if excerpts else []

    # ls / grep -l output: just filenames, no content to extract.
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if lines and all(_FILENAME_LINE_RE.match(ln) for ln in lines):
        return []

    # cat output — possibly concatenated. Split on doc-JSON boundaries.
    if stripped.startswith("{"):
        # Find all start positions of doc JSON objects.
        starts = [m.start() for m in _DOC_START_RE.finditer(stripped)]
        if len(starts) <= 1:
            return [stripped]
        chunks = []
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(stripped)
            chunk = stripped[start:end].strip()
            if chunk:
                chunks.append(chunk)
        return chunks

    return []


def extract_mentioned_tools(content: str) -> set[str]:
    """From shell output, pull out any discoverable tool name mentions."""
    if not content:
        return set()
    return set(_TOOL_NAME_RE.findall(content))


def extract_file_paths(content: str) -> list[str]:
    """From ls / grep -l output, return the list of files the agent found."""
    if not content:
        return []
    paths: list[str] = []
    seen: set[str] = set()
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        # grep -l returns bare paths; ls returns bare filenames; both match.
        if _FILENAME_LINE_RE.match(line):
            if line not in seen:
                seen.add(line)
                paths.append(line)
    return paths


__all__ = [
    "is_shell_output",
    "extract_kb_docs",
    "extract_mentioned_tools",
    "extract_file_paths",
]
