"""Orchestration events — Python mirror of the TS OrchestrationEvent union.

The spike (spikes/002-orchestration-events-in-loop/events.py) validated the
contract; this is the production port using the shared TaskPlan/Trace types.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .types import ExecutionTrace, TaskPlan


def _id() -> str:
    return str(uuid.uuid4())


@dataclass
class PlanEvent:
    type: str = "plan"
    plan: TaskPlan | None = None


@dataclass
class TraceEvent:
    type: str = "trace"
    trace: ExecutionTrace | None = None


@dataclass
class DeltaEvent:
    type: str = "delta"
    content: str = ""


@dataclass
class ResultEvent:
    type: str = "result"
    result: Any = None  # ChatResult


@dataclass
class ErrorEvent:
    type: str = "error"
    message: str = ""
    recoverable: bool = False
    plan: TaskPlan | None = None
    partial_content: str | None = None
    execution_message: Any = None  # ChatMessage


# Discriminated union via .type + payload, matching the TS union.
Event = Any


def now() -> float:
    return time.time()
