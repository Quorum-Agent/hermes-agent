"""Tests for orchestrator.py — translated from orchestrator.test.ts key cases.

pytest-asyncio 1.3.0 is available; mark async tests @pytest.mark.asyncio.
"""

from __future__ import annotations

import copy
import re

import pytest

from hermes_routing.types import (
    ChatMessage,
    ChatRequest,
    ChatResult,
    ExecutionAttempt,
    ModelDescriptor,
    RuntimeToolDescriptor,
    WebSearchResult,
    WebSearchResponse,
    leaves_device,
    location_tier,
)
from hermes_routing.events import (
    DeltaEvent,
    ErrorEvent,
    PlanEvent,
    ResultEvent,
    TraceEvent,
)
from hermes_routing.orchestrator import (
    MAX_FAILED_DRAFTS,
    CIRCUIT_FAILURE_THRESHOLD,
    ModelExecutionError,
    Orchestrator,
)
from hermes_routing.compiler import RequestCompiler, detect_sensitive_content
from hermes_routing.planner import RoutePlanner
from hermes_routing.policies import get_policy


# ── Test helpers ────────────────────────────────────────────────────


def _chat_request(content: str, policy: str = "balanced") -> ChatRequest:
    return ChatRequest(
        conversation_id="conversation-1",
        policy=policy,
        messages=[
            ChatMessage(
                id="message-1",
                role="user",
                content=content,
                created_at="1970-01-01T00:00:00.000Z",
            )
        ],
    )


def _make_model(
    id_: str,
    capabilities: tuple = ("chat",),
    location: str = "local",
    transport: str = "loopback",
    provider: str = "test",
    quality_rating: int = 50,
    context_window: int = 16384,
    available: bool = True,
    specialties: tuple = (),
    role: str | None = None,
) -> ModelDescriptor:
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


_GENERAL_MODEL = _make_model(
    "local:general:test", capabilities=("chat", "coding", "reasoning")
)
_CODING_MODEL = _make_model(
    "local:coding:test",
    capabilities=("chat", "coding"),
    quality_rating=50,
    specialties=("coding",),
    role="coding",
)
_CLOUD_MODEL = _make_model(
    "cloud:test",
    capabilities=("chat", "coding", "reasoning"),
    location="cloud",
    transport="remote",
    quality_rating=90,
)


def _provider(model: ModelDescriptor, stream_fn):
    """Create a provider dict with .model and async generator .stream()."""

    class Provider:
        def __init__(self, model, stream_fn):
            self.model = model
            self._stream_fn = stream_fn

        async def stream(self, input):
            async for chunk in self._stream_fn(input):
                yield chunk

    return Provider(model, stream_fn)


async def _answer(content: str):
    """Async generator that yields one valid public-answer envelope."""
    yield f"<quorum-final>{content}</quorum-final>"


async def _fail_before_output():
    """Async generator that raises immediately."""
    raise Exception("provider unavailable")
    yield  # unreachable, makes it an async generator


async def _collect(
    orchestrator: Orchestrator, request: ChatRequest, signal=None
) -> list:
    """Collect all events from an orchestrator run."""
    events = []
    async for event in orchestrator.run(request, signal):
        events.append(event)
    return events


# ── Basic orchestration contract ────────────────────────────────────


