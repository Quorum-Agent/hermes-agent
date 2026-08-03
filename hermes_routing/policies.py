"""Policies — Python port of packages/core/src/policies.ts.

Behaviour preserved exactly; values verbatim. Each policy declares ceilings
rather than booleans, and the user-visible description is COMPUTED from the
ceilings by policy_copy.policy_description (never hand-written).
"""

from __future__ import annotations

from .types import PolicyDefinition
from .policy_copy import policy_description


class UnsupportedPolicyError(ValueError):
    """Raised when a declared policy cannot yet be enforced truthfully."""


def require_enforceable_policy(mode: str) -> None:
    """Fail closed for policies whose security contract is not implemented.

    ``cost_controlled`` promises a rolling spend cap. Route-time token-price
    sorting is not that contract: retries, streaming usage, and concurrent
    sessions all spend against the same budget. Until an atomic usage ledger
    exists, accepting this mode would present an unenforced policy as active.
    """

    if mode == "cost_controlled":
        raise UnsupportedPolicyError(
            "Cost controlled mode is unavailable until Quorum has an atomic "
            "usage ledger; choose Private, Balanced, Best quality, or Offline."
        )


# Each entry omits `description`; it is derived at POLICIES construction.
_POLICY_SPECS: list[PolicyDefinition] = [
    PolicyDefinition(
        id="private",
        label="Private",
        intent="For work you want handled entirely by software you run yourself.",
        inference_ceiling="local",
        tool_ceiling="none",
        prefer_local=True,
    ),
    PolicyDefinition(
        id="balanced",
        label="Balanced",
        intent="The default: prefer what you host, and go further only when it clearly helps.",
        inference_ceiling="cloud",
        tool_ceiling="web",
        prefer_local=True,
    ),
    PolicyDefinition(
        id="quality",
        label="Best quality",
        intent="The strongest available route for each request.",
        inference_ceiling="cloud",
        tool_ceiling="web",
        prefer_local=False,
    ),
    PolicyDefinition(
        id="offline",
        label="Offline",
        intent="For a machine with no network, or with none of its model servers running.",
        inference_ceiling="device",
        tool_ceiling="none",
        prefer_local=True,
    ),
    PolicyDefinition(
        id="cost_controlled",
        label="Cost controlled",
        intent="Prefer routes that cost nothing, and cap what an exceptional request may spend.",
        inference_ceiling="cloud",
        tool_ceiling="web",
        prefer_local=True,
        cloud_budget_usd=1.0,
    ),
]


def _build_policy(p: PolicyDefinition) -> PolicyDefinition:
    return PolicyDefinition(
        id=p.id,
        label=p.label,
        intent=p.intent,
        inference_ceiling=p.inference_ceiling,
        tool_ceiling=p.tool_ceiling,
        prefer_local=p.prefer_local,
        description=policy_description(p),
        cloud_budget_usd=p.cloud_budget_usd,
    )


POLICIES: dict[str, PolicyDefinition] = {p.id: _build_policy(p) for p in _POLICY_SPECS}


def get_policy(mode: str) -> PolicyDefinition:
    return POLICIES[mode]
