"""Tests for hermes_routing.compiler — ported from request-compiler.test.ts."""

from __future__ import annotations

import pytest

from hermes_routing.compiler import (
    RequestCompiler,
    contains_sensitive_content,
    detect_sensitive_content,
)
from hermes_routing.types import (
    ChatMessage,
    ChatRequest,
    CompiledRequest,
    MessageExecutionRecord,
    PromptAnalyzerResult,
    RequestAnalysis,
    RequestIntent,
    RequestRequirements,
    TaskPlan,
)


# ---------------------------------------------------------------------------
# Helpers — mirror the TS test helpers
# ---------------------------------------------------------------------------


def _request(content: str) -> ChatRequest:
    return ChatRequest(
        conversation_id="conversation-1",
        policy="balanced",
        messages=[
            ChatMessage(
                id="message-1",
                role="user",
                content=content,
                created_at="1970-01-01T00:00:00.000Z",
            )
        ],
    )


def _conversation_request(contents: list[str]) -> ChatRequest:
    msgs: list[ChatMessage] = []
    for idx, content in enumerate(contents):
        msgs.append(
            ChatMessage(
                id=f"message-{idx}",
                role="user",
                content=content,
                created_at=f"1970-01-01T00:00:0{idx}.000Z",
            )
        )
    return ChatRequest(
        conversation_id="conversation-1",
        policy="balanced",
        messages=msgs,
    )


def _assistant_with_intent(intent: RequestIntent, index: int) -> ChatMessage:
    execution = MessageExecutionRecord(
        plan=TaskPlan(
            id=f"plan-{index}",
            request_id=f"request-{index}",
            policy="balanced",
            verbosity="detailed",
            analysis=RequestAnalysis(
                source="local_model",
                intent=intent,
                confidence=0.94,
                task_summary=f"Continue the {intent} task.",
            ),
            route="local",
            model_id=f"local:{intent}:test",
            rationale=f"Selected the {intent} route.",
            steps=[],
        ),
        traces=[],
        started_at=float(index),
        completed_at=float(index + 1),
    )
    return ChatMessage(
        id=f"assistant-{index}",
        role="assistant",
        content=f"Previous {intent} response.",
        created_at=f"1970-01-01T00:00:0{index}.000Z",
        execution=execution,
    )


def _match_object(actual: object, expected: dict) -> None:
    """Partial match: all key-value pairs in `expected` must exist in `actual`."""
    if isinstance(actual, dict):
        d = actual
    else:
        d = vars(actual) if hasattr(actual, "__dict__") else {}
    for key, val in expected.items():
        assert key in d, f"Missing key '{key}' in {d}"
        actual_val = d[key]
        if isinstance(val, dict):
            _match_object(actual_val, val)
        elif isinstance(val, list):
            assert list(actual_val) == val, (
                f"Key '{key}': expected {val}, got {actual_val}"
            )
        else:
            assert actual_val == val, (
                f"Key '{key}': expected {val!r}, got {actual_val!r}"
            )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_compiler = RequestCompiler()