class TestOrchestratorBasic:
    """Plan / trace / delta / result / error event contract."""

    @pytest.mark.asyncio
    async def test_plan_emitted_first(self):
        """The first event is always a plan."""
        orch = Orchestrator([_provider(_GENERAL_MODEL, lambda _: _answer("hello"))])
        events = await _collect(orch, _chat_request("Hello."))
        assert len(events) > 0
        assert events[0].type == "plan"

    @pytest.mark.asyncio
    async def test_trace_lifecycle_compile_policy_model(self):
        """Traces for compile, policy, and model steps are emitted."""
        orch = Orchestrator([_provider(_GENERAL_MODEL, lambda _: _answer("hello"))])
        events = await _collect(orch, _chat_request("Hello."))
        traces = [e for e in events if e.type == "trace"]
        kinds = {t.trace.kind for t in traces if t.trace}
        assert "compile" in kinds
        assert "policy" in kinds
        assert "model" in kinds

    @pytest.mark.asyncio
    async def test_delta_emitted_for_answer(self):
        """Only the validated answer is emitted, never raw envelope chunks."""

        async def stream_fn(_input):
            yield "<quorum-final>part1"
            yield "part2</quorum-final>"

        orch = Orchestrator([_provider(_GENERAL_MODEL, stream_fn)])
        events = await _collect(orch, _chat_request("Hello."))
        deltas = [e for e in events if e.type == "delta"]
        assert [event.content for event in deltas] == ["part1part2"]

    @pytest.mark.asyncio
    async def test_result_with_plan_and_message(self):
        """Result event carries plan and message."""
        orch = Orchestrator([
            _provider(_GENERAL_MODEL, lambda _: _answer("the response"))
        ])
        events = await _collect(orch, _chat_request("Hello."))
        results = [e for e in events if e.type == "result"]
        assert len(results) == 1
        result = results[0]
        assert result.result is not None
        assert result.result.message is not None
        assert result.result.plan is not None

    @pytest.mark.asyncio
    async def test_error_on_all_failed(self):
        """When all models fail before output, error is yielded."""
        orch = Orchestrator([
            _provider(_CODING_MODEL, lambda _: _fail_before_output()),
        ])
        events = await _collect(orch, _chat_request("Write code."))
        errors = [e for e in events if e.type == "error"]
        assert len(errors) >= 1
        assert any("No safe fallback" in e.message for e in errors)

    @pytest.mark.asyncio
    async def test_append_only_attempts_ledger(self):
        """Attempts ledger records each attempt in order."""
        orch = Orchestrator([
            _provider(_CODING_MODEL, lambda _: _fail_before_output()),
            _provider(_GENERAL_MODEL, lambda _: _answer("fallback")),
        ])
        events = await _collect(orch, _chat_request("Write code."))
        result = next(e for e in events if e.type == "result")
        attempts = result.result.plan.attempts
        assert len(attempts) == 2
        assert attempts[0].model_id == _CODING_MODEL.id
        assert attempts[0].status == "failed"
        assert attempts[1].model_id == _GENERAL_MODEL.id
        assert attempts[1].status == "completed"

    @pytest.mark.asyncio
    async def test_request_kind_error_no_fallback_to_scaffold(self):
        """A request-level error (kind='request') does not fall back."""

        async def queue_fail(_input):
            raise ModelExecutionError(
                "Local inference queue wait exceeded 50000ms.",
                "request",
            )
            yield

        orch = Orchestrator([
            _provider(_CODING_MODEL, queue_fail),
            _provider(_GENERAL_MODEL, lambda _: _answer("should not appear")),
        ])
        events = await _collect(orch, _chat_request("Write code."))
        assert not any(e.type == "result" for e in events)
        errors = [e for e in events if e.type == "error"]
        assert any("Local inference queue" in e.message for e in errors)


# ── Cloud disclosure ────────────────────────────────────────────────


class TestCloudDisclosure:
    @pytest.mark.asyncio
    async def test_cloud_disclosure_when_cloud_used(self):
        """Cloud disclosure is present when a cloud model is used."""
        orch = Orchestrator([
            _provider(_CLOUD_MODEL, lambda _: _answer("cloud response")),
            _provider(_GENERAL_MODEL, lambda _: _answer("local")),
        ])
        events = await _collect(orch, _chat_request("Hello.", policy="quality"))
        result = next(e for e in events if e.type == "result")
        plan = result.result.plan
        assert plan.cloud_disclosure is not None
        assert "leave this device" in plan.cloud_disclosure.lower()

    @pytest.mark.asyncio
    async def test_no_disclosure_when_local(self):
        """No cloud disclosure when route stays local."""
        orch = Orchestrator([
            _provider(_GENERAL_MODEL, lambda _: _answer("local")),
        ])
        events = await _collect(orch, _chat_request("Hello.", policy="private"))
        result = next(e for e in events if e.type == "result")
        assert result.result.plan.cloud_disclosure is None


