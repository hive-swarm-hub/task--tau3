"""Metadata quality validator for the Interventions Registry.

Guards against sloppy registrations drifting into main. Two layers:

1. **Live-registry tests** run the metadata checks against the actual
   ``REGISTRY`` populated at import time (by ``interventions_banking.py``
   and future per-domain modules). These are the gates CI enforces.

2. **Rule unit-tests** construct small in-memory ``Intervention`` objects
   and assert the validator helpers accept/reject them. These let us
   confirm the rules themselves are correct independent of what's
   currently registered.

Run: ``python3 -m pytest eval/test_intervention_metadata.py -q``
"""

from __future__ import annotations

import os
import re
import sys
from typing import Iterable

import pytest

# Make the repo root importable regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interventions import REGISTRY, Intervention  # noqa: E402

# Importing this module triggers registrations into REGISTRY at import time.
# We import lazily inside a fixture so that if interventions_banking.py has
# import errors, only the live-registry tests fail — the rule unit tests
# (which use synthetic Interventions) still run.
try:  # pragma: no cover — guarded import
    from interventions import banking as _interventions_banking  # noqa: F401,E402  (side-effect: registers)
except Exception as _exc:  # pragma: no cover
    _IMPORT_ERR = _exc
else:
    _IMPORT_ERR = None


# ── constants ────────────────────────────────────────────────────────────────

VALID_HOOKS = {
    "prompt", "annotator", "gate_pre", "gate_post", "state_track", "tool_result",
}
VALID_CLUSTERS = {
    "verification", "arguments", "dispute", "execution", "discovery", "any",
}
VALID_STATUSES = {"active", "disabled", "experimental"}

ID_RE = re.compile(r"^[A-Z][A-Z0-9]?$")
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
AUTHOR_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

MIN_DESCRIPTION_LEN = 20
MIN_REGISTERED = 9  # A-I must survive the refactor


# ── rule helpers ─────────────────────────────────────────────────────────────
# Each helper returns None if the intervention is valid, else a specific
# error string. Tests assert helper(intv) is None; failing tests surface
# the returned string via pytest.fail.


def _rule_id_shape(intv: Intervention) -> str | None:
    if not isinstance(intv.id, str) or not intv.id:
        return f"Intervention has empty or non-string id: {intv.id!r}"
    if len(intv.id) > 3:
        return (
            f"Intervention {intv.id!r} has id longer than 3 chars "
            f"(len={len(intv.id)}); keep IDs short like 'A', 'K2', 'XX'."
        )
    if not ID_RE.match(intv.id):
        return (
            f"Intervention {intv.id!r} has invalid id format: expected "
            f"^[A-Z][A-Z0-9]?$ (e.g. 'A', 'K2', 'XX'); got {intv.id!r}."
        )
    return None


def _rule_name_kebab(intv: Intervention) -> str | None:
    if not isinstance(intv.name, str) or not intv.name:
        return f"Intervention {intv.id} has empty or non-string name."
    if not NAME_RE.match(intv.name):
        suggested = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", intv.name).lower()
        suggested = re.sub(r"[^a-z0-9-]+", "-", suggested).strip("-")
        return (
            f"Intervention {intv.id} has invalid name {intv.name!r}: "
            f"expected kebab-case (like {suggested!r})."
        )
    return None


def _rule_hook_valid(intv: Intervention) -> str | None:
    if intv.hook not in VALID_HOOKS:
        return (
            f"Intervention {intv.id} has invalid hook {intv.hook!r}; "
            f"expected one of {sorted(VALID_HOOKS)}."
        )
    return None


def _rule_cluster_valid(intv: Intervention) -> str | None:
    if intv.target_cluster not in VALID_CLUSTERS:
        return (
            f"Intervention {intv.id} has invalid target_cluster "
            f"{intv.target_cluster!r}; expected one of {sorted(VALID_CLUSTERS)}."
        )
    return None


def _rule_description_substantive(intv: Intervention) -> str | None:
    desc = intv.description or ""
    if not isinstance(desc, str):
        return f"Intervention {intv.id} has non-string description: {desc!r}."
    if len(desc) < MIN_DESCRIPTION_LEN:
        return (
            f"Intervention {intv.id} has description shorter than "
            f"{MIN_DESCRIPTION_LEN} chars ({len(desc)}): {desc!r}. "
            f"Write a one-sentence plain-English explanation."
        )
    return None