class TestRequestCompiler:
    def test_model_capabilities_not_fresh_web_research(self):
        compiled = _compiler.compile(_request("What are your current capabilities?"))

        _match_object(
            compiled.requirements,
            {
                "intent": "conversation",
                "intent_confidence": 0.5,
                "intent_source": "default",
                "capabilities": ["chat"],
                "requires_freshness": False,
                "contains_sensitive_data": False,
                "contains_web_grounded_data": False,
            },
        )
        assert compiled.verbosity == "standard"
        assert compiled.analysis.source == "heuristic"
        assert compiled.analysis.intent == "conversation"
        assert compiled.analysis.confidence == 0.5

    def test_preserves_explicit_verbosity(self):
        req = _request("Explain the routing decision.")
        req.verbosity = "detailed"
        assert _compiler.compile(req).verbosity == "detailed"

    @pytest.mark.parametrize(
        "prompt",
        [
            "What is the current federal tax law?",
            "Who is currently the CEO?",
            "Give me today's weather.",
            "Research the latest Qwen release.",
        ],
    )
    def test_time_sensitive_requests_are_fresh(self, prompt):
        compiled = _compiler.compile(_request(prompt))
        assert compiled.requirements.intent == "research"
        assert compiled.requirements.requires_freshness is True
        assert compiled.requirements.capabilities == ["chat", "reasoning", "web"]

    @pytest.mark.parametrize(
        "prompt",
        [
            "What is the most recent coding model advisor from Qwen?",
            "So qwen3.5:9B is the best spoke publicly available?",
            "What is the best publicly available model?",
            "What is the state of the art open weight model?",
        ],
    )
    def test_present_tense_ranking_of_public_options_searches(self, prompt):
        compiled = _compiler.compile(_request(prompt))
        assert compiled.requirements.requires_freshness is True
        assert "web" in compiled.requirements.capabilities

    @pytest.mark.parametrize(
        "prompt",
        [
            "What is the latest from Qwen?",
            "What is the newest Llama available?",
        ],
    )
    def test_named_model_family_is_time_sensitive(self, prompt):
        compiled = _compiler.compile(_request(prompt))
        assert "web" in compiled.requirements.capabilities

    @pytest.mark.parametrize(
        "prompt",
        [
            "How does open source licensing work?",
            "Explain how Llama attention works.",
            "What is the best way to sort this array?",
            "What are the best practices for error handling?",
        ],
    )
    def test_half_signal_not_fresh(self, prompt):
        compiled = _compiler.compile(_request(prompt))
        assert compiled.requirements.requires_freshness is False
        assert "web" not in compiled.requirements.capabilities

    def test_personal_fresh_withholds_web(self):
        compiled = _compiler.compile(
            _request("What is my current medication schedule?")
        )
        assert "web" not in compiled.requirements.capabilities

    def test_citation_request_is_research_without_freshness(self):
        compiled = _compiler.compile(
            _request("Cite sources supporting this architectural recommendation.")
        )
        assert compiled.requirements.intent == "research"
        assert compiled.requirements.requires_freshness is False
        assert compiled.requirements.capabilities == ["chat", "reasoning", "web"]

    @pytest.mark.parametrize(
        "prompt",
        [
            "Add a source map to the Webpack build.",
            "Add a citations field to this TypeScript interface.",
            "Include the source code file in the package.",
            "Provide a source property on this React component.",
            "Review the latest source code: const internalAlgorithm = 42;",
            "Undo my most recent local commit.",
            "Use the latest value from this array.",
            "Fix the live preview component in this code.",
        ],
    )
    def test_no_web_for_local_coding_language(self, prompt):
        compiled = _compiler.compile(_request(prompt))
        assert compiled.requirements.intent == "coding"
        assert "web" not in compiled.requirements.capabilities

    @pytest.mark.parametrize(
        "prompt",
        [
            "List sources from the local database without using the internet.",
            "Do not use the internet. Summarize the Acme merger for me.",
            "Don't search the web. What are the latest news on the merger?",
            "Never access the internet. Explain the current prices.",
            "Research the local database; do not use external services.",
        ],
    )
    def test_honors_explicit_network_denial(self, prompt):
        compiled = _compiler.compile(_request(prompt))
        assert "web" not in compiled.requirements.capabilities

    @pytest.mark.parametrize(
        "prompt",
        [
            "Research this offline.",
            "Research only my local notes.",
            "Research the local database; do not use external services.",
            "Research this topic.",
        ],
    )
    def test_research_intent_alone_not_network_consent(self, prompt):
        compiled = _compiler.compile(_request(prompt))
        assert compiled.requirements.intent == "research"
        assert compiled.requirements.capabilities == ["chat", "reasoning"]

    def test_explicit_web_search_authorizes_web(self):
        caps = _compiler.compile(
            _request("Search the web for Quorum architecture sources.")
        ).requirements.capabilities
        assert caps == ["chat", "reasoning", "web"]

    def test_external_web_phrasing_authorizes_research(self):
        compiled = _compiler.compile(
            _request("Use external web sources to compare these claims.")
        )
        assert compiled.requirements.intent == "research"
        assert "web" in compiled.requirements.capabilities

    @pytest.mark.parametrize(
        "prompt",
        [
            "Now summarize that offline.",
            "Now compare it using only my local notes.",
            "What about it without external services?",
        ],
    )
    def test_follow_up_denial_overrides_prior_web(self, prompt):
        compiled = _compiler.compile(
            _conversation_request([
                "Search the web for the latest Quorum release.",
                prompt,
            ])
        )
        assert compiled.requirements.intent == "research"
        assert "web" not in compiled.requirements.capabilities

    @pytest.mark.parametrize(
        "prompt",
        [
            "Solve this equation: 2x + 4 = 12.",
            "Analyze the logic of this argument.",
            "Calculate the area of a circle with radius 5.",
            "Compare a modular architecture with a monolith for a local-first assistant, then recommend a practical starting point.",
        ],
    )
    def test_classifies_explicit_reasoning_work(self, prompt):
        compiled = _compiler.compile(_request(prompt))
        assert compiled.requirements.intent == "reasoning"
        assert compiled.requirements.capabilities == ["chat", "reasoning"]
        assert compiled.requirements.requires_freshness is False

    @pytest.mark.parametrize(
        "prompt",
        [
            "Write a SQL query to list overdue invoices.",
            "Refactor this Go method to avoid duplication.",
            "Solve this SQL query.",
            "Analyze the runtime complexity of this algorithm.",
            "Thank you. Could this be used easily with JS applications?",
            "Can I call this from a TS service?",
            "Integrate this with NodeJS.",
            "Would JQuery be any different?",
            "Migrate this Angular component to Vue.js.",
            "Run the suite with pytest.",
            "Containerize the service with Docker.",
            "Change the PostgreSQL schema.",
            "Update package.json and tsconfig.json.",
            "Add a route to this Express app.",
            "Configure the Spring Boot service.",
            "Write an HCL module for Terraform.",
            "How should I structure React state?",
            "Why does my Android Activity crash?",
            "Explain this HTTP 500 from the API.",
            "Update this Helm chart.",
            "Optimize this CUDA kernel.",
            "Validate this YAML config.",
            "Fix this regular expression.",
            "Deploy the AWS Lambda.",
            "Please review this source code for bugs.",
            "How do I build a Docker image?",
            "Write a function to resize an image in Python.",
            "Take a screenshot programmatically in Node.",
        ],
    )
    def test_recognizes_concrete_coding(self, prompt):
        assert _compiler.compile(_request(prompt)).requirements.intent == "coding"

    def test_keeps_most_recent_network_denial_across_follow_ups(self):
        compiled = _compiler.compile(
            _conversation_request([
                "Search the web for the latest Quorum release.",
                "From now on, do not use the internet.",
                "Also tell me more.",
            ])
        )
        assert compiled.requirements.intent == "research"
        assert "web" not in compiled.requirements.capabilities

    def test_latest_authorization_re_grants_web_after_denial(self):
        compiled = _compiler.compile(
            _conversation_request([
                "Do not use the internet for this.",
                "Actually, search the web for the latest Quorum release.",
                "Also tell me more.",
            ])
        )
        assert compiled.requirements.intent == "research"
        assert "web" in compiled.requirements.capabilities

    @pytest.mark.parametrize(
        "prompt",
        [
            "What is my current medication schedule?",
            "Give me the sources for my HIV medication.",
            "What are my current payroll prices?",
        ],
    )
    def test_personal_scope_freshness_no_web(self, prompt):
        compiled = _compiler.compile(_request(prompt))
        assert "web" not in compiled.requirements.capabilities
        assert compiled.requirements.contains_sensitive_data is True

    def test_coding_domains_despite_abstract_visual(self):
        prompt = "Explain the architecture diagram pattern for microservices."
        assert _compiler.compile(_request(prompt)).requirements.intent == "coding"

    @pytest.mark.parametrize(
        "prompt",
        [
            "Compare image formats for archival storage.",
            "Analyze the visual design of this UI.",
        ],
    )
    def test_no_vision_for_abstract_visual_language(self, prompt):
        assert _compiler.compile(_request(prompt)).requirements.intent != "vision"

    @pytest.mark.parametrize(
        "prompt",
        [
            "Analyze this attached screenshot.",
            "Read the uploaded diagram.",
            "What is in this image?",
            "What does this PCB show?",
        ],
    )
    def test_recognizes_explicit_visual_inspection(self, prompt):
        assert _compiler.compile(_request(prompt)).requirements.intent == "vision"

    @pytest.mark.parametrize(
        "prompt",
        [
            "There is no bug; tell me a joke.",
            "Do not calculate anything; just chat.",
            "What is API pricing?",
            "Analyze how I feel about this.",
            "Tell me about JS Bach.",
            "Read a TS Eliot poem.",
            "There is rust on my bicycle.",
            "Should I go to the store?",
            "How should I react to criticism?",
            "The oracle at Delphi gave an answer.",
            "She writes poetry every morning.",
            "The cargo arrived by rail.",
            "Explain angular momentum.",
            "What does a python eat?",
            "Tell me about Java coffee.",
            "Please nix that proposal.",
            "That song is groovy.",
            "Throw a dart at the board.",
            "The solidity of packed snow varies.",
            "She took a flask with her.",
            "Tell me about Cassandra in Greek mythology.",
            "I need a prettier room.",
            "Should I use the bus or go by train?",
            "There is rust on my bike with a broken chain.",
            "Build a nest for the birds.",
            "How do I install a spring on a door?",
            "Install the spring on the door.",
            "Use the flask for water.",
            "Run the dart tournament.",
            "Flutter activity in my chest worries me.",
            "We run in the spring.",
            "They work in unity.",
            "Travel via rails to the station.",
            "Use Java coffee in the recipe.",
            "That rude man is a git.",
            "Could humans terraform Mars?",
            "What is the molarity of HCl?",
            "I booked tickets at Vue cinema.",
            "Do not sass me.",
            "The museum displayed a ruby gem.",
            "Our community unity project brought neighbors together.",
            "The spring bean crop was planted early.",
            "I booked a Java class about Indonesian history.",
            "The oracle query was answered by the priestess.",
            "Tell me how to use cargo rail services.",
            "The rust test on this metal was written yesterday.",
            "What is better, plan A or C?",
            "Review the fashion models and react to their poses.",
        ],
    )
    def test_incidental_keywords_not_routed_to_expert(self, prompt):
        assert _compiler.compile(_request(prompt)).requirements.intent == "conversation"

    @pytest.mark.parametrize(
        "prompt",
        [
            "Prove that sqrt(2) is irrational.",
            "What is 17 * 23?",
            "Solve this probability problem.",
        ],
    )
    def test_recognizes_direct_mathematical_reasoning(self, prompt):
        assert _compiler.compile(_request(prompt)).requirements.intent == "reasoning"

    def test_carries_established_task_through_referential_follow_up(self):
        compiled = _compiler.compile(
            _conversation_request([
                "Refactor this TypeScript function to remove duplication.",
                "Now make it faster.",
            ])
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "coding",
                "intent_source": "conversation",
                "intent_confidence": 0.78,
                "capabilities": ["chat", "coding"],
            },
        )

    def test_chained_contextual_follow_ups(self):
        compiled = _compiler.compile(
            _conversation_request([
                "Create a SQL PIVOT query with dynamic columns.",
                "Are there any better ways?",
                "What about for ORACLE?",
            ])
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "coding",
                "intent_source": "conversation",
                "intent_confidence": 0.78,
                "capabilities": ["chat", "coding"],
            },
        )

    def test_courteous_referential_follow_up_uses_previous_effective_intent(self):
        compiled = _compiler.compile(
            ChatRequest(
                conversation_id="conversation-1",
                policy="balanced",
                messages=[
                    ChatMessage(
                        id="message-1",
                        role="user",
                        content="Design a backend integration.",
                        created_at="1970-01-01T00:00:00.000Z",
                    ),
                    _assistant_with_intent("coding", 1),
                    ChatMessage(
                        id="message-2",
                        role="user",
                        content="Thank you. Could this be used easily with desktop applications?",
                        created_at="1970-01-01T00:00:02.000Z",
                    ),
                ],
            )
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "coding",
                "intent_source": "conversation",
                "intent_confidence": 0.78,
                "capabilities": ["chat", "coding"],
            },
        )

    @pytest.mark.parametrize(
        "prompt",
        [
            "Are there any better ways?",
            "Is there a better approach?",
            "What other options are there?",
            "Any alternatives?",
            "What else?",
        ],
    )
    def test_comparative_follow_up_carries_task(self, prompt):
        compiled = _compiler.compile(
            _conversation_request([
                "Create a SQL PIVOT query with dynamic columns.",
                prompt,
            ])
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "coding",
                "intent_source": "conversation",
                "intent_confidence": 0.78,
                "capabilities": ["chat", "coding"],
            },
        )

    @pytest.mark.parametrize(
        "prompt",
        [
            "What about Vue?",
            "What about Python instead?",
            "What about Python for this?",
            "Would Python work here?",
            "Could Python be used here?",
            "Does React work the same way?",
            "Would Terraform work for this?",
            "Python instead?",
            "Could we use Python?",
        ],
    )
    def test_named_technology_comparison_only_from_coding_task(self, prompt):
        compiled = _compiler.compile(
            _conversation_request([
                "Migrate this Angular component.",
                prompt,
            ])
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "coding",
                "intent_source": "conversation",
                "capabilities": ["chat", "coding"],
            },
        )

    @pytest.mark.parametrize(
        "prompt",
        [
            "What about Java?",
            "What about Java instead?",
            "Would Java work here?",
        ],
    )
    def test_geographic_java_not_coding(self, prompt):
        compiled = _compiler.compile(
            _conversation_request([
                "Tell me about Indonesian islands.",
                prompt,
            ])
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "conversation",
                "intent_source": "default",
                "capabilities": ["chat"],
            },
        )

    def test_explicit_topic_reset(self):
        compiled = _compiler.compile(
            _conversation_request([
                "Solve this equation: 2x = 8.",
                "New topic: tell me a joke.",
            ])
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "conversation",
                "intent_source": "default",
            },
        )

    def test_no_cross_explicit_reset_on_later_short_follow_up(self):
        compiled = _compiler.compile(
            _conversation_request([
                "Write a TypeScript function.",
                "New topic: tell me a joke.",
                "Why?",
            ])
        )
        assert compiled.requirements.intent == "conversation"

    def test_no_cross_explicit_reset_in_follow_up_chain(self):
        compiled = _compiler.compile(
            _conversation_request([
                "Create a SQL PIVOT query with dynamic columns.",
                "Are there any better ways?",
                "New topic: tell me a joke.",
                "What about that?",
            ])
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "conversation",
                "intent_source": "default",
            },
        )

    def test_courtesy_prefixed_reset_not_persisted(self):
        compiled = _compiler.compile(
            ChatRequest(
                conversation_id="conversation-1",
                policy="balanced",
                messages=[
                    ChatMessage(
                        id="message-1",
                        role="user",
                        content="Design a backend integration.",
                        created_at="1970-01-01T00:00:00.000Z",
                    ),
                    _assistant_with_intent("coding", 1),
                    ChatMessage(
                        id="message-2",
                        role="user",
                        content="Thank you. New topic: tell me a joke.",
                        created_at="1970-01-01T00:00:02.000Z",
                    ),
                ],
            )
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "conversation",
                "intent_source": "default",
            },
        )

    def test_no_persist_across_completed_conversational_turn(self):
        compiled = _compiler.compile(
            ChatRequest(
                conversation_id="conversation-1",
                policy="balanced",
                messages=[
                    ChatMessage(
                        id="message-1",
                        role="user",
                        content="Design a backend integration.",
                        created_at="1970-01-01T00:00:00.000Z",
                    ),
                    _assistant_with_intent("coding", 1),
                    ChatMessage(
                        id="message-2",
                        role="user",
                        content="New topic: let's just talk.",
                        created_at="1970-01-01T00:00:02.000Z",
                    ),
                    _assistant_with_intent("conversation", 3),
                    ChatMessage(
                        id="message-3",
                        role="user",
                        content="Thanks. Could this be improved?",
                        created_at="1970-01-01T00:00:04.000Z",
                    ),
                ],
            )
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "conversation",
                "intent_source": "default",
            },
        )

    @pytest.mark.parametrize(
        "prompt",
        [
            "continue",
            "please continue",
            "Please continue.",
            "continue with it",
            "keep going",
            "go on",
            "carry on",
        ],
    )
    def test_bare_continuation_inherits_persisted_intent(self, prompt):
        compiled = _compiler.compile(
            ChatRequest(
                conversation_id="conversation-1",
                policy="balanced",
                messages=[
                    ChatMessage(
                        id="message-1",
                        role="user",
                        content="Explore biomimetic applications to computer technology.",
                        created_at="1970-01-01T00:00:00.000Z",
                    ),
                    _assistant_with_intent("research", 1),
                    ChatMessage(
                        id="message-2",
                        role="user",
                        content=prompt,
                        created_at="1970-01-01T00:00:02.000Z",
                    ),
                ],
            )
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "research",
                "intent_source": "conversation",
                "capabilities": ["chat", "reasoning"],
            },
        )

    def test_object_bearing_continuation_inherits_coding(self):
        compiled = _compiler.compile(
            ChatRequest(
                conversation_id="conversation-1",
                policy="balanced",
                messages=[
                    ChatMessage(
                        id="message-1",
                        role="user",
                        content="Implement an Oracle query in Node.js.",
                        created_at="1970-01-01T00:00:00.000Z",
                    ),
                    _assistant_with_intent("coding", 1),
                    ChatMessage(
                        id="message-2",
                        role="user",
                        content="Please continue the implementation.",
                        created_at="1970-01-01T00:00:02.000Z",
                    ),
                ],
            )
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "coding",
                "intent_source": "conversation",
                "capabilities": ["chat", "coding"],
            },
        )

    def test_recovers_coding_after_low_confidence_misroute(self):
        misrouted = _assistant_with_intent("conversation", 3)
        if misrouted.execution is not None:
            misrouted.execution.plan.analysis.confidence = 0.5
            misrouted.execution.plan.analysis.source = "hybrid"

        compiled = _compiler.compile(
            ChatRequest(
                conversation_id="conversation-1",
                policy="balanced",
                messages=[
                    ChatMessage(
                        id="message-1",
                        role="user",
                        content="Create a dynamic SQL PIVOT query.",
                        created_at="1970-01-01T00:00:00.000Z",
                    ),
                    _assistant_with_intent("coding", 1),
                    ChatMessage(
                        id="message-2",
                        role="user",
                        content="Could this be used easily with JS applications?",
                        created_at="1970-01-01T00:00:02.000Z",
                    ),
                    misrouted,
                    ChatMessage(
                        id="message-3",
                        role="user",
                        content="Thanks. Could this be made asynchronous?",
                        created_at="1970-01-01T00:00:04.000Z",
                    ),
                ],
            )
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "coding",
                "intent_source": "conversation",
                "capabilities": ["chat", "coding"],
            },
        )

    def test_new_what_about_not_persisted_coding_follow_up(self):
        compiled = _compiler.compile(
            ChatRequest(
                conversation_id="conversation-1",
                policy="balanced",
                messages=[
                    ChatMessage(
                        id="message-1",
                        role="user",
                        content="Design a backend integration.",
                        created_at="1970-01-01T00:00:00.000Z",
                    ),
                    _assistant_with_intent("coding", 1),
                    ChatMessage(
                        id="message-2",
                        role="user",
                        content="What about the weather?",
                        created_at="1970-01-01T00:00:02.000Z",
                    ),
                ],
            )
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "conversation",
                "intent_source": "default",
            },
        )

    @pytest.mark.parametrize(
        "prompt",
        [
            "What should I cook this weekend?",
            "Tell me about this composer.",
            "What is the source of this error?",
        ],
    )
    def test_unrelated_pronoun_not_follow_up(self, prompt):
        compiled = _compiler.compile(
            _conversation_request(["Write a TypeScript function.", prompt])
        )
        assert compiled.requirements.intent == "conversation"

    def test_detects_sensitive_data_anywhere_in_context(self):
        compiled = _compiler.compile(
            _conversation_request([
                "My private key is in the earlier message.",
                "Now summarize that.",
            ])
        )
        assert compiled.requirements.contains_sensitive_data is True

    @pytest.mark.parametrize(
        "value",
        [
            # Full-length tokens after the redacted prefix so the credential
            # patterns actually fire (NFKC turns \u2026 into three dots).
            "\u00abredacted:xoxb-abcdefghijklmnopqrstuv\u00bb",
            "\u00abredacted:sk_live_abcdefghijklmnopqrst\u00bb",
            "postgres://admin:***@database.example/app",
            "4111 1111 1111 1111",
            "INTERNAL ONLY Project Falcon roadmap",
            "AccountKey=abcdefghijklmnopqrstuvwxyz012345",
        ],
    )
    def test_detects_common_credential_and_restricted_data_forms(self, value):
        assert contains_sensitive_content(value) is True

    @pytest.mark.parametrize(
        "value, category",
        [
            (
                "AWS_SECRET_ACCESS_KEY=abcdefghijklmnopqrstuvwxyz1234567890ABCD",
                "credentials",
            ),
            (
                "GOOGLE_API_KEY=\u00abredacted:AIzaSyD5P9n4abc1234567890abcdefghijklmn\u00bb",
                "credentials",
            ),
            ("SSN 123456789", "government_id"),
            ("DB_PASS=hunter2-example", "credentials"),
            ("IBAN: GB82WEST12345698765432", "financial"),
            ("email: person@example.com", "personal_contact"),
            ("my medical diagnosis is private", "health"),
            ("p\u0430ssword\u200b=hidden-value", "credentials"),
            (
                "Jane Q. Doe, born 1984-03-11, 12 Elm St, 555-867-5309",
                "personal_contact",
            ),
            ("I was just diagnosed with stage 2 lymphoma", "health"),
            (
                "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDgL7SFnKcY3Q8u",
                "private_key",
            ),
            ("My social is 123456789", "government_id"),
            ("social: 123456789", "government_id"),
            ("social security number 123456789", "government_id"),
        ],
    )
    def test_detects_normalized_sensitive_data(self, value, category):
        compiled = _compiler.compile(_request(value))
        assert compiled.requirements.contains_sensitive_data is True
        assert category in compiled.requirements.sensitive_data_categories

    @pytest.mark.parametrize(
        "value",
        [
            "How do I hash a password with bcrypt in Node?",
            "Explain what an API key is.",
            "What does 'confidential' mean in a legal contract?",
            "Order number 4532015112830366 shipped today",
            "Her social media following is 1234567890 strong",
            "The social had 12345 attendees",
        ],
    )
    def test_no_sensitive_false_positive(self, value):
        assert contains_sensitive_content(value) is False

    def test_assistant_secret_terminology_not_user_data(self):
        compiled = _compiler.compile(
            ChatRequest(
                conversation_id="conversation-1",
                policy="balanced",
                messages=[
                    ChatMessage(
                        id="assistant-1",
                        role="assistant",
                        content="A password is an authentication secret.",
                        created_at="1970-01-01T00:00:00.000Z",
                    ),
                    ChatMessage(
                        id="user-2",
                        role="user",
                        content="Can you expand on that?",
                        created_at="1970-01-01T00:00:01.000Z",
                    ),
                ],
            )
        )
        assert compiled.requirements.contains_sensitive_data is False

    def test_assistant_live_looking_key_not_poison(self):
        compiled = _compiler.compile(
            ChatRequest(
                conversation_id="conversation-1",
                policy="balanced",
                messages=[
                    ChatMessage(
                        id="assistant-1",
                        role="assistant",
                        content="Keys such as \u00abredacted:sk-\u2026\u00bb should be rotated immediately.",
                        created_at="1970-01-01T00:00:00.000Z",
                    ),
                    ChatMessage(
                        id="user-2",
                        role="user",
                        content="Tell me more about that.",
                        created_at="1970-01-01T00:00:01.000Z",
                    ),
                ],
            )
        )
        assert compiled.requirements.contains_sensitive_data is False
        assert compiled.requirements.sensitive_data_categories == []

    def test_prior_web_grounded_assistant_output_local_only(self):
        compiled = _compiler.compile(
            ChatRequest(
                conversation_id="conversation-1",
                policy="balanced",
                messages=[
                    ChatMessage(
                        id="assistant-1",
                        role="assistant",
                        content="A web-grounded answer.",
                        provenance="web_grounded",
                        created_at="1970-01-01T00:00:00.000Z",
                    ),
                    ChatMessage(
                        id="user-2",
                        role="user",
                        content="Compare that with the alternative.",
                        created_at="1970-01-01T00:00:01.000Z",
                    ),
                ],
            )
        )
        assert compiled.requirements.contains_web_grounded_data is True

    # --- applyPromptAnalysis tests ---

    def test_uses_confident_local_prompt_analysis(self):
        baseline = _compiler.compile(_request("Can you help me with this?"))
        compiled = _compiler.apply_prompt_analysis(
            baseline,
            {"id": "local:classifier:test", "label": "Tiny classifier"},
            PromptAnalyzerResult(
                intent="coding",
                confidence=0.91,
                task_summary="Help with the current coding task.",
            ),
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "coding",
                "intent_confidence": 0.91,
                "intent_source": "classifier",
                "capabilities": ["chat", "coding"],
            },
        )
        assert compiled.analysis.source == "local_model"
        assert compiled.analysis.intent == "coding"
        assert compiled.analysis.task_summary == "Help with the current coding task."
        assert compiled.analysis.analyzer is not None
        assert compiled.analysis.analyzer.model_id == "local:classifier:test"  # type: ignore[union-attr]

    def test_classifier_never_authorizes_network_search(self):
        baseline = _compiler.compile(_request("Can you help me with this?"))
        compiled = _compiler.apply_prompt_analysis(
            baseline,
            {"id": "local:classifier:test", "label": "Tiny classifier"},
            PromptAnalyzerResult(
                intent="research",
                confidence=0.99,
                task_summary="Search for current information.",
            ),
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "research",
                "intent_source": "classifier",
                "capabilities": ["chat", "reasoning"],
                "requires_freshness": False,
            },
        )

    def test_strong_deterministic_beats_conflicting_tiny_model(self):
        baseline = _compiler.compile(
            _request("Write a SQL query for dynamic pivot columns.")
        )
        compiled = _compiler.apply_prompt_analysis(
            baseline,
            {"id": "local:classifier:test", "label": "Tiny classifier"},
            PromptAnalyzerResult(
                intent="conversation",
                confidence=0.9,
                task_summary="Discuss database tables.",
            ),
        )
        assert compiled.requirements.intent == "coding"
        assert compiled.analysis.source == "hybrid"
        assert compiled.analysis.intent == "coding"
        assert compiled.analysis.analyzer is not None
        assert compiled.analysis.analyzer.intent == "conversation"  # type: ignore[union-attr]

    def test_prompt_expert_not_override_architecture_to_document(self):
        baseline = _compiler.compile(
            _request(
                "Compare a modular architecture with a monolith, then recommend a starting point."
            )
        )
        compiled = _compiler.apply_prompt_analysis(
            baseline,
            {"id": "local:classifier:test", "label": "Prompt expert"},
            PromptAnalyzerResult(
                intent="document",
                confidence=1.0,
                task_summary="Compare two software architectures.",
            ),
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "reasoning",
                "intent_confidence": 0.9,
                "capabilities": ["chat", "reasoning"],
            },
        )
        assert compiled.analysis.source == "hybrid"
        assert compiled.analysis.intent == "reasoning"
        assert compiled.analysis.analyzer is not None
        assert compiled.analysis.analyzer.intent == "document"  # type: ignore[union-attr]

    def test_prompt_expert_corrects_contextual_software_guess(self):
        baseline = _compiler.compile(
            _request("Explain the React state of this art exhibition.")
        )
        _match_object(
            baseline.requirements,
            {
                "intent": "coding",
                "intent_confidence": 0.82,
            },
        )

        compiled = _compiler.apply_prompt_analysis(
            baseline,
            {"id": "local:classifier:test", "label": "Prompt expert"},
            PromptAnalyzerResult(
                intent="conversation",
                confidence=0.95,
                task_summary="Discuss an art exhibition.",
            ),
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "conversation",
                "intent_source": "classifier",
            },
        )

    def test_protects_inherited_specialist_context_from_tiny_model(self):
        baseline = _compiler.compile(
            _conversation_request([
                "Create a SQL PIVOT query with dynamic columns.",
                "Are there any better ways?",
            ])
        )
        compiled = _compiler.apply_prompt_analysis(
            baseline,
            {"id": "local:classifier:test", "label": "Tiny classifier"},
            PromptAnalyzerResult(
                intent="conversation",
                confidence=1.0,
                task_summary="Discuss alternative approaches.",
            ),
        )
        _match_object(
            compiled.requirements,
            {
                "intent": "coding",
                "intent_source": "conversation",
                "capabilities": ["chat", "coding"],
            },
        )
        assert compiled.analysis.source == "hybrid"
        assert compiled.analysis.intent == "coding"
        assert compiled.analysis.analyzer is not None
        assert compiled.analysis.analyzer.intent == "conversation"  # type: ignore[union-attr]

    def test_prompt_analysis_never_clears_sensitive_data_detection(self):
        baseline = _compiler.compile(
            _request(
                "Use API key \u00abredacted:sk-proj-abcdefghijklmnopqrst\u00bb to help with this."
            )
        )
        compiled = _compiler.apply_prompt_analysis(
            baseline,
            {"id": "local:classifier:test", "label": "Tiny classifier"},
            PromptAnalyzerResult(
                intent="conversation",
                confidence=0.95,
                task_summary="Help with a request.",
            ),
        )
        assert compiled.requirements.contains_sensitive_data is True

    @pytest.mark.parametrize(
        "prompt",
        [
            # Full-length tokens so credential regexes actually fire.
            "Customer credential \u00abredacted:AKIAIOSFODNN7EXAMPLE\u00bb",
            "The account number is 123-45-6789",
            "Use this API key for the request",
            "OPENAI_API_KEY=\u00abredacted:sk-proj-abcdefghijklmnopqrstuv\u00bb",
            "api_key=private-value",
            "\u00abredacted:ghp_abcdefghijklmnopqrstuvwxyz12\u00bb",
            "Authorization: Basic dXNlcjE6cGFzc3dvcmQxMjM0NTY3ODkwYWJjZGU=",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmno",
        ],
    )
    def test_recognizes_common_structured_secret_signals(self, prompt):
        assert (
            _compiler.compile(_request(prompt)).requirements.contains_sensitive_data
            is True
        )


