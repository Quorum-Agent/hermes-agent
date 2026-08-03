"""Behavior contract for the cross-language golden-fixture interface."""

from __future__ import annotations

import json

import pytest

from hermes_routing.fixtures import (
    FIXTURE_SCHEMA,
    FixtureFormatError,
    FixtureMismatch,
    evaluate_fixture_document,
    load_fixture_document,
    run_fixture_case,
    verify_fixture_document,
)


def _request(policy: str = "private") -> dict:
    return {
        "conversationId": "conversation-1",
        "policy": policy,
        "messages": [
            {
                "id": "message-1",
                "role": "user",
                "content": "Explain this idea.",
                "createdAt": "2026-01-01T00:00:00Z",
            }
        ],
    }


def _local_model() -> dict:
    return {
        "id": "local:test",
        "label": "Local Test",
        "provider": "test",
        "location": "local",
        "transport": "loopback",
        "capabilities": ["chat", "reasoning"],
        "contextWindow": 8192,
        "qualityRating": 50,
        "available": True,
    }


def test_compile_outcome_omits_generated_identity_and_is_repeatable():
    case = {"operation": "compile", "input": {"request": _request()}}

    first = run_fixture_case(case)
    second = run_fixture_case(case)

    assert first == second
    assert first["ok"] is True
    assert set(first["value"]) == {
        "prompt",
        "policy",
        "verbosity",
        "analysis",
        "requirements",
    }


def test_plan_outcome_is_repeatable_despite_generated_step_ids():
    case = {
        "operation": "plan",
        "input": {"request": _request(), "models": [_local_model()]},
    }

    first = run_fixture_case(case)
    second = run_fixture_case(case)

    assert first == second
    assert first["value"]["modelId"] == "local:test"
    assert all("id" not in step for step in first["value"]["steps"])


def test_document_verifies_a_language_neutral_golden():
    document = {
        "schema": FIXTURE_SCHEMA,
        "cases": [
            {
                "id": "valid-envelope",
                "operation": "envelope",
                "input": {"raw": "<quorum-final>Hello.</quorum-final>"},
                "expected": {"ok": True, "value": {"answer": "Hello."}},
            },
            {
                "id": "unsafe-envelope",
                "operation": "envelope",
                "input": {"raw": "unstructured private reasoning"},
                "expected": {
                    "ok": False,
                    "error": {
                        "code": "unsafe_output",
                        "message": "Expected exactly one envelope, found 0 open / 0 close tags.",
                    },
                },
            },
        ],
    }

    verify_fixture_document(document)
    evaluated = evaluate_fixture_document(document)
    assert [case["id"] for case in evaluated["cases"]] == [
        "valid-envelope",
        "unsafe-envelope",
    ]


def test_loader_reads_utf8_document(tmp_path):
    path = tmp_path / "golden.json"
    path.write_text(
        json.dumps({"schema": FIXTURE_SCHEMA, "cases": []}),
        encoding="utf-8",
    )

    assert load_fixture_document(path)["schema"] == FIXTURE_SCHEMA


def test_verifier_names_divergent_case():
    document = {
        "schema": FIXTURE_SCHEMA,
        "cases": [
            {
                "id": "drifted",
                "operation": "envelope",
                "input": {"raw": '{"answer":"actual"}'},
                "expected": {"ok": True, "value": {"answer": "stale"}},
            }
        ],
    }

    with pytest.raises(FixtureMismatch, match="drifted"):
        verify_fixture_document(document)


@pytest.mark.parametrize(
    "document, message",
    [
        ({"schema": "quorum.routing.v0", "cases": []}, "document.schema"),
        (
            {
                "schema": FIXTURE_SCHEMA,
                "cases": [
                    {"id": "same", "operation": "policy", "input": {"mode": "private"}},
                    {"id": "same", "operation": "policy", "input": {"mode": "offline"}},
                ],
            },
            "Duplicate fixture case id",
        ),
    ],
)
def test_document_validation_fails_closed(document, message):
    with pytest.raises(FixtureFormatError, match=message):
        evaluate_fixture_document(document)
