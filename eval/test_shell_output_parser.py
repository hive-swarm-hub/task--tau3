"""Tests for ``interventions.shell_output_parser``.

The parser bridges terminal_use retrieval shell outputs (ls/cat/grep) into
text chunks that existing annotator code knows how to process. These tests
hardcode representative fixtures and exercise each public function.

Run: python3 -m pytest eval/test_shell_output_parser.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interventions.shell_output_parser import (  # noqa: E402
    extract_file_paths,
    extract_kb_docs,
    extract_mentioned_tools,
    is_shell_output,
)


# ── fixtures ─────────────────────────────────────────────────────────────────

LS_OUTPUT = (
    "doc_checking_accounts_blue_account_001.json\n"
    "doc_checking_accounts_green_account_001.json\n"
    "doc_savings_accounts_basic_001.json\n"
)

GREP_L_OUTPUT = (
    "documents/doc_checking_accounts_blue_account_001.json\n"
    "documents/doc_disputes_cash_back_001.json\n"
)

GREP_N_OUTPUT = (
    "documents/doc_disputes_cash_back_001.json:4:  "
    '"tool_references": ["submit_cash_back_dispute_0589"],\n'
    "documents/doc_disputes_cash_back_001.json:7:  "
    '"content": "Use submit_cash_back_dispute_0589 when the customer disputes..."\n'
)

CAT_OUTPUT_ONE = (
    '{"id": "doc_checking_accounts_blue_account_001", '
    '"title": "Blue Account at a glance", '
    '"content": "Monthly fee: $20. Waiver at $625 balance.", '
    '"tool_references": ["update_account_0001"]}'
)

CAT_OUTPUT_THREE = (
    '{"id": "doc_a", "title": "A", "content": "aaa"}\n'
    '{"id": "doc_b", "title": "B", "content": "bbb", '
    '"tool_references": ["submit_cash_back_dispute_0589"]}\n'
    '{"id": "doc_c", "title": "C", "content": "ccc"}'
)

KB_SEARCH_JSON = (
    '{"results": [{"doc_id": "doc_x", "score": 0.9, '
    '"content": "blah blah"}]}'
)


# ── is_shell_output ──────────────────────────────────────────────────────────

def test_is_shell_output_ls():
    assert is_shell_output(None, LS_OUTPUT) is True


def test_is_shell_output_grep_n():
    assert is_shell_output(None, GREP_N_OUTPUT) is True


def test_is_shell_output_grep_l():
    assert is_shell_output(None, GREP_L_OUTPUT) is True


def test_is_shell_output_cat_json():
    assert is_shell_output(None, CAT_OUTPUT_ONE) is True


def test_is_shell_output_tool_name_shell():
    # Any content routes to True when the tool name itself is "shell".
    assert is_shell_output("shell", "anything goes here") is True


def test_is_shell_output_rejects_kb_search():
    assert is_shell_output("KB_search", KB_SEARCH_JSON) is False
    # Also, even with no tool name, KB_search-shaped JSON should not match.
    assert is_shell_output(None, KB_SEARCH_JSON) is False


def test_is_shell_output_empty():
    assert is_shell_output(None, "") is False


# ── extract_kb_docs ──────────────────────────────────────────────────────────

def test_extract_kb_docs_single_cat():
    chunks = extract_kb_docs(CAT_OUTPUT_ONE)
    assert len(chunks) == 1
    assert "Blue Account" in chunks[0]


def test_extract_kb_docs_three_concatenated():
    chunks = extract_kb_docs(CAT_OUTPUT_THREE)
    assert len(chunks) == 3
    assert '"id": "doc_a"' in chunks[0]
    assert '"id": "doc_b"' in chunks[1]
    assert '"id": "doc_c"' in chunks[2]


def test_extract_kb_docs_grep_l_returns_empty():
    # -l form is just file paths; no content to extract.
    assert extract_kb_docs(GREP_L_OUTPUT) == []


def test_extract_kb_docs_ls_returns_empty():
    assert extract_kb_docs(LS_OUTPUT) == []


def test_extract_kb_docs_grep_n_returns_excerpts():
    chunks = extract_kb_docs(GREP_N_OUTPUT)
    assert len(chunks) == 1
    assert "submit_cash_back_dispute_0589" in chunks[0]


# ── extract_mentioned_tools ──────────────────────────────────────────────────

def test_extract_mentioned_tools_from_grep():
    tools = extract_mentioned_tools(GREP_N_OUTPUT)
    assert "submit_cash_back_dispute_0589" in tools


def test_extract_mentioned_tools_from_cat():
    tools = extract_mentioned_tools(CAT_OUTPUT_THREE)
    assert "submit_cash_back_dispute_0589" in tools


def test_extract_mentioned_tools_empty():
    assert extract_mentioned_tools("") == set()


# ── extract_file_paths ───────────────────────────────────────────────────────

def test_extract_file_paths_grep_l():
    paths = extract_file_paths(GREP_L_OUTPUT)
    assert paths == [
        "documents/doc_checking_accounts_blue_account_001.json",
        "documents/doc_disputes_cash_back_001.json",
    ]


def test_extract_file_paths_ls():
    paths = extract_file_paths(LS_OUTPUT)
    assert len(paths) == 3
    assert "doc_checking_accounts_blue_account_001.json" in paths


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
