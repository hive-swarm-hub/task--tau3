"""Tests for DISABLED_INTERVENTIONS / ENABLE_EXPERIMENTAL env var support.

Each test constructs a FRESH ``InterventionRegistry`` and manually registers
sample interventions, then invokes the env-override helpers directly. We
never mutate the module-level ``REGISTRY`` — that would leak state between
tests (and into other test modules).

Run: python3 -m pytest eval/test_intervention_env_overrides.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

# Make the repo root importable so `import interventions` works regardless
# of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interventions import (  # noqa: E402  (sys.path mutation above)
    Intervention,
    InterventionRegistry,
    _apply_env_disables,
    _apply_env_enables,
)


def _make(id_: str, *, status: str = "active") -> Intervention:
    return Intervention(
        id=id_,
        name=f"name-{id_}",
        hook="gate_pre",
        target_cluster="cluster-x",
        author="tester",
        description=f"test intervention {id_}",
        status=status,
        apply=lambda ctx: None,
    )


def _fresh_registry_with(*interventions: Intervention) -> InterventionRegistry:
    reg = InterventionRegistry()
    for intv in interventions:
        reg.register(intv)
    return reg


# ── DISABLED_INTERVENTIONS ───────────────────────────────────────────────────

def test_disables_single_id(monkeypatch):
    monkeypatch.setenv("DISABLED_INTERVENTIONS", "E")
    reg = _fresh_registry_with(_make("E"), _make("F"))
    disabled = _apply_env_disables(reg)
    assert disabled == ["E"]
    assert reg.get("E").status == "disabled"
    assert reg.get("F").status == "active"


def test_disables_multiple_ids(monkeypatch):
    monkeypatch.setenv("DISABLED_INTERVENTIONS", "E,F")
    reg = _fresh_registry_with(_make("E"), _make("F"), _make("G"))
    disabled = _apply_env_disables(reg)
    assert disabled == ["E", "F"]
    assert reg.get("E").status == "disabled"
    assert reg.get("F").status == "disabled"
    assert reg.get("G").status == "active"


def test_case_insensitive(monkeypatch):
    monkeypatch.setenv("DISABLED_INTERVENTIONS", "e,f")
    reg = _fresh_registry_with(_make("E"), _make("F"))
    disabled = _apply_env_disables(reg)
    assert disabled == ["E", "F"]
    assert reg.get("E").status == "disabled"
    assert reg.get("F").status == "disabled"


def test_empty_is_noop(monkeypatch):
    monkeypatch.setenv("DISABLED_INTERVENTIONS", "")
    reg = _fresh_registry_with(_make("E"))
    disabled = _apply_env_disables(reg)
    assert disabled == []
    assert reg.get("E").status == "active"


def test_whitespace_only_is_noop(monkeypatch):
    monkeypatch.setenv("DISABLED_INTERVENTIONS", "   ")
    reg = _fresh_registry_with(_make("E"))
    disabled = _apply_env_disables(reg)
    assert disabled == []
    assert reg.get("E").status == "active"


def test_unknown_id_warns_and_continues(monkeypatch, capsys):
    monkeypatch.setenv("DISABLED_INTERVENTIONS", "ZZZ,E")
    reg = _fresh_registry_with(_make("E"))
    disabled = _apply_env_disables(reg)
    # Valid ID still gets disabled.
    assert disabled == ["E"]
    assert reg.get("E").status == "disabled"
    # Unknown ID emits a stderr warning.
    captured = capsys.readouterr()
    assert "ZZZ" in captured.err
    assert "unknown id" in captured.err


# ── ENABLE_EXPERIMENTAL ──────────────────────────────────────────────────────

def test_enable_experimental_flips_experimental_to_active(monkeypatch):
    monkeypatch.setenv("ENABLE_EXPERIMENTAL", "1")
    reg = _fresh_registry_with(
        _make("A"),                            # already active
        _make("J", status="experimental"),
        _make("K", status="experimental"),
        _make("X", status="disabled"),         # should stay disabled
    )
    flipped = _apply_env_enables(reg)
    assert sorted(flipped) == ["J", "K"]
    assert reg.get("A").status == "active"
    assert reg.get("J").status == "active"
    assert reg.get("K").status == "active"
    assert reg.get("X").status == "disabled"


def test_enable_experimental_default_is_noop(monkeypatch):
    monkeypatch.delenv("ENABLE_EXPERIMENTAL", raising=False)
    reg = _fresh_registry_with(_make("J", status="experimental"))
    flipped = _apply_env_enables(reg)
    assert flipped == []
    assert reg.get("J").status == "experimental"


# ── interaction: explicit disable beats implicit enable ─────────────────────

def test_both_env_vars_explicit_disable_wins(monkeypatch):
    """ENABLE_EXPERIMENTAL=1 DISABLED_INTERVENTIONS=J → J ends up disabled.

    Order matters: enables are applied first (J: experimental→active), then
    disables (J: active→disabled). K stays flipped to active.
    """
    monkeypatch.setenv("ENABLE_EXPERIMENTAL", "1")
    monkeypatch.setenv("DISABLED_INTERVENTIONS", "J")
    reg = _fresh_registry_with(
        _make("J", status="experimental"),
        _make("K", status="experimental"),
    )
    flipped = _apply_env_enables(reg)
    disabled = _apply_env_disables(reg)
    assert sorted(flipped) == ["J", "K"]
    assert disabled == ["J"]
    assert reg.get("J").status == "disabled"
    assert reg.get("K").status == "active"


# ── lazy application via for_hook() ──────────────────────────────────────────

def test_lazy_application_via_for_hook(monkeypatch, capsys):
    """Env overrides fire on the first for_hook() call and print the header."""
    monkeypatch.setenv("DISABLED_INTERVENTIONS", "E")
    reg = _fresh_registry_with(_make("E"), _make("F"))
    # Before calling for_hook(), the override hasn't fired yet.
    assert reg._env_applied is False
    assert reg.get("E").status == "active"
    # First for_hook() call triggers the lazy apply.
    active = reg.for_hook("gate_pre")
    assert [iv.id for iv in active] == ["F"]
    assert reg._env_applied is True
    assert reg.get("E").status == "disabled"
    # And the startup line was emitted to stderr.
    captured = capsys.readouterr()
    assert "env overrides" in captured.err
    assert "disabled=['E']" in captured.err


def test_no_env_vars_is_silent(monkeypatch, capsys):
    """When neither env var is set, we emit nothing to stderr."""
    monkeypatch.delenv("DISABLED_INTERVENTIONS", raising=False)
    monkeypatch.delenv("ENABLE_EXPERIMENTAL", raising=False)
    reg = _fresh_registry_with(_make("E"))
    _ = reg.for_hook("gate_pre")
    captured = capsys.readouterr()
    assert captured.err == ""
