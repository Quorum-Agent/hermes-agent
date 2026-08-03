"""Policy reach copy — Python port of policy-copy.test.ts."""

from hermes_routing import POLICIES, get_policy
from hermes_routing.policy_copy import (
    LOCATION_NOUNS,
    inference_reach_sentence,
    next_location_beyond,
    policies_without_search,
    policy_description,
    tool_reach_sentence,
)
from hermes_routing.types import EXECUTION_LOCATIONS, PolicyDefinition


def _pol(label, tool_ceiling):
    return PolicyDefinition(
        id=label.lower().replace(" ", "_"),
        label=label,
        intent="",
        inference_ceiling="local",
        tool_ceiling=tool_ceiling,
        prefer_local=True,
    )


def nouns_present(text: str):
    return [loc for loc in EXECUTION_LOCATIONS if LOCATION_NOUNS[loc] in text]


def test_offline_description_matches_invariant():
    # I-4: offline declares ceiling "device" and never offers the local network.
    offline = get_policy("offline")
    assert offline.inference_ceiling == "device"
    assert offline.description.startswith(offline.intent)
    assert "run no further than Quorum's own process" in offline.description
    assert "do not reach a loopback server on this machine" in offline.description
    assert "No tools run." in offline.description


def test_five_policies_present():
    assert sorted(POLICIES.keys()) == [
        "balanced",
        "cost_controlled",
        "offline",
        "private",
        "quality",
    ]


def test_private_ceiling():
    private = get_policy("private")
    assert private.inference_ceiling == "local"
    assert private.tool_ceiling == "none"


def test_description_computed_not_literal():
    # The mutation this design defends against: a hand-written description.
    for policy in POLICIES.values():
        assert policy.description == policy_description(policy)


def test_next_location_beyond_reads_order():
    for i, loc in enumerate(EXECUTION_LOCATIONS):
        expected = (
            EXECUTION_LOCATIONS[i + 1] if i + 1 < len(EXECUTION_LOCATIONS) else None
        )
        assert next_location_beyond(loc) == expected


def test_reach_sentence_names_only_entitled_tiers():
    for policy in POLICIES.values():
        named = nouns_present(policy.description)
        inferred = inference_reach_sentence(policy)
        tool = tool_reach_sentence(policy)
        assert inferred in policy.description
        assert tool in policy.description


def test_intent_makes_no_reach_claim():
    for policy in POLICIES.values():
        assert nouns_present(policy.intent) == []


def test_policies_without_search_names_network_tool_policy():
    # A policy with toolCeiling "network" permits tools but cannot run a web
    # search; a filter on toolCeiling=="none" would wrongly leave it off.
    fixture = [
        _pol("Private", "none"),
        _pol("Balanced", "web"),
        _pol("LAN tools", "network"),
        _pol("Offline", "none"),
    ]
    got = [p.label for p in policies_without_search(fixture)]
    assert got == ["Private", "LAN tools", "Offline"]


def test_policies_without_search_reads_tool_location():
    got = policies_without_search([_pol("Balanced", "web")], "cloud")
    assert [p.label for p in got] == ["Balanced"]


def test_policies_without_search_splits_shipped_set():
    named = [p.label for p in policies_without_search(list(POLICIES.values()))]
    assert len(named) > 0
    assert len(named) < len(POLICIES)
    assert "Offline" in named
    assert "Balanced" not in named
