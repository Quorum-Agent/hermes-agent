"""Core types — Python port of packages/core/src/types.ts.

Port fidelity note: the TS module carries a large amount of design history in
comments. Those are preserved here in condensed form where they explain a
*behavioural* decision (why a field is a ceiling, why a comparison is an
equality, why a function is the single source of truth). Pure prose history is
dropped. The behavioural surface is 1:1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

Id = str

MessageRole = Literal["user", "assistant", "system", "tool"]

PolicyMode = Literal["private", "balanced", "quality", "offline", "cost_controlled"]

ResponseVerbosity = Literal["concise", "standard", "detailed"]

# Ordered nearest-to-furthest. Index is the tier; compare, do not equate.
EXECUTION_LOCATIONS: list[str] = [
    "device",
    "local",
    "network",
    "remote",
    "web",
    "cloud",
]

ExecutionLocation = Literal["device", "local", "network", "remote", "web", "cloud"]

# Model locations exclude device and web: a model runs somewhere specific.
ModelLocation = Literal["local", "network", "remote", "cloud"]
ModelTransport = Literal["in_process", "loopback", "remote"]

Capability = Literal[
    "chat", "reasoning", "coding", "vision", "documents", "web", "tools"
]

LocalModelRole = Literal["general", "coding", "reasoning"]

OrchestrationMode = Literal["route", "relay"]

RequestIntent = Literal[
    "conversation", "reasoning", "coding", "document", "vision", "research"
]

SensitiveDataCategory = Literal[
    "credentials",
    "private_key",
    "financial",
    "government_id",
    "personal_contact",
    "health",
    "confidential",
]

# Where Quorum's web-search tool runs. web rather than cloud deliberately: a
# search provider receives a query string, not the whole conversation.
WEB_SEARCH_LOCATION: str = "web"


def location_tier(location: str) -> int:
    return EXECUTION_LOCATIONS.index(location)


def leaves_device(location: str) -> bool:
    """Whether execution here puts conversation content off the device.

    The single source of truth for that question — previously written inline
    as `location == "cloud"` at five sites, each a separate chance to be wrong.
    """
    return location_tier(location) > location_tier("local")


def policy_permits_tool(policy, tool_location: str) -> bool:
    """Whether a policy permits a tool that runs at `tool_location`.

    `"none"` is not a tier and cannot be compared, so it is answered first.
    """
    if policy.tool_ceiling == "none":
        return False
    return location_tier(tool_location) <= location_tier(policy.tool_ceiling)


def policy_reaches_off_device(policy) -> bool:
    """Whether anything a policy permits can put content off the device, on
    either axis — inference OR tools (a search tool reaching the internet puts
    content off it even if every model stays local)."""
    return leaves_device(policy.inference_ceiling) or (
        policy.tool_ceiling != "none" and leaves_device(policy.tool_ceiling)
    )


def model_reach(model) -> str:
    """How far a model actually reaches, which is not always what it declares.

    `in_process` only downgrades a model that ALSO declares itself local. Where
    the two fields contradict, the safe reading is the FURTHER of the two —
    this can only ever return the declared location or something nearer.
    """
    return (
        "device"
        if (model.transport == "in_process" and model.location == "local")
        else model.location
    )


@dataclass
class MessageExecutionRecord:
    plan: "TaskPlan"
    traces: list["ExecutionTrace"]
    started_at: float
    completed_at: float
    status: str | None = None  # running|completed|failed|cancelled


@dataclass
class ChatMessage:
    id: Id
    role: MessageRole
    content: str
    created_at: str
    provenance: str | None = None  # web_grounded|hub_synthesized
    execution: MessageExecutionRecord | None = None


@dataclass
class ModelInferenceSettings:
    reasoning_effort: str | None = None  # none|low|medium|high
    max_output_tokens: int | None = None


@dataclass
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    measured: bool


@dataclass
class ModelDescriptor:
    id: Id
    label: str
    provider: str
    role: LocalModelRole | None = None
    location: ModelLocation = "local"
    transport: ModelTransport = "loopback"
    capabilities: list[Capability] = field(default_factory=list)
    context_window: int = 0
    quality_rating: int = 0
    specialties: list[Capability] = field(default_factory=list)
    inference: ModelInferenceSettings | None = None
    available: bool = False
    cost_per_million_tokens: float | None = None


@dataclass
class LocalModelRoleStatus:
    role: LocalModelRole
    configured_model: str
    model_id: Id | None = None
    required: bool = False
    available: bool = False


@dataclass
class LocalRuntimeProblem:
    summary: str
    detail: str | None = None


@dataclass
class PromptAnalyzerResult:
    intent: RequestIntent
    confidence: float
    task_summary: str


@dataclass
class RequestRequirements:
    intent: RequestIntent
    intent_confidence: float
    intent_source: str  # current|conversation|default|classifier
    capabilities: list[Capability]
    requires_freshness: bool = False
    contains_sensitive_data: bool = False
    sensitive_data_categories: list[SensitiveDataCategory] = field(default_factory=list)
    contains_web_grounded_data: bool = False


@dataclass
class RequestAnalysis:
    source: str  # heuristic|local_model|hybrid
    intent: RequestIntent
    confidence: float
    task_summary: str
    analyzer: "AnalyzerRef | None" = None  # modelId/modelLabel/intent/confidence


@dataclass
class AnalyzerRef:
    model_id: Id
    model_label: str
    intent: RequestIntent
    confidence: float


@dataclass
class CompiledRequest:
    id: Id
    conversation_id: Id
    messages: list[ChatMessage]
    prompt: str
    policy: PolicyMode
    verbosity: ResponseVerbosity
    analysis: RequestAnalysis
    requirements: RequestRequirements


@dataclass
class PolicyDefinition:
    id: PolicyMode
    label: str
    intent: str
    inference_ceiling: str
    tool_ceiling: Union[str, Literal["none"]]  # ExecutionLocation | "none"
    prefer_local: bool
    description: str = ""
    cloud_budget_usd: float | None = None


@dataclass
class PlanStep:
    id: Id
    label: str
    kind: str  # compile|classification|policy|retrieval|model|synthesis
    location: str
    model_id: Id | None = None


@dataclass
class ExecutionAttempt:
    model_id: Id
    route: str
    status: str  # completed|failed
    context_may_have_been_transmitted: bool
    stage: str | None = None  # draft|synthesis
    detail: str | None = None


@dataclass
class WebSearchSource:
    title: str
    url: str
    published_at: str | None = None


@dataclass
class WebSearchAttempt:
    provider: str
    status: str  # running|completed|failed
    detail: str | None = None


@dataclass
class TaskPlan:
    id: Id
    request_id: Id
    policy: PolicyMode
    verbosity: ResponseVerbosity
    analysis: RequestAnalysis
    route: str
    model_id: Id
    rationale: str
    steps: list[PlanStep]
    spoke_model_id: Id | None = None
    synthesis_degraded: bool | None = None
    degraded: bool | None = None
    fallback_from_model_id: Id | None = None
    attempts: list[ExecutionAttempt] | None = None
    cloud_disclosure: str | None = None
    safety: dict | None = None
    web_search: dict | None = None


TraceStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class ExecutionTrace:
    id: Id
    request_id: Id
    step_id: Id
    label: str
    kind: str
    location: str
    status: TraceStatus
    detail: str | None = None
    model_id: Id | None = None
    started_at: str = ""
    completed_at: str | None = None


@dataclass
class ChatRequest:
    conversation_id: Id
    messages: list[ChatMessage]
    policy: PolicyMode
    verbosity: ResponseVerbosity | None = None


@dataclass
class ChatResult:
    request_id: Id
    conversation_id: Id
    message: ChatMessage
    plan: TaskPlan


OrchestrationEvent = object  # typed structurally in events.py


@dataclass
class RuntimeToolDescriptor:
    id: Id
    label: str
    capabilities: list[Capability]
    location: str
    available: bool
    context_may_leave_device: bool


@dataclass
class WebSearchResult:
    title: str
    url: str
    snippet: str
    published_at: str | None = None


@dataclass
class WebSearchResponse:
    query: str
    results: list[WebSearchResult]
    provider: str | None = None
    attempts: list[WebSearchAttempt] | None = None