# ── Web search Q-01: grounding taint ────────────────────────────────


class TestWebSearchQ01:
    @pytest.mark.asyncio
    async def test_web_grounded_taint_excludes_cloud(self):
        """Q-01: Freshly retrieved web data must not reach a cloud model."""
        cloud_called = False

        async def cloud_stream(_input):
            nonlocal cloud_called
            cloud_called = True
            yield "<quorum-final>cloud must not receive retrieved data</quorum-final>"

        ws_tool = RuntimeToolDescriptor(
            id="web-search:test",
            label="Test Search",
            capabilities=["web"],
            location="web",
            available=True,
            context_may_leave_device=True,
        )

        class WebSearch:
            tool = ws_tool

            async def search(self, query, signal=None):
                return WebSearchResponse(
                    query=query,
                    provider="Test Search",
                    results=[
                        WebSearchResult(
                            title="Current source",
                            url="https://example.com/current",
                            snippet="The current release is 2.0.",
                        )
                    ],
                )

        received_input = {}

        async def general_stream(input):
            received_input["messages"] = [m for m in input["messages"]]
            received_input["runtime_tools"] = input.get("runtimeTools", [])
            yield "<quorum-final>The current release is 2.0 [1].</quorum-final>"

        orch = Orchestrator(
            [
                _provider(_GENERAL_MODEL, general_stream),
                _provider(_CLOUD_MODEL, cloud_stream),
            ],
            web_search=WebSearch(),
        )

        events = await _collect(
            orch, _chat_request("Research the latest Quorum release.", policy="quality")
        )
        result = next(e for e in events if e.type == "result")

        # Cloud model must NOT have been called
        assert cloud_called is False

        # Route must stay local
        assert result.result.plan.route == "local"

        # Web search sources must be in the plan
        assert result.result.plan.web_search is not None
        assert len(result.result.plan.web_search["sources"]) == 1

        # Message provenance must be web_grounded
        assert result.result.message.provenance == "web_grounded"

        # Retrieved sources must appear in the tool message
        last_tool_msg = [
            m for m in received_input.get("messages", []) if m.role == "tool"
        ]
        assert len(last_tool_msg) >= 1
        assert "https://example.com/current" in last_tool_msg[-1].content


# ── Bidi / zero-width strip Q-03 ────────────────────────────────────


class TestBidiStrip:
    @pytest.mark.asyncio
    async def test_strips_bidi_from_displayed_sources(self):
        """Q-03: Bidi and zero-width chars are stripped from source titles."""
        ws_tool = RuntimeToolDescriptor(
            id="web-search:test",
            label="Test Search",
            capabilities=["web"],
            location="web",
            available=True,
            context_may_leave_device=True,
        )

        class WebSearch:
            tool = ws_tool

            async def search(self, query, signal=None):
                return WebSearchResponse(
                    query=query,
                    results=[
                        WebSearchResult(
                            title="Trusted\u200b\u202egpj.exe\u2066",
                            url="https://example.com/current",
                            snippet="Current information.",
                        )
                    ],
                )

        async def gen_stream(_input):
            yield "<quorum-final>Grounded answer [1].</quorum-final>"

        orch = Orchestrator(
            [_provider(_GENERAL_MODEL, gen_stream)],
            web_search=WebSearch(),
        )

        events = await _collect(
            orch, _chat_request("Research the latest Quorum release.")
        )
        result = next(e for e in events if e.type == "result")

        sources = result.result.plan.web_search["sources"]
        assert sources[0]["title"] == "Trusted gpj.exe"

        # The bidi-reversed filename must NOT appear in the answer
        assert "gpj.exe" not in result.result.message.content

        # Raw bidi/zero-width chars must not appear in the JSON-serialized result
        serialized = str(result.result)
        assert "\u200b" not in serialized
        assert "\u202e" not in serialized
        assert "\u2066" not in serialized


# ── Current-turn query bound Q-07 ───────────────────────────────────


