"""Policy reach copy — Python port of packages/core/src/policy-copy.ts.

The reach half of every policy description is COMPUTED from the ceiling it
describes; a description cannot contradict a value it is derived from. What
is hand-written is only the *intent*. `policy_description` is the only way a
description is produced — reintroducing a literal is the mutation this module
is defended against.
"""

from __future__ import annotations

from .types import (
    EXECUTION_LOCATIONS,
    location_tier,
    policy_permits_tool,
    WEB_SEARCH_LOCATION,
)

# One bare noun phrase per tier, used for both what a ceiling permits and what
# it excludes. One table rather than two — a "permits" and an "excludes"
# phrasing maintained separately is the same one-fact-two-fields shape this
# project keeps finding. These must stay mutually distinct and non-overlapping.
LOCATION_NOUNS: dict[str, str] = {
    "device": "Quorum's own process",
    "local": "a loopback server on this machine",
    "network": "your local network",
    "remote": "hardware you rent",
    "web": "the public internet",
    "cloud": "a vendor's API",
}


def next_location_beyond(ceiling: str) -> str | None:
    """The tier immediately beyond a ceiling, or None at the top of the order."""
    idx = location_tier(ceiling)
    if idx + 1 >= len(EXECUTION_LOCATIONS):
        return None
    return EXECUTION_LOCATIONS[idx + 1]


def _render_reach_claim(
    subject: str, permitted_up_to: str, excluded_from: str | None
) -> str:
    permitted = f"{subject} run no further than {LOCATION_NOUNS[permitted_up_to]}."
    if not excluded_from:
        return permitted
    return f"{permitted} They do not reach {LOCATION_NOUNS[excluded_from]}, or anything past it."


def inference_reach_sentence(policy) -> str:
    return _render_reach_claim(
        "Models",
        policy.inference_ceiling,
        next_location_beyond(policy.inference_ceiling),
    )


def tool_reach_sentence(policy) -> str:
    if policy.tool_ceiling == "none":
        return "No tools run."
    return _render_reach_claim(
        "Tools", policy.tool_ceiling, next_location_beyond(policy.tool_ceiling)
    )


def policy_description(policy) -> str:
    """Hand-written intent, then computed reach."""
    return " ".join([
        policy.intent,
        inference_reach_sentence(policy),
        tool_reach_sentence(policy),
    ])


def policies_without_search(policies, search_location: str = WEB_SEARCH_LOCATION):
    """Which policies cannot run a web search — identified via the same
    question the orchestrator asks, not by remembered names."""
    return [p for p in policies if not policy_permits_tool(p, search_location)]
