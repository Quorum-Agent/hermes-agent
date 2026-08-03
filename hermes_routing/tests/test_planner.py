"""Route planner — Python port of key route-planner.test.ts cases.

These build CompiledRequest objects directly (via conftest helpers) rather than
going through the compiler, to test the planner in isolation.
"""

import pytest

from hermes_routing import RoutePlanner
from hermes_routing.policies import get_policy
from hermes_routing.types import (
    CompiledRequest,
    RequestAnalysis,
    RequestRequirements,
)

from conftest import make_compiled_request, make_message, make_model


def _request(
    policy, intent="conversation", sensitive=False, stirred_web_grounded=False
):
    req = make_compiled_request(policy=policy)
    req.requirements.intent = intent
    req.requirements.capabilities = ["chat"]
    if intent == "coding":
        req.requirements.capabilities.append("coding")
    if intent == "reasoning":
        req.requirements.capabilities.append("reasoning")
    if sensitive:
        req.requirements.contains_sensitive_data = True
        req.requirements.sensitive_data_categories = ["confidential"]
    if stirred_web_grounded:
        req.requirements.contains_web_grounded_data = True
    return req


LOCAL = make_model(
    "local:test",
    capabilities=["chat", "reasoning", "coding", "web", "documents"],
    quality_rating=60,
    context_window=32000,
)
CLOUD = make_model(
    "cloud:test",
    capabilities=["chat", "reasoning", "web", "documents"],
    quality_rating=90,
    context_window=128000,
    location="cloud",
    transport="remote",
    provider="openrouter",
)


def test_keeps_private_requests_local():
    plan = RoutePlanner().plan(_request("private", intent="research"), [LOCAL, CLOUD])
    assert plan.route == "local"
    assert plan.model_id == LOCAL.id
    assert plan.cloud_disclosure is None


def test_quality_prefers_stronger_cloud():
    plan = RoutePlanner().plan(_request("quality", intent="research"), [LOCAL, CLOUD])
    assert plan.route == "cloud"
    assert plan.cloud_disclosure is not None


def test_quality_keeps_sensitive_content_local():
    plan = RoutePlanner().plan(
        _request("quality", intent="research", sensitive=True), [LOCAL, CLOUD]
    )
    assert plan.route == "local"


def test_fails_closed_when_no_safe_local_route():
    with pytest.raises(ValueError, match="No available model"):
        RoutePlanner().plan(
            _request("quality", intent="research", sensitive=True), [CLOUD]
        )


def test_web_grounded_stays_local():
    plan = RoutePlanner().plan(
        _request("quality", intent="research", stirred_web_grounded=True),
        [LOCAL, CLOUD],
    )
    assert plan.route == "local"
    assert plan.safety["contains_web_grounded_data"] is True


def test_offline_only_uses_in_process():
    in_process = make_model(
        "local:in-process",
        capabilities=["chat"],
        quality_rating=1,
        transport="in_process",
    )
    plan = RoutePlanner().plan(_request("offline"), [LOCAL, in_process])
    assert plan.model_id == in_process.id


def test_prefers_matching_specialist():
    expert = make_model(
        "local:code-expert",
        capabilities=["chat", "coding"],
        specialties=["coding"],
        quality_rating=50,
    )
    plan = RoutePlanner().plan(_request("balanced", intent="coding"), [LOCAL, expert])
    assert plan.model_id == expert.id
    assert "coding specialist" in plan.rationale


def test_ordinary_conversation_not_sent_to_specialist():
    coding_expert = make_model(
        "local:code-expert",
        capabilities=["chat", "coding"],
        specialties=["coding"],
        quality_rating=50,
    )
    plan = RoutePlanner().plan(_request("balanced"), [LOCAL, coding_expert])
    assert plan.model_id == LOCAL.id


def test_duplicate_specialty_tags_do_not_inflate_score():
    single = make_model(
        "local:single",
        capabilities=["chat", "coding"],
        specialties=["coding"],
        quality_rating=50,
    )
    dup = make_model(
        "local:dup",
        capabilities=["chat", "coding"],
        specialties=["coding", "coding", "coding"],
        quality_rating=49,
    )
    plan = RoutePlanner().plan(_request("balanced", intent="coding"), [single, dup])
    assert plan.model_id == single.id


def test_quality_mode_anchored_to_quality_not_specialty():
    specialist = make_model(
        "local:low-quality-specialist",
        capabilities=["chat", "reasoning"],
        specialties=["reasoning"],
        quality_rating=50,
    )
    stronger = make_model(
        "cloud:stronger",
        capabilities=["chat", "reasoning"],
        quality_rating=79,
        location="cloud",
        transport="remote",
    )
    plan = RoutePlanner().plan(
        _request("quality", intent="reasoning"), [specialist, stronger]
    )
    assert plan.model_id == stronger.id


def test_degrades_to_scaffold_when_no_capability():
    scaffold = make_model(
        "local:scaffold",
        capabilities=["chat"],
        provider="quorum",
        transport="in_process",
        quality_rating=5,
    )
    plan = RoutePlanner().plan(_request("offline"), [scaffold])
    assert plan.model_id == scaffold.id
    assert plan.degraded is True
    assert "no model with every required capability" in plan.rationale
