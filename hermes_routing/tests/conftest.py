"""Shared test helpers for hermes_routing."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the package root is importable regardless of how pytest is invoked.
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from hermes_routing.types import (  # noqa: E402
    ChatMessage,
    CompiledRequest,
    ModelDescriptor,
    RequestAnalysis,
    RequestRequirements,
    ResponseVerbosity,
    TaskPlan,
)
from hermes_routing import get_policy  # noqa: E402


def make_message(role, content, provenance=None, execution=None, created_at=None):
    return ChatMessage(
        id=f"{role}-{abs(hash(content))}",
        role=role,
        content=content,
        created_at=created_at or "2026-08-01T00:00:00Z",
        provenance=provenance,
        execution=execution,
    )


def make_model(
    id_,
    capabilities=("chat",),
    location="local",
    transport="loopback",
    provider="quorum",
    quality_rating=50,
    context_window=8000,
    available=True,
    specialties=(),
    role=None,
):
    return ModelDescriptor(
        id=id_,
        label=id_,
        provider=provider,
        role=role,
        location=location,
        transport=transport,
        capabilities=list(capabilities),
        context_window=context_window,
        quality_rating=quality_rating,
        available=available,
        specialties=list(specialties),
    )


def make_compiled_request(
    policy="balanced",
    verbosity="standard",
    messages=None,
    intent="conversation",
    confidence=0.5,
    intent_source="current",
    requires_freshness=False,
):
    msgs = messages or [make_message("user", "hello")]
    requirements = RequestRequirements(
        intent=intent,
        intent_confidence=confidence,
        intent_source=intent_source,
        capabilities=["chat"],
        requires_freshness=requires_freshness,
    )
    return CompiledRequest(
        id="req-1",
        conversation_id="conv-1",
        messages=msgs,
        prompt=msgs[-1].content if msgs else "",
        policy=policy,
        verbosity="standard",
        analysis=RequestAnalysis(
            source="heuristic",
            intent=intent,
            confidence=confidence,
            task_summary="task",
        ),
        requirements=requirements,
    )
