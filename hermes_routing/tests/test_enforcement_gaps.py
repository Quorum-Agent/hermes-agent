"""Regression tests for policy promises that previously failed open."""

from __future__ import annotations

import pytest

from hermes_routing.compiler import RequestCompiler
from hermes_routing.events import DeltaEvent, ErrorEvent, ResultEvent
from hermes_routing.orchestrator import Orchestrator
from hermes_routing.planner import RoutePlanner
from hermes_routing.policies import UnsupportedPolicyError
from hermes_routing.types import ChatMessage, ChatRequest, ModelDescriptor


def _request(content: str, policy: str = "balanced") -> ChatRequest:
    return ChatRequest(
        conversation_id="conversation-1",
        policy=policy,
        messages=[
            ChatMessage(
                id="message-1",
                role="user",
                content=content,
                created_at="2026-01-01T00:00:00Z",
            )
        ],
    )


def _model(model_id: str = "local:test", location: str = "local") -> ModelDescriptor:
    return ModelDescriptor(
        id=model_id,
        label=model_id,
        provider="test",
        location=location,
        transport="loopback" if location == "local" else "remote",
        capabilities=["chat", "reasoning", "web"],
        context_window=8192,
        quality_rating=50,
        available=True,
    )


class _Provider:
    def __init__(self, model: ModelDescriptor, answer: str):
        self.model = model
        self.answer = answer
        self.calls = 0

    async def stream(self, _input):
        self.calls += 1
        yield self.answer


async def _collect(orchestrator: Orchestrator, request: ChatRequest) -> list:
    return [event async for event in orchestrator.run(request)]


def test_cost_controlled_planning_is_rejected_until_atomic_ledger_exists():
    compiled = RequestCompiler().compile(_request("Explain this.", "cost_controlled"))

    with pytest.raises(UnsupportedPolicyError, match="atomic usage ledger"):
        RoutePlanner().plan(compiled, [_model()])


@pytest.mark.asyncio
async def test_cost_controlled_rejected_before_model_dispatch():
    provider = _Provider(_model(), "<quorum-final>must not run</quorum-final>")
    events = await _collect(
        Orchestrator([provider]),
        _request("Explain this.", "cost_controlled"),
    )

    assert provider.calls == 0
    assert not any(isinstance(event, ResultEvent) for event in events)
    assert any(
        isinstance(event, ErrorEvent) and "atomic usage ledger" in event.message
        for event in events
    )


@pytest.mark.asyncio
async def test_invalid_model_output_never_falls_back_to_raw_content():
    provider = _Provider(_model(), "private chain of thought without an envelope")
    events = await _collect(Orchestrator([provider]), _request("Explain this."))

    assert not any(isinstance(event, DeltaEvent) for event in events)
    assert not any(isinstance(event, ResultEvent) for event in events)
    assert any(
        isinstance(event, ErrorEvent) and "No safe fallback route" in event.message
        for event in events
    )


@pytest.mark.asyncio
async def test_default_compiler_applies_full_sensitive_data_floor():
    cloud = _Provider(
        _model("cloud:test", "cloud"),
        "<quorum-final>must not run</quorum-final>",
    )
    events = await _collect(
        Orchestrator([cloud]),
        _request("email: alice@example.com"),
    )

    assert cloud.calls == 0
    assert any(isinstance(event, ErrorEvent) for event in events)
    assert not any(isinstance(event, ResultEvent) for event in events)