class TestQueryBound:
    @pytest.mark.asyncio
    async def test_bounded_current_turn_search_query(self):
        """Q-07: Search query is bounded to current turn only, 500 chars."""
        searched_query = ""

        ws_tool = RuntimeToolDescriptor(
            id="web-search:test",
            label="Test Search",
            capabilities=["web"],
            location="web",
            available=True,
            context_may_leave_device=True,
        )

        class WebSearch:
            tool = ws_tool

            async def search(self, query, signal=None):
                nonlocal searched_query
                searched_query = query
                return WebSearchResponse(
                    query=query,
                    results=[
                        WebSearchResult(
                            title="Result",
                            url="https://example.com",
                            snippet="data",
                        )
                    ],
                )

        orch = Orchestrator(
            [_provider(_GENERAL_MODEL, lambda _: _answer("answer [1]."))],
            web_search=WebSearch(),
        )

        await _collect(
            orch,
            ChatRequest(
                conversation_id="conversation-1",
                policy="balanced",
                messages=[
                    ChatMessage(
                        id="prior-user",
                        role="user",
                        content="Search the web for PostgreSQL releases.",
                        created_at="1970-01-01T00:00:00.000Z",
                    ),
                    ChatMessage(
                        id="current-user",
                        role="user",
                        content="What's the latest?",
                        created_at="1970-01-01T00:00:01.000Z",
                    ),
                ],
            ),
        )

        # Q-07: only the LAST user message becomes the query
        assert searched_query == "What's the latest?"

    @pytest.mark.asyncio
    async def test_query_truncated_to_500_chars(self):
        """Search query longer than 500 chars is truncated."""
        searched_query = ""

        ws_tool = RuntimeToolDescriptor(
            id="web-search:test",
            label="Test Search",
            capabilities=["web"],
            location="web",
            available=True,
            context_may_leave_device=True,
        )

        class WebSearch:
            tool = ws_tool

            async def search(self, query, signal=None):
                nonlocal searched_query
                searched_query = query
                return WebSearchResponse(
                    query=query,
                    results=[
                        WebSearchResult(title="R", url="https://x.com", snippet="d")
                    ],
                )

        orch = Orchestrator(
            [_provider(_GENERAL_MODEL, lambda _: _answer("ok."))],
            web_search=WebSearch(),
        )

        long_msg = "A" * 600
        await _collect(orch, _chat_request(long_msg))
        assert len(searched_query) <= 500


# ── Fallback tier constraint ────────────────────────────────────────


class TestFallbackTier:
    @pytest.mark.asyncio
    async def test_fallback_never_escalates_beyond_failed_attempt(self):
        """Fallback must not go further out than the failed attempt's tier."""
        orch = Orchestrator([
            _provider(_CODING_MODEL, lambda _: _fail_before_output()),
            _provider(_CLOUD_MODEL, lambda _: _answer("cloud response")),
        ])
        events = await _collect(orch, _chat_request("Write a TypeScript function."))
        plans = [e for e in events if e.type == "plan"]
        # Cloud model should never appear in plans
        for p in plans:
            assert p.plan.model_id != _CLOUD_MODEL.id
        # When no safe fallback exists, we get an error, not a cloud escalation
        errors = [e for e in events if e.type == "error"]
        assert any("No safe fallback" in e.message for e in errors)

    @pytest.mark.asyncio
    async def test_retains_failed_cloud_in_local_fallback(self):
        """Failed cloud attempt is recorded with contextMayHaveBeenTransmitted."""
        orch = Orchestrator([
            _provider(_CLOUD_MODEL, lambda _: _fail_before_output()),
            _provider(_GENERAL_MODEL, lambda _: _answer("local response")),
        ])
        events = await _collect(orch, _chat_request("Hello.", policy="quality"))
        result = next(e for e in events if e.type == "result")
        plan = result.result.plan
        assert plan.route == "local"
        assert plan.attempts[0].model_id == _CLOUD_MODEL.id
        assert plan.attempts[0].context_may_have_been_transmitted is True
        assert plan.attempts[1].model_id == _GENERAL_MODEL.id