def _rule_author_format(intv: Intervention) -> str | None:
    if not isinstance(intv.author, str) or not intv.author:
        return f"Intervention {intv.id} has empty author."
    if not AUTHOR_RE.match(intv.author):
        return (
            f"Intervention {intv.id} has invalid author {intv.author!r}: "
            f"expected lowercase alias matching ^[a-z][a-z0-9_-]*$ "
            f"(e.g. 'junjie', 'brian2')."
        )
    return None


def _rule_status_valid(intv: Intervention) -> str | None:
    if intv.status not in VALID_STATUSES:
        return (
            f"Intervention {intv.id} has invalid status {intv.status!r}; "
            f"expected one of {sorted(VALID_STATUSES)}."
        )
    return None


def _rule_apply_callable(intv: Intervention) -> str | None:
    if intv.apply is None or not callable(intv.apply):
        return (
            f"Intervention {intv.id} has non-callable apply: {intv.apply!r}. "
            f"Set apply=<function> when registering."
        )
    return None


def _rule_measured_impact_shape(intv: Intervention) -> str | None:
    """Advisory — only runs if measured_impact is present."""
    mi = intv.measured_impact
    if mi is None:
        return None  # optional field; skip
    if not isinstance(mi, dict):
        return (
            f"Intervention {intv.id} has non-dict measured_impact: {mi!r}."
        )
    expected_delta_keys = {"lite_delta_tasks", "full_delta_tasks"}
    if not (expected_delta_keys & set(mi.keys())):
        return (
            f"Intervention {intv.id} has measured_impact without any delta "
            f"key; expected at least one of {sorted(expected_delta_keys)}."
        )
    return None


ALL_RULES = [
    _rule_id_shape,
    _rule_name_kebab,
    _rule_hook_valid,
    _rule_cluster_valid,
    _rule_description_substantive,
    _rule_author_format,
    _rule_status_valid,
    _rule_apply_callable,
    _rule_measured_impact_shape,
]


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def registered() -> list[Intervention]:
    """All interventions in the live REGISTRY (including disabled)."""
    if _IMPORT_ERR is not None:
        pytest.fail(
            f"interventions_banking.py failed to import: {_IMPORT_ERR!r}. "
            f"The registry will be empty — fix the import first."
        )
    intvs = REGISTRY.list(include_disabled=True)
    if not intvs:
        pytest.fail(
            "REGISTRY is empty — no interventions registered. "
            "interventions_banking should register at least "
            f"{MIN_REGISTERED} of them at import time."
        )
    return intvs


# ── live-registry tests ──────────────────────────────────────────────────────


def test_unique_ids(registered: list[Intervention]) -> None:
    """Every intervention has a unique ID."""
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for intv in registered:
        seen[intv.id] = seen.get(intv.id, 0) + 1
        if seen[intv.id] == 2:
            duplicates.append(intv.id)
    if duplicates:
        pytest.fail(
            f"Duplicate intervention IDs detected: {duplicates}. "
            f"Each Intervention.id must be unique across the registry."
        )


def test_id_format(registered: list[Intervention]) -> None:
    for intv in registered:
        err = _rule_id_shape(intv)
        if err:
            pytest.fail(err)


def test_name_kebab_case(registered: list[Intervention]) -> None:
    for intv in registered:
        err = _rule_name_kebab(intv)
        if err:
            pytest.fail(err)


def test_hook_valid(registered: list[Intervention]) -> None:
    for intv in registered:
        err = _rule_hook_valid(intv)
        if err:
            pytest.fail(err)


def test_target_cluster_valid(registered: list[Intervention]) -> None:
    for intv in registered:
        err = _rule_cluster_valid(intv)
        if err:
            pytest.fail(err)


def test_description_substantive(registered: list[Intervention]) -> None:
    for intv in registered:
        err = _rule_description_substantive(intv)
        if err:
            pytest.fail(err)


def test_author_format(registered: list[Intervention]) -> None:
    for intv in registered:
        err = _rule_author_format(intv)
        if err:
            pytest.fail(err)


def test_status_valid(registered: list[Intervention]) -> None:
    for intv in registered:
        err = _rule_status_valid(intv)
        if err:
            pytest.fail(err)


def test_apply_callable(registered: list[Intervention]) -> None:
    for intv in registered:
        err = _rule_apply_callable(intv)
        if err:
            pytest.fail(err)


def test_measured_impact_shape(registered: list[Intervention]) -> None:
    """Advisory: if measured_impact is present, it must have delta keys."""
    for intv in registered:
        err = _rule_measured_impact_shape(intv)
        if err:
            pytest.fail(err)


def test_at_least_one_active(registered: list[Intervention]) -> None:
    """Sanity: a registry with no active interventions means agent.py is no-op."""
    active = [i for i in registered if i.status == "active"]
    if not active:
        pytest.fail(
            "No interventions have status='active' — agent.py would run with "
            "zero hooks. At least one must be active."
        )


