"""Intervention registry framework for the τ³-bench swarm agent.

Interventions are first-class, registered, discoverable objects. Previously the
banking agent had 9 interventions embedded inline inside ``_gate_tool_calls``
and ``annotate_banking`` — new swarm agents had to grep a 1200-line file to
find them. This module provides the hook-dispatch infrastructure; the
individual plug-ins live in sibling modules inside this package — e.g.
``interventions.banking`` (A-H), ``interventions.prefer_discoverable_reads`` (J),
``interventions.verify_before_mutate`` (K). See ``docs/interventions_inventory.md``
for the per-intervention reference.

Typical usage:

    from interventions import REGISTRY, HookContext
    # inside _gate_tool_calls
    for intv in REGISTRY.for_hook("gate_pre"):
        ctx = HookContext(tool_call=tc, state=self._task_state, ...)
        result = intv.apply(ctx)
        if result and result.drop:
            ...

Registration order is preserved — interventions fire in the order they were
registered within a given hook. Future agents can add their own interventions
by importing REGISTRY and calling ``REGISTRY.register(Intervention(...))``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional


HookType = Literal[
    "prompt",        # system prompt augmentation (runs once at agent init)
    "annotator",     # annotate_banking — modifies KB_search results before LLM sees them
    "gate_pre",      # before tool call dispatch — can drop/rewrite tool calls
    "gate_post",     # after dispatch — injects reminders on give_discoverable_user_tool
    "state_track",   # _track_state — measures but does not intervene
    "tool_result",   # when a tool result lands — hook for caching/parsing
]

_VALID_HOOKS = frozenset([
    "prompt", "annotator", "gate_pre", "gate_post", "state_track", "tool_result",
])
_VALID_STATUSES = frozenset(["active", "disabled", "experimental"])


@dataclass(frozen=True)
class HookContext:
    """Immutable context passed to each intervention's apply() method.

    Fields are optional because different hook types populate different
    subsets. A gate_pre hook gets ``tool_call`` + ``state``; an annotator
    hook gets ``state`` + ``meta={"content": ...}``; a state_track hook
    gets ``assistant_msg`` + ``incoming``.
    """
    tool_call: Optional[Any] = None            # the ToolCall about to be made
    assistant_msg: Optional[Any] = None        # full AssistantMessage (gate_post, state_track)
    state: dict = field(default_factory=dict)  # the agent's _task_state (mutable — intentional)
    incoming: Any = None                       # incoming UserMessage or ToolMessage
    meta: dict = field(default_factory=dict)   # hook-specific extra (e.g., annotator's `content`)


@dataclass
class HookResult:
    """What an intervention returns.

    None means "no-op, continue with the next intervention". A non-None
    result signals the caller to act on whatever fields are populated.
    """
    drop: bool = False                           # if True, remove this tool call
    replace_with: Optional[Any] = None           # replace the call with a new ToolCall
    annotation: Optional[str] = None             # text to append (for annotator hooks)
    drop_note: Optional[str] = None              # user-facing message explaining a drop
    log: Optional[dict] = None                   # entry to append to gate_interventions log


@dataclass
class Intervention:
    """A registered intervention.

    ``id`` is a stable short label ("A", "B", ..., "J") that callers and
    tests can reference. ``name`` is a kebab-case human-readable label.
    ``apply`` is the callable that receives a HookContext and returns an
    Optional[HookResult].
    """
    id: str
    name: str
    hook: HookType
    target_cluster: str                          # "verification" | "arguments" | "dispute" | "execution" | "discovery" | "any"
    author: str
    description: str
    status: Literal["active", "disabled", "experimental"] = "active"
    measured_impact: Optional[dict] = None       # e.g. {"lite_delta_tasks": 1.0, "verified_sha": "..."}
    apply: Optional[Callable[[HookContext], Optional[HookResult]]] = None


class InterventionRegistry:
    """Ordered registry of interventions grouped by hook type.

    Registration order is preserved within a hook bucket — iterating
    ``for_hook(h)`` yields active interventions in the order they were
    registered. Later agents can swap ordering by setting status="disabled"
    and re-registering a reordered version.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Intervention] = {}
        self._order: list[str] = []

    def register(self, intervention: Intervention, *, force: bool = False) -> None:
        """Register an intervention. Raises ValueError on duplicate id.

        Use ``force=True`` to overwrite an existing registration (preserves
        original position in iteration order).
        """
        if intervention.hook not in _VALID_HOOKS:
            raise ValueError(
                f"intervention {intervention.id}: invalid hook {intervention.hook!r}, "
                f"expected one of {sorted(_VALID_HOOKS)}"
            )
        if intervention.status not in _VALID_STATUSES:
            raise ValueError(
                f"intervention {intervention.id}: invalid status {intervention.status!r}, "
                f"expected one of {sorted(_VALID_STATUSES)}"
            )
        if intervention.id in self._by_id:
            if not force:
                existing = self._by_id[intervention.id]
                raise ValueError(
                    f"intervention id {intervention.id!r} already registered "
                    f"(name={existing.name!r}, author={existing.author!r}) — "
                    f"pick a different id or pass force=True to overwrite"
                )
            self._by_id[intervention.id] = intervention
            return
        self._by_id[intervention.id] = intervention
        self._order.append(intervention.id)

    def for_hook(self, hook: HookType) -> list[Intervention]:
        """Return active interventions for ``hook`` in registration order."""
        return [
            self._by_id[i]
            for i in self._order
            if self._by_id[i].hook == hook
            and self._by_id[i].status == "active"
        ]

    def list(self, *, include_disabled: bool = False) -> list[Intervention]:
        """Return all registered interventions in registration order."""
        if include_disabled:
            return [self._by_id[i] for i in self._order]
        return [
            self._by_id[i]
            for i in self._order
            if self._by_id[i].status != "disabled"
        ]

    def get(self, id: str) -> Optional[Intervention]:
        return self._by_id.get(id)

    def set_status(self, id: str, status: str) -> None:
        """Flip status for intervention ``id`` to "active" | "disabled" | "experimental"."""
        if id not in self._by_id:
            raise KeyError(f"unknown intervention id: {id}")
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"invalid status {status!r}, expected one of {sorted(_VALID_STATUSES)}"
            )
        intv = self._by_id[id]
        # dataclass instance; we mutate in-place for simplicity
        object.__setattr__(intv, "status", status)  # works on non-frozen dataclass too


# Module-level singleton. Import and call ``REGISTRY.register(...)`` from
# your interventions plug-in. Banking's interventions are wired in
# ``interventions.banking`` (imported by agent.py at startup).
REGISTRY = InterventionRegistry()