# ── Policy blocks search ────────────────────────────────────────────


class TestPolicyBlocksSearch:
    @pytest.mark.asyncio
    async def test_private_mode_blocks_web_search(self):
        """Private mode blocks web search before contacting provider."""
        search_calls = 0

        ws_tool = RuntimeToolDescriptor(
            id="web-search:test",
            label="Test Search",
            capabilities=["web"],
            location="web",
            available=True,
            context_may_leave_device=True,
        )

        class WebSearch:
            tool = ws_tool

            async def search(self, query, signal=None):
                nonlocal search_calls
                search_calls += 1
                return WebSearchResponse(query="unused", results=[])

        orch = Orchestrator(
            [_provider(_GENERAL_MODEL, lambda _: _answer("must not run"))],
            web_search=WebSearch(),
        )

        events = await _collect(
            orch, _chat_request("Research the latest Quorum release.", policy="private")
        )

        assert search_calls == 0
        errors = [e for e in events if e.type == "error"]
        assert any(
            "Private" in e.message and "blocks web search" in e.message for e in errors
        )
        assert not any(e.type == "plan" for e in events)

    @pytest.mark.asyncio
    async def test_unconfigured_search_yields_error(self):
        """No web search provider configured yields clear error."""
        orch = Orchestrator([
            _provider(_GENERAL_MODEL, lambda _: _answer("must not run")),
        ])
        events = await _collect(
            orch, _chat_request("Research the latest Quorum release.")
        )
        errors = [e for e in events if e.type == "error"]
        assert any("no web-search provider" in e.message.lower() for e in errors)
        assert not any(e.type == "plan" for e in events)

    @pytest.mark.asyncio
    async def test_sensitive_search_text_blocked(self):
        """Sensitive content in search query is blocked before leaving device."""
        search_calls = 0

        ws_tool = RuntimeToolDescriptor(
            id="web-search:test",
            label="Test Search",
            capabilities=["web"],
            location="web",
            available=True,
            context_may_leave_device=True,
        )

        class WebSearch:
            tool = ws_tool

            async def search(self, query, signal=None):
                nonlocal search_calls
                search_calls += 1
                return WebSearchResponse(query="unused", results=[])

        orch = Orchestrator(
            [_provider(_GENERAL_MODEL, lambda _: _answer("must not run"))],
            web_search=WebSearch(),
        )

        events = await _collect(
            orch,
            _chat_request(
                "Search the web for the latest breach involving SSN 123-45-6789."
            ),
        )

        assert search_calls == 0
        errors = [e for e in events if e.type == "error"]
        assert any("privacy guard" in e.message for e in errors)
        assert any("government_id" in e.message for e in errors)


# ── Circuit breaker ─────────────────────────────────────────────────


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_opens_circuit_after_repeated_provider_failures(self):
        """Circuit opens after CIRCUIT_FAILURE_THRESHOLD provider failures."""
        orch = Orchestrator([
            _provider(_CODING_MODEL, lambda _: _fail_before_output()),
            _provider(_GENERAL_MODEL, lambda _: _answer("fallback")),
        ])
        req = _chat_request("Write a TypeScript function.")

        for _ in range(3):
            await _collect(orch, req)

        # coding model should be marked unavailable
        coding = next((m for m in orch.models if m.id == _CODING_MODEL.id), None)
        assert coding is not None
        assert coding.available is False

    @pytest.mark.asyncio
    async def test_does_not_open_for_unsafe_output(self):
        """Empty answer (unsafe_output) does not open circuit."""

        async def empty_output(_input):
            # yields nothing — treated as unsafe_output
            return
            yield

        orch = Orchestrator([
            _provider(_CODING_MODEL, empty_output),
            _provider(_GENERAL_MODEL, lambda _: _answer("fallback")),
        ])
        req = _chat_request("Write code.")

        for _ in range(3):
            await _collect(orch, req)

        coding = next((m for m in orch.models if m.id == _CODING_MODEL.id), None)
        assert coding.available is True

    @pytest.mark.asyncio
    async def test_does_not_open_for_request_kind_failure(self):
        """Request-level failures don't open circuits."""

        async def request_fail(_input):
            raise ModelExecutionError("context too large", "request")
            yield

        orch = Orchestrator([
            _provider(_CODING_MODEL, request_fail),
            _provider(_GENERAL_MODEL, lambda _: _answer("fallback")),
        ])
        req = _chat_request("Write code.")

        for _ in range(3):
            await _collect(orch, req)

        coding = next((m for m in orch.models if m.id == _CODING_MODEL.id), None)
        assert coding.available is True


