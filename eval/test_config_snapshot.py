"""Tests for eval config snapshotting + scripts/reproduce.py.

Covers:
- `_snapshot_interventions()` picks up registered interventions after plug-in import
- `_git_sha_or_dirty()` returns a 40-char hex (+optional -dirty) or 'unknown'
- `_git_current_branch_or_detached()` returns a plausible branch or 'unknown'
- Snapshot round-trip: env vars reconstructed correctly on the reproduce cmdline
- Reproduce.py handles a missing snapshot file gracefully (exits 1)

Run: `python3 -m pytest eval/test_config_snapshot.py -v`
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Make the repo root importable so `from eval.run_eval import ...` resolves.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _import_run_eval_helpers():
    """Import the snapshot helpers from eval/run_eval.py without running main().

    The module has side effects (registers a custom agent factory with tau2),
    but those are benign — the registry already tolerates re-registration in
    test contexts.
    """
    from eval import run_eval  # noqa: WPS433 — intentional import in function
    return run_eval


# ── _snapshot_interventions ─────────────────────────────────────────────────


def test_snapshot_interventions_returns_list_after_banking_import():
    """After importing interventions.banking, REGISTRY is non-empty."""
    from interventions import REGISTRY  # noqa: F401
    import interventions.banking  # noqa: F401 — triggers registration

    run_eval = _import_run_eval_helpers()
    snap = run_eval._snapshot_interventions()
    assert isinstance(snap, list)
    assert len(snap) > 0, "expected banking interventions to be registered"
    # Structural check — every record has the documented fields.
    for entry in snap:
        assert set(entry.keys()) >= {"id", "name", "hook", "status", "author"}
        assert entry["status"] in {"active", "disabled", "experimental"}


# ── _git_sha_or_dirty ───────────────────────────────────────────────────────


def test_git_sha_or_dirty_returns_valid_shape():
    """Returns a 40-hex SHA (optionally suffixed '-dirty'), or 'unknown'."""
    run_eval = _import_run_eval_helpers()
    sha = run_eval._git_sha_or_dirty()
    assert isinstance(sha, str)
    if sha == "unknown":
        return  # graceful fallback path (no git binary / not a repo)
    # Strip the optional '-dirty' suffix before shape check.
    core = sha[:-len("-dirty")] if sha.endswith("-dirty") else sha
    assert re.fullmatch(r"[0-9a-f]{40}", core), f"expected 40-hex SHA, got {core!r}"


# ── _git_current_branch_or_detached ─────────────────────────────────────────


def test_git_branch_returns_string():
    """Returns a branch name ('main', 'hive/foo', 'HEAD' if detached) or 'unknown'."""
    run_eval = _import_run_eval_helpers()
    branch = run_eval._git_current_branch_or_detached()
    assert isinstance(branch, str)
    assert len(branch) > 0
    # No newlines / whitespace leaking from the git command.
    assert "\n" not in branch


# ── Reproduce.py round-trip ─────────────────────────────────────────────────


def test_reproduce_reconstructs_env_vars_from_snapshot(tmp_path):
    """Write a fake snapshot, invoke reproduce.py in print mode, check env vars appear."""
    fake = {
        "timestamp": "2026-04-12T21:45:00Z",
        "git_sha": "7f3a9c1abcdef0123456789abcdef0123456789a",
        "git_branch": "hive/test-branch",
        "env": {
            "RETRIEVAL_VARIANT": "terminal_use",            # default → should NOT appear
            "SOLVER_MODEL": "gpt-5.2",         # default → should NOT appear
            "USER_MODEL": "gpt-5.2",     # default → should NOT appear
            "EVAL_CONCURRENCY": "8",                # default → should NOT appear
            "EVAL_LITE": "1",                       # NON-default → should appear
            "SAMPLE_FRAC": "1.0",                   # default → should NOT appear
            "DISABLED_INTERVENTIONS": "E,F",        # NON-default → should appear
            "ENABLE_EXPERIMENTAL": "0",             # default → should NOT appear
        },
        "config": {"domain": "banking_knowledge", "split": "test",
                   "num_trials": 1, "max_concurrency": 8, "task_ids": [], "n_tasks": 0},
        "interventions": [
            {"id": "A", "name": "a", "hook": "gate_pre", "status": "active", "author": "x"},
            {"id": "B", "name": "b", "hook": "gate_pre", "status": "experimental", "author": "x"},
        ],
        "python_version": "3.11.0",
        "tau2_version": "1.0.0",
    }
    snap_path = tmp_path / "snap.json"
    snap_path.write_text(json.dumps(fake))

    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "reproduce.py"),
         "--snapshot", str(snap_path)],
        capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, f"reproduce.py failed: {result.stderr}"
    out = result.stdout

    # Non-default env vars MUST appear.
    assert "EVAL_LITE=1" in out
    assert "DISABLED_INTERVENTIONS=E,F" in out
    # Default-valued env vars MUST NOT appear (keeps the reproduce cmd punchy).
    assert "RETRIEVAL_VARIANT=terminal_use" not in out
    assert "SOLVER_MODEL=" not in out
    assert "SAMPLE_FRAC=" not in out
    # SHA prefix shown in the report + full SHA in the checkout line.
    assert "7f3a9c1" in out
    assert "git checkout 7f3a9c1abcdef0123456789abcdef0123456789a" in out
    # Intervention counts surfaced correctly.
    assert "1 active + 1 experimental" in out


def test_reproduce_warns_on_dirty_sha(tmp_path):
    """If the snapshot SHA ends in -dirty, reproduce.py must warn."""
    fake = {
        "timestamp": "2026-04-12T21:45:00Z",
        "git_sha": "7f3a9c1abcdef0123456789abcdef0123456789a-dirty",
        "git_branch": "hive/test",
        "env": {k: v for k, v in [
            ("RETRIEVAL_VARIANT", "bm25"), ("SOLVER_MODEL", "gpt-4.1-mini"),
            ("USER_MODEL", "gpt-4.1-2025-04-14"), ("EVAL_CONCURRENCY", "8"),
            ("EVAL_LITE", "0"), ("SAMPLE_FRAC", "1.0"),
            ("DISABLED_INTERVENTIONS", ""), ("ENABLE_EXPERIMENTAL", "0"),
        ]},
        "config": {"domain": "banking_knowledge", "split": "test",
                   "num_trials": 1, "max_concurrency": 8, "task_ids": [], "n_tasks": 0},
        "interventions": [],
        "python_version": "3.11.0",
        "tau2_version": "1.0.0",
    }
    snap_path = tmp_path / "dirty.json"
    snap_path.write_text(json.dumps(fake))

    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "reproduce.py"),
         "--snapshot", str(snap_path)],
        capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0
    assert "[DIRTY]" in result.stdout
    assert "uncommitted changes" in result.stdout


def test_reproduce_exits_1_on_missing_snapshot(tmp_path):
    """A non-existent snapshot path must exit 1 with a clear error on stderr."""
    missing = tmp_path / "does_not_exist.json"
    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "reproduce.py"),
         "--snapshot", str(missing)],
        capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 1
    assert "snapshot file not found" in result.stderr


def test_reproduce_exits_1_on_invalid_json(tmp_path):
    """A malformed snapshot must exit 1 with a parse-error message."""
    bad = tmp_path / "bad.json"
    bad.write_text("not json {[")
    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "reproduce.py"),
         "--snapshot", str(bad)],
        capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 1
    assert "could not parse snapshot" in result.stderr


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
