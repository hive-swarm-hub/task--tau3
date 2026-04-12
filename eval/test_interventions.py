"""Pytest tests for the Interventions Registry framework.

Tests the public API of `interventions.py` (InterventionRegistry,
Intervention, HookContext, HookResult). Each test uses a FRESH registry
via the `reg` fixture so tests are independent of ordering.

Run: python3 -m pytest eval/test_interventions.py -q
"""

import os
import sys
import dataclasses

import pytest

# Make the repo root importable so `import interventions` works regardless
# of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interventions import (  # noqa: E402  (sys.path mutation above)
    HookContext,
    HookResult,
    Intervention,
    InterventionRegistry,
)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def reg():
    """Fresh registry per test — never share the module-level REGISTRY."""
    return InterventionRegistry()


def _make(
    id_: str,
    *,
    hook: str = "gate_pre",
    status: str = "active",
    apply=None,
    name: str = None,
    target_cluster: str = "cluster-x",
    author: str = "tester",
    description: str = "test intervention",
):
    """Helper to build an Intervention with sensible defaults."""
    return Intervention(
        id=id_,
        name=name or f"name-{id_}",
        hook=hook,
        target_cluster=target_cluster,
        author=author,
        description=description,
        status=status,
        apply=apply if apply is not None else (lambda ctx: None),
    )


# ── core registry behavior ───────────────────────────────────────────────────

def test_register_and_list_preserves_insertion_order(reg):
    a = _make("A")
    b = _make("B")
    c = _make("C")
    reg.register(a)
    reg.register(b)
    reg.register(c)
    assert [iv.id for iv in reg.list()] == ["A", "B", "C"]


def test_for_hook_filters_by_hook_type(reg):
    reg.register(_make("G1", hook="gate_pre"))
    reg.register(_make("A1", hook="annotator"))
    reg.register(_make("G2", hook="gate_pre"))
    reg.register(_make("A2", hook="annotator"))
    assert [iv.id for iv in reg.for_hook("gate_pre")] == ["G1", "G2"]
    assert [iv.id for iv in reg.for_hook("annotator")] == ["A1", "A2"]


def test_for_hook_excludes_disabled_by_default(reg):
    reg.register(_make("A", hook="gate_pre"))
    reg.register(_make("B", hook="gate_pre"))
    reg.set_status("A", "disabled")
    assert [iv.id for iv in reg.for_hook("gate_pre")] == ["B"]
    # list() default also hides disabled
    assert [iv.id for iv in reg.list()] == ["B"]
    # list(include_disabled=True) DOES show it
    assert {iv.id for iv in reg.list(include_disabled=True)} == {"A", "B"}


def test_get_returns_intervention_or_none(reg):
    e = _make("E")
    reg.register(e)
    got = reg.get("E")
    assert got is not None
    assert got.id == "E"
    assert reg.get("nonexistent") is None


def test_set_status_transitions_between_active_and_disabled(reg):
    reg.register(_make("A", hook="gate_pre"))
    # active → disabled
    reg.set_status("A", "disabled")
    assert reg.for_hook("gate_pre") == []
    # disabled → active (reappears)
    reg.set_status("A", "active")
    assert [iv.id for iv in reg.for_hook("gate_pre")] == ["A"]


def test_duplicate_id_rejected(reg):
    reg.register(_make("DUP"))
    with pytest.raises((ValueError, KeyError, Exception)):
        reg.register(_make("DUP"))


def test_invalid_hook_type_rejected(reg):
    # If the framework validates, constructing or registering with a bogus
    # hook should raise. If it doesn't validate at construction, registering
    # should be the enforcement point.
    with pytest.raises(Exception):
        bad = Intervention(
            id="X",
            name="x",
            hook="not_a_real_hook",  # type: ignore[arg-type]
            target_cluster="c",
            author="a",
            description="d",
            apply=lambda ctx: None,
        )
        reg.register(bad)


def test_set_status_rejects_bogus_value(reg):
    reg.register(_make("A"))
    with pytest.raises(Exception):
        reg.set_status("A", "bogus")


def test_empty_registry(reg):
    assert reg.list() == []
    assert reg.list(include_disabled=True) == []
    assert reg.for_hook("gate_pre") == []
    assert reg.for_hook("annotator") == []


def test_registration_order_stable_not_alphabetical(reg):
    # Register in C, A, B order — for_hook must return [C, A, B],
    # NOT [A, B, C] (would indicate alphabetical sort).
    reg.register(_make("C", hook="gate_pre"))
    reg.register(_make("A", hook="gate_pre"))
    reg.register(_make("B", hook="gate_pre"))
    assert [iv.id for iv in reg.for_hook("gate_pre")] == ["C", "A", "B"]
    assert [iv.id for iv in reg.list()] == ["C", "A", "B"]


def test_set_status_unknown_id_raises(reg):
    with pytest.raises(Exception):
        reg.set_status("does_not_exist", "disabled")


def test_experimental_status_treated_as_non_active(reg):
    # `active` is the only status that for_hook should include; experimental
    # interventions should not fire by default. If the framework treats
    # experimental as active-ish, this test documents the expected behavior.
    reg.register(_make("E", hook="gate_pre", status="experimental"))
    reg.register(_make("A", hook="gate_pre", status="active"))
    active_ids = [iv.id for iv in reg.for_hook("gate_pre")]
    assert "A" in active_ids
    assert "E" not in active_ids


# ── HookContext + HookResult sanity ──────────────────────────────────────────

def test_hookcontext_is_frozen():
    ctx = HookContext()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.state = {"mutated": True}  # type: ignore[misc]


def test_hookresult_default_values():
    r = HookResult()
    assert r.drop is False
    assert r.replace_with is None
    assert r.annotation is None
    assert r.drop_note is None
    assert r.log is None


def test_hookresult_carries_drop_note():
    r = HookResult(drop=True, drop_note="stop doing that")
    assert r.drop is True
    assert r.drop_note == "stop doing that"


def test_hookresult_carries_replace_with_and_annotation():
    r = HookResult(replace_with="new_value", annotation="changed it")
    assert r.replace_with == "new_value"
    assert r.annotation == "changed it"


# ── integration smoke ────────────────────────────────────────────────────────

def test_intervention_apply_returns_hookresult(reg):
    iv = _make(
        "drop_one",
        hook="gate_pre",
        apply=lambda ctx: HookResult(drop=True, drop_note="nope"),
    )
    reg.register(iv)
    got = reg.get("drop_one")
    assert got is not None
    result = got.apply(HookContext())
    assert isinstance(result, HookResult)
    assert result.drop is True
    assert result.drop_note == "nope"


def test_intervention_apply_returning_none_is_noop(reg):
    iv = _make("noop", apply=lambda ctx: None)
    reg.register(iv)
    got = reg.get("noop")
    assert got is not None
    # Should not raise
    result = got.apply(HookContext())
    assert result is None


def test_for_hook_returns_objects_with_callable_apply(reg):
    reg.register(_make(
        "cb",
        hook="gate_pre",
        apply=lambda ctx: HookResult(annotation="touched"),
    ))
    hooks = reg.for_hook("gate_pre")
    assert len(hooks) == 1
    result = hooks[0].apply(HookContext())
    assert isinstance(result, HookResult)
    assert result.annotation == "touched"