def test_schema_completeness(registered: list[Intervention]) -> None:
    """At least the 9 original A-I interventions must survive the refactor."""
    if len(registered) < MIN_REGISTERED:
        ids = sorted(i.id for i in registered)
        pytest.fail(
            f"Registry regressed: found {len(registered)} interventions "
            f"({ids}), expected at least {MIN_REGISTERED} (the original A-I). "
            f"Something dropped an intervention during the refactor."
        )


# ── rule unit-tests ──────────────────────────────────────────────────────────
# These exercise the validator itself using synthetic Interventions so we
# have confidence the rules actually reject the things we think they do —
# even if the live registry happens to be all-good.


def _good(**overrides) -> Intervention:
    """A known-valid Intervention; override one field to make it bad."""
    base = dict(
        id="Z",
        name="unit-test-fixture",
        hook="gate_pre",
        target_cluster="any",
        author="tester",
        description="A synthetic intervention used by validator unit tests only.",
        status="active",
        measured_impact=None,
        apply=lambda ctx: None,
    )
    base.update(overrides)
    return Intervention(**base)


def test_rule_id_accepts_good() -> None:
    for good_id in ["A", "Z", "K2", "XX", "A1"]:
        assert _rule_id_shape(_good(id=good_id)) is None, good_id


def test_rule_id_rejects_bad() -> None:
    for bad_id in ["aa", "abc", "A-B", "", "1A", "ABCD", "a"]:
        intv = Intervention(
            id=bad_id, name="x", hook="gate_pre", target_cluster="any",
            author="t", description="a" * 30, status="active",
            apply=lambda c: None,
        )
        assert _rule_id_shape(intv) is not None, (
            f"id {bad_id!r} should have been rejected"
        )


def test_rule_name_accepts_good() -> None:
    for good in ["verify-before-mutate", "a", "json-encode-inner-arguments", "a1-b2"]:
        assert _rule_name_kebab(_good(name=good)) is None, good


def test_rule_name_rejects_bad() -> None:
    for bad in ["VerifyBeforeMutate", "Verify-Before-Mutate", "verify_before_mutate",
                "-leading-hyphen", "trailing-", "", "UPPER"]:
        assert _rule_name_kebab(_good(name=bad)) is not None, (
            f"name {bad!r} should have been rejected"
        )


def test_rule_description_rejects_short() -> None:
    assert _rule_description_substantive(_good(description="TODO")) is not None
    assert _rule_description_substantive(_good(description="x" * 19)) is not None
    assert _rule_description_substantive(_good(description="x" * 20)) is None


def test_rule_rejects_invalid_hook() -> None:
    # Can't set an invalid hook via Literal type, but dataclass accepts it.
    intv = _good()
    object.__setattr__(intv, "hook", "not_a_hook")
    assert _rule_hook_valid(intv) is not None


def test_rule_rejects_invalid_cluster() -> None:
    assert _rule_cluster_valid(_good(target_cluster="bogus")) is not None


def test_rule_rejects_non_callable_apply() -> None:
    assert _rule_apply_callable(_good(apply=None)) is not None
    assert _rule_apply_callable(_good(apply="not a function")) is not None


def test_rule_measured_impact_accepts_none() -> None:
    assert _rule_measured_impact_shape(_good(measured_impact=None)) is None


def test_rule_measured_impact_requires_delta_key() -> None:
    assert _rule_measured_impact_shape(
        _good(measured_impact={"verified_sha": "abc"})
    ) is not None
    assert _rule_measured_impact_shape(
        _good(measured_impact={"lite_delta_tasks": 1.0})
    ) is None
    assert _rule_measured_impact_shape(
        _good(measured_impact={"full_delta_tasks": 2.0, "verified_sha": "deadbeef"})
    ) is None


def test_duplicate_detection_unit() -> None:
    """The duplicate-id check catches repeats across a list."""
    intvs = [_good(id="A"), _good(id="B"), _good(id="A")]
    seen: dict[str, int] = {}
    dupes: list[str] = []
    for i in intvs:
        seen[i.id] = seen.get(i.id, 0) + 1
        if seen[i.id] == 2:
            dupes.append(i.id)
    assert dupes == ["A"]


def test_author_format_rejects_bad() -> None:
    for bad in ["", "Alice", "alice space", "alice!", "2digit"]:
        assert _rule_author_format(_good(author=bad)) is not None, bad
    for good in ["junjie", "brian2", "alice-dev", "a_b"]:
        assert _rule_author_format(_good(author=good)) is None, good