# ── Fallback stops after output ─────────────────────────────────────


class TestBufferedFallback:
    @pytest.mark.asyncio
    async def test_partial_unvalidated_output_is_replaced_without_splice(self):
        """Interrupted raw output is withheld, then replaced by a safe fallback."""

        async def partial_fail(_input):
            yield "partial"
            raise Exception("stream interrupted")

        orch = Orchestrator([
            _provider(_CODING_MODEL, partial_fail),
            _provider(_GENERAL_MODEL, lambda _: _answer("should not appear")),
        ])
        events = await _collect(orch, _chat_request("Write code."))
        result = next(e.result for e in events if e.type == "result")
        assert result.message.content == "should not appear"
        assert [e.content for e in events if e.type == "delta"] == ["should not appear"]
        assert "partial" not in [e.content for e in events if e.type == "delta"]

    @pytest.mark.asyncio
    async def test_cancellation_reported_cleanly(self):
        """Cancellation produces clean error with partial content."""

        async def slow_respond(_input):
            raise ModelExecutionError("stopped", "cancelled")
            yield

        orch = Orchestrator([
            _provider(_GENERAL_MODEL, slow_respond),
        ])
        events = await _collect(orch, _chat_request("Hello."))
        errors = [e for e in events if e.type == "error"]
        assert len(errors) >= 1


# ── Drop sensitive search results ───────────────────────────────────


class TestDropSensitiveResults:
    @pytest.mark.asyncio
    async def test_drops_sensitive_results_retains_plan(self):
        """Search results with credentials are filtered out."""
        ws_tool = RuntimeToolDescriptor(
            id="web-search:test",
            label="Test Search",
            capabilities=["web"],
            location="web",
            available=True,
            context_may_leave_device=True,
        )

        class WebSearch:
            tool = ws_tool

            async def search(self, query, signal=None):
                return WebSearchResponse(
                    query=query,
                    results=[
                        WebSearchResult(
                            title="Leaked credential",
                            url="https://example.com/leak",
                            snippet="Use API key sk-abc123def4567890.",
                        ),
                    ],
                )

        orch = Orchestrator(
            [_provider(_GENERAL_MODEL, lambda _: _answer("must not run"))],
            web_search=WebSearch(),
        )

        events = await _collect(
            orch, _chat_request("Research the latest Quorum release.")
        )
        errors = [e for e in events if e.type == "error"]
        assert any("no usable" in e.message.lower() for e in errors)
        assert not any(e.type == "result" for e in events)


# ── Sensitive content helpers ───────────────────────────────────────


class TestSensitiveContent:
    def test_detects_ssn(self):
        assert detect_sensitive_content("SSN 123-45-6789")

    def test_detects_sk_key(self):
        assert detect_sensitive_content("sk-abc123def456789012345678")

    def test_clean_text_ok(self):
        assert detect_sensitive_content("Hello world") == []


# ── Compilation ─────────────────────────────────────────────────────


class TestCompilation:
    def test_detects_research_intent(self):
        req = _chat_request("Research the latest Quorum release.")
        comp = RequestCompiler().compile(req)
        assert "web" in comp.requirements.capabilities
        assert comp.requirements.intent == "research"

    def test_detects_coding_intent(self):
        req = _chat_request("Write a TypeScript function.")
        comp = RequestCompiler().compile(req)
        assert "coding" in comp.requirements.capabilities

    def test_simple_conversation(self):
        req = _chat_request("Hello.")
        comp = RequestCompiler().compile(req)
        assert comp.requirements.intent == "conversation"