# ---------------------------------------------------------------------------
# detect_sensitive_content standalone tests
# ---------------------------------------------------------------------------


class TestDetectSensitiveContent:
    def test_empty_string_no_categories(self):
        assert detect_sensitive_content("") == []

    def test_absolute_workspace_path_is_not_a_credential(self):
        content = (
            "/tmp/pytest-of-runner/pytest-284/"
            "test_codex_final_preflight_bou0/hermes_test"
        )
        assert detect_sensitive_content(content) == []

    def test_generic_opaque_high_entropy_token_is_a_credential(self):
        token = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        assert "credentials" in detect_sensitive_content(token)

    def test_aws_access_key(self):
        cats = detect_sensitive_content("AKIAIOSFODNN7EXAMPLE")
        assert "credentials" in cats

    def test_gcp_api_key(self):
        cats = detect_sensitive_content("AIzaSyD5P9n4abc1234567890abcdefghijklmn")
        assert "credentials" in cats

    def test_openai_api_key(self):
        cats = detect_sensitive_content("sk-proj-abc123def456ghi789jkl012mno345pqr")
        assert "credentials" in cats

    def test_stripe_live_key(self):
        cats = detect_sensitive_content("sk_live_abc123def456ghi789jkl")
        assert "credentials" in cats

    def test_github_pat(self):
        cats = detect_sensitive_content("ghp_abcdefghijklmnopqrstuvwxyz12")
        assert "credentials" in cats

    def test_jwt_token(self):
        cats = detect_sensitive_content(
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        assert "credentials" in cats

    def test_private_key_pem(self):
        cats = detect_sensitive_content(
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA..."
        )
        assert "private_key" in cats

    def test_private_key_base64(self):
        cats = detect_sensitive_content(
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQD"
        )
        assert "private_key" in cats

    def test_ssn_dashed(self):
        cats = detect_sensitive_content("123-45-6789")
        assert "government_id" in cats

    def test_ssn_labeled(self):
        cats = detect_sensitive_content("SSN: 123456789")
        assert "government_id" in cats

    def test_health_diagnosis(self):
        cats = detect_sensitive_content("My diagnosis is stage 3 melanoma")
        assert "health" in cats

    def test_health_prescription(self):
        cats = detect_sensitive_content("prescription: ibuprofen 200mg daily")
        assert "health" in cats

    def test_confidential_marker(self):
        cats = detect_sensitive_content("INTERNAL ONLY Project Falcon")
        assert "confidential" in cats

    def test_db_connection_string(self):
        cats = detect_sensitive_content(
            "postgres://admin:hunter2@database.example.com/app"
        )
        assert "credentials" in cats

    def test_env_var_with_secret(self):
        cats = detect_sensitive_content("OPENAI_API_KEY=sk-abc123")
        assert "credentials" in cats

    def test_confusable_ascii_password(self):
        # Uses Cyrillic 'а' (U+0430) instead of Latin 'a' in 'password'
        cats = detect_sensitive_content("p\u0430ssword = secret12345")
        assert "credentials" in cats

    def test_zero_width_space_in_token(self):
        # \u200B is a zero-width space (Cf character) — should be stripped
        cats = detect_sensitive_content("sk-\u200bproj-abc123def456ghi789jkl")
        assert "credentials" in cats
