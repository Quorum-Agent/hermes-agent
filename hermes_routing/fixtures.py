"""Versioned JSON fixture runner for TypeScript/Python differential tests.

The TypeScript policy core remains the canonical behavior specification while
Quorum embeds a Python port in Hermes. This module defines the language-neutral
boundary between them. Fixture inputs and normalized outcomes use camelCase;
generated UUIDs and timestamps are deliberately omitted.

A fixture producer does not need to import Python. It writes a document shaped
like this::

    {
      "schema": "quorum.routing.v1",
      "cases": [{
        "id": "private-local",
        "operation": "plan",
        "input": {"request": {...}, "models": [...]},
        "expected": {"ok": true, "value": {...}}
      }]
    }

Run ``python -m hermes_routing.fixtures path/to/golden.json`` to verify every
case. ``--actual`` prints normalized outcomes without comparing them, which is
useful while building the matching TypeScript fixture exporter.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .compiler import RequestCompiler
from .envelope import UnsafeOutput, extract_public_answer
from .planner import RoutePlanner
from .policies import UnsupportedPolicyError, get_policy
from .types import (
    ChatMessage,
    ChatRequest,
    CompiledRequest,
    ModelDescriptor,
    ModelInferenceSettings,
    RequestAnalysis,
    RequestRequirements,
    TaskPlan,
)

FIXTURE_SCHEMA = "quorum.routing.v1"
SUPPORTED_OPERATIONS = frozenset({"compile", "plan", "policy", "envelope"})


class FixtureFormatError(ValueError):
    """A fixture document does not satisfy the versioned interface."""


class FixtureMismatch(AssertionError):
    """A Python outcome differs from the canonical expected outcome."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FixtureFormatError(f"{label} must be a JSON object.")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise FixtureFormatError(f"{label} must be a JSON array.")
    return value


def _required_string(value: Mapping[str, Any], key: str, label: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise FixtureFormatError(f"{label}.{key} must be a non-empty string.")
    return item


def _chat_request(value: Any) -> ChatRequest:
    data = _mapping(value, "request")
    messages: list[ChatMessage] = []
    for index, raw in enumerate(_sequence(data.get("messages"), "request.messages")):
        message = _mapping(raw, f"request.messages[{index}]")
        messages.append(
            ChatMessage(
                id=_required_string(message, "id", f"request.messages[{index}]"),
                role=_required_string(message, "role", f"request.messages[{index}]"),
                content=_required_string(
                    message, "content", f"request.messages[{index}]"
                ),
                created_at=str(message.get("createdAt", "")),
                provenance=message.get("provenance"),
            )
        )
    return ChatRequest(
        conversation_id=_required_string(data, "conversationId", "request"),
        messages=messages,
        policy=_required_string(data, "policy", "request"),
        verbosity=data.get("verbosity"),
    )


def _model(value: Any, index: int) -> ModelDescriptor:
    data = _mapping(value, f"models[{index}]")
    inference_data = data.get("inference")
    inference = None
    if inference_data is not None:
        normalized = _mapping(inference_data, f"models[{index}].inference")
        inference = ModelInferenceSettings(
            reasoning_effort=normalized.get("reasoningEffort"),
            max_output_tokens=normalized.get("maxOutputTokens"),
        )
    return ModelDescriptor(
        id=_required_string(data, "id", f"models[{index}]"),
        label=_required_string(data, "label", f"models[{index}]"),
        provider=_required_string(data, "provider", f"models[{index}]"),
        role=data.get("role"),
        location=str(data.get("location", "local")),
        transport=str(data.get("transport", "loopback")),
        capabilities=list(
            _sequence(data.get("capabilities", []), f"models[{index}].capabilities")
        ),
        context_window=int(data.get("contextWindow", 0)),
        quality_rating=int(data.get("qualityRating", 0)),
        specialties=list(
            _sequence(data.get("specialties", []), f"models[{index}].specialties")
        ),
        inference=inference,
        available=bool(data.get("available", False)),
        cost_per_million_tokens=data.get("costPerMillionTokens"),
    )


def _analysis(value: RequestAnalysis) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": value.source,
        "intent": value.intent,
        "confidence": value.confidence,
        "taskSummary": value.task_summary,
    }
    if value.analyzer is not None:
        result["analyzer"] = {
            "modelId": value.analyzer.model_id,
            "modelLabel": value.analyzer.model_label,
            "intent": value.analyzer.intent,
            "confidence": value.analyzer.confidence,
        }
    return result


def _requirements(value: RequestRequirements) -> dict[str, Any]:
    return {
        "intent": value.intent,
        "intentConfidence": value.intent_confidence,
        "intentSource": value.intent_source,
        "capabilities": list(value.capabilities),
        "requiresFreshness": value.requires_freshness,
        "containsSensitiveData": value.contains_sensitive_data,
        "sensitiveDataCategories": list(value.sensitive_data_categories),
        "containsWebGroundedData": value.contains_web_grounded_data,
    }


def normalize_compiled_request(value: CompiledRequest) -> dict[str, Any]:
    """Return only deterministic, cross-language compiler behavior."""

    return {
        "prompt": value.prompt,
        "policy": value.policy,
        "verbosity": value.verbosity,
        "analysis": _analysis(value.analysis),
        "requirements": _requirements(value.requirements),
    }


