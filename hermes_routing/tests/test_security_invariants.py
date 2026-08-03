"""Security invariants — Python port of security-invariants.test.ts (planner
and type-model half). The orchestrator/analyzer half is covered in
test_orchestrator.py once the orchestrator port lands.

Every test was written against a MUTATION in the TS source (see the TS file's
header). The fixture-strength guard is preserved: fix the fixture so it can
actually reach the wrong answer, and assert the rating arithmetic so a local
model can never win on score alone and mask the policy filter's absence.
"""

import pytest

from hermes_routing import RoutePlanner
from hermes_routing.types import leaves_device, location_tier, model_reach

from conftest import make_compiled_request, make_model

# A cloud model rated ABOVE any score a local model can reach, so only the
# policy ceiling can keep it out (not the sort). Local worst case:
# 100 (local) + qualityRating + 6 specialties*18 + 20 (freshness).
LOCAL_BONUS = 100
SPECIALTY_BONUS = 18
FRESHNESS_BONUS = 20
MAX_SPECIALTIES = 6
LOCAL_CEILING = LOCAL_BONUS + 60 + SPECIALTY_BONUS * MAX_SPECIALTIES + FRESHNESS_BONUS

CLOUD = make_model(
    "cloud:strong",
    capabilities=["chat", "reasoning", "coding", "documents"],
    quality_rating=300,
    context_window=200000,
    location="cloud",
    transport="remote",
)
LAN_PEER = make_model(
    "network:peer",
    capabilities=["chat", "reasoning", "coding", "documents"],
    quality_rating=300,
    context_window=128000,
    location="network",
    transport="remote",
)
RENTED = make_model(
    "remote:rented",
    capabilities=["chat", "reasoning", "coding", "documents"],
    quality_rating=300,
    context_window=128000,
    location="remote",
    transport="remote",
)
LOCAL = make_model(
    "local:general",
    capabilities=["chat", "reasoning", "coding", "documents"],
    quality_rating=60,
    context_window=32000,
    role="general",
)
SCAFFOLD = make_model(
    "local:scaffold",
    capabilities=["chat"],
    quality_rating=5,
    provider="quorum",
    transport="in_process",
)


def _request(policy):
    return make_compiled_request(policy=policy)


def _plan(policy, models):
    return RoutePlanner().plan(_request(policy), models)


def test_fixture_outranks_local_on_score_alone():
    # Reads the fixture, not a copy: a guard holding its own literal drifts.
    for model in (CLOUD, LAN_PEER, RENTED):
        assert model.quality_rating > LOCAL_CEILING


@pytest.mark.parametrize("policy", ["private", "offline"])
def test_policy_refuses_cloud_that_would_win_on_score(policy):
    plan = _plan(policy, [LOCAL, CLOUD, SCAFFOLD])
    assert plan.route == "local"
    assert plan.model_id != CLOUD.id
    assert plan.cloud_disclosure is None


def test_private_refuses_cloud_even_when_only_capable_model():
    plan = _plan("private", [CLOUD, SCAFFOLD])
    assert plan.route == "local"
    assert plan.model_id == SCAFFOLD.id
    assert plan.degraded is True


def test_quality_still_allows_cloud():
    plan = _plan("quality", [LOCAL, CLOUD])
    assert plan.route == "cloud"
    assert plan.model_id == CLOUD.id
    assert plan.cloud_disclosure is not None


def test_offline_refuses_loopback_even_when_best_available():
    plan = _plan("offline", [LOCAL, SCAFFOLD])
    assert plan.model_id == SCAFFOLD.id
    assert all(step.location in ("device", "local") for step in plan.steps)


def test_offline_has_no_route_without_in_process():
    with pytest.raises(ValueError, match="No available model satisfies"):
        _plan("offline", [LOCAL, CLOUD])


@pytest.mark.parametrize("model", [LAN_PEER, RENTED])
def test_private_refuses_network_and_remote(model):
    plan = _plan("private", [LOCAL, model, SCAFFOLD])
    assert plan.model_id != model.id
    assert all(step.location in ("device", "local") for step in plan.steps)


@pytest.mark.parametrize("model", [LAN_PEER, RENTED])
def test_quality_discloses_egress_for_network_and_remote(model):
    plan = _plan("quality", [LOCAL, model])
    assert plan.model_id == model.id
    assert plan.route == model.location
    assert plan.cloud_disclosure is not None


def test_offline_refuses_network_and_remote():
    with pytest.raises(ValueError, match="No available model satisfies"):
        _plan("offline", [LAN_PEER, RENTED])


def test_sensitive_content_stays_local_even_under_quality():
    req = make_compiled_request(policy="quality")
    req.requirements.contains_sensitive_data = True
    req.requirements.sensitive_data_categories = ["confidential"]
    plan = RoutePlanner().plan(req, [LOCAL, LAN_PEER])
    assert plan.model_id == LOCAL.id
    assert plan.cloud_disclosure is None


def test_contradictory_cloud_inprocess_refused_private_and_offline():
    contradictory = make_model(
        "cloud:contradictory",
        capabilities=["chat", "reasoning", "coding", "documents"],
        quality_rating=999,
        location="cloud",
        transport="in_process",
    )
    for policy in ("private", "offline"):
        with pytest.raises(ValueError, match="No available model satisfies"):
            _plan(policy, [contradictory])
    # A genuinely in-process local model still serves offline.
    plan = _plan("offline", [contradictory, SCAFFOLD])
    assert plan.model_id == SCAFFOLD.id


def test_model_reach_never_reports_further_tier_than_declared():
    locations = ["local", "network", "remote", "cloud"]
    transports = ["in_process", "loopback", "remote"]
    for location in locations:
        for transport in transports:
            m = make_model("m", location=location, transport=transport)
            reach = model_reach(m)
            assert location_tier(reach) <= location_tier(location)
            if reach != location:
                assert location == "local"
                assert transport == "in_process"


def test_disclosure_present_exactly_when_plan_leaves_device():
    cases = [
        ("local only", "balanced", [LOCAL, SCAFFOLD]),
        ("cloud permitted", "quality", [LOCAL, CLOUD]),
        ("cloud refused", "private", [LOCAL, CLOUD]),
        ("scaffold only", "offline", [SCAFFOLD]),
    ]
    for name, policy, models in cases:
        plan = _plan(policy, models)
        assert leaves_device(plan.route) == (plan.cloud_disclosure is not None), name