def normalize_plan(value: TaskPlan) -> dict[str, Any]:
    """Return a deterministic plan projection suitable for golden files."""

    result: dict[str, Any] = {
        "policy": value.policy,
        "verbosity": value.verbosity,
        "analysis": _analysis(value.analysis),
        "route": value.route,
        "modelId": value.model_id,
        "rationale": value.rationale,
        "steps": [
            {
                "label": step.label,
                "kind": step.kind,
                "location": step.location,
                **({"modelId": step.model_id} if step.model_id is not None else {}),
            }
            for step in value.steps
        ],
        "attempts": [],
        "safety": value.safety,
    }
    optional = {
        "spokeModelId": value.spoke_model_id,
        "synthesisDegraded": value.synthesis_degraded,
        "degraded": value.degraded,
        "fallbackFromModelId": value.fallback_from_model_id,
        "cloudDisclosure": value.cloud_disclosure,
        "webSearch": value.web_search,
    }
    result.update({key: item for key, item in optional.items() if item is not None})
    return result


def _normalize_policy(mode: str) -> dict[str, Any]:
    policy = get_policy(mode)
    return {
        "id": policy.id,
        "label": policy.label,
        "intent": policy.intent,
        "inferenceCeiling": policy.inference_ceiling,
        "toolCeiling": policy.tool_ceiling,
        "preferLocal": policy.prefer_local,
        "description": policy.description,
        **(
            {"cloudBudgetUsd": policy.cloud_budget_usd}
            if policy.cloud_budget_usd is not None
            else {}
        ),
    }


def _error_code(error: Exception) -> str:
    if isinstance(error, UnsupportedPolicyError):
        return "unsupported_policy"
    if isinstance(error, UnsafeOutput):
        return "unsafe_output"
    if isinstance(error, (KeyError, FixtureFormatError)):
        return "invalid_fixture"
    if isinstance(error, ValueError) and "No available model" in str(error):
        return "no_route"
    return "execution_error"


def run_fixture_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one case and return a stable success/error outcome."""

    operation = _required_string(case, "operation", "case")
    if operation not in SUPPORTED_OPERATIONS:
        raise FixtureFormatError(f"Unsupported fixture operation: {operation}.")
    inputs = _mapping(case.get("input"), "case.input")
    try:
        if operation == "compile":
            value = normalize_compiled_request(
                RequestCompiler().compile(_chat_request(inputs.get("request")))
            )
        elif operation == "plan":
            request = RequestCompiler().compile(_chat_request(inputs.get("request")))
            models = [
                _model(raw, index)
                for index, raw in enumerate(
                    _sequence(inputs.get("models"), "case.input.models")
                )
            ]
            excluded = frozenset(
                str(item)
                for item in _sequence(
                    inputs.get("excludedModelIds", []), "case.input.excludedModelIds"
                )
            )
            mode = str(inputs.get("orchestrationMode", "route"))
            value = normalize_plan(RoutePlanner(mode).plan(request, models, excluded))
        elif operation == "policy":
            value = _normalize_policy(_required_string(inputs, "mode", "case.input"))
        else:
            raw = inputs.get("raw")
            if not isinstance(raw, str):
                raise FixtureFormatError("case.input.raw must be a string.")
            value = {"answer": extract_public_answer(raw)}
        return {"ok": True, "value": value}
    except FixtureFormatError:
        raise
    except Exception as error:
        return {
            "ok": False,
            "error": {"code": _error_code(error), "message": str(error)},
        }


def load_fixture_document(path: str | Path) -> Mapping[str, Any]:
    """Load and minimally validate a versioned fixture document."""

    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FixtureFormatError(
            f"Could not read fixture document {source}: {error}"
        ) from error
    return _validate_document(document)


def _validate_document(document: Any) -> Mapping[str, Any]:
    normalized = _mapping(document, "document")
    if normalized.get("schema") != FIXTURE_SCHEMA:
        raise FixtureFormatError(
            f"document.schema must be {FIXTURE_SCHEMA!r}; got {normalized.get('schema')!r}."
        )
    cases = _sequence(normalized.get("cases"), "document.cases")
    seen: set[str] = set()
    for index, raw in enumerate(cases):
        case = _mapping(raw, f"document.cases[{index}]")
        case_id = _required_string(case, "id", f"document.cases[{index}]")
        if case_id in seen:
            raise FixtureFormatError(f"Duplicate fixture case id: {case_id}.")
        seen.add(case_id)
    return normalized


def evaluate_fixture_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return actual outcomes for every case without comparing goldens."""

    checked = _validate_document(document)
    return {
        "schema": FIXTURE_SCHEMA,
        "cases": [
            {
                "id": case["id"],
                "operation": case["operation"],
                "actual": run_fixture_case(case),
            }
            for case in checked["cases"]
        ],
    }


def verify_fixture_document(document: Mapping[str, Any]) -> None:
    """Compare every actual outcome with its canonical expected outcome."""

    checked = _validate_document(document)
    for case in checked["cases"]:
        if "expected" not in case:
            raise FixtureFormatError(
                f"Fixture case {case['id']!r} has no expected outcome."
            )
        actual = run_fixture_case(case)
        expected = case["expected"]
        if actual != expected:
            rendered_expected = json.dumps(
                expected, indent=2, sort_keys=True, ensure_ascii=False
            )
            rendered_actual = json.dumps(
                actual, indent=2, sort_keys=True, ensure_ascii=False
            )
            raise FixtureMismatch(
                f"Fixture {case['id']!r} diverged.\nExpected:\n{rendered_expected}\nActual:\n{rendered_actual}"
            )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Quorum routing golden fixtures."
    )
    parser.add_argument("fixture", type=Path)
    parser.add_argument(
        "--actual",
        action="store_true",
        help="print normalized actual outcomes instead of comparing expected values",
    )
    args = parser.parse_args(argv)
    document = load_fixture_document(args.fixture)
    if args.actual:
        print(
            json.dumps(
                evaluate_fixture_document(document), indent=2, ensure_ascii=False
            )
        )
    else:
        verify_fixture_document(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
