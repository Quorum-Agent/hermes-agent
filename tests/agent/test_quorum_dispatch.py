from __future__ import annotations

import pytest

from agent import quorum_dispatch, relay_llm

_REAL_LOAD_SETTINGS = quorum_dispatch._load_settings


@pytest.fixture(autouse=True)
def _reset_quorum_events():
    quorum_dispatch._reset_for_tests()
    yield
    quorum_dispatch._reset_for_tests()


@pytest.mark.parametrize(
    ("provider", "base_url", "expected"),
    [
        ("ollama", "http://127.0.0.1:11434/v1", "local"),
        ("custom", "http://192.168.1.25:8000/v1", "network"),
        ("custom", "http://model-server:8000/v1", "network"),
        ("openai", "https://api.openai.com/v1", "cloud"),
        ("mock", "https://api.openai.com/v1", "cloud"),
        ("in-process", "http://192.168.1.25:8000/v1", "network"),
        ("openai", "", "cloud"),
        ("quorum-scaffold", "", "device"),
    ],
)
def test_model_reach_is_derived_from_physical_boundary(
    provider, base_url, expected
):
    assert quorum_dispatch.classify_model_reach(provider, base_url) == expected


def test_runtime_config_defaults_to_private_without_cloud_consent():
    settings = _REAL_LOAD_SETTINGS()

    assert settings.default_policy == "private"
    assert settings.cloud_consent is False


def test_private_blocks_cloud_before_provider_callback(monkeypatch):
    monkeypatch.setattr(
        quorum_dispatch,
        "_load_settings",
        lambda: quorum_dispatch.DispatchSettings(default_policy="private"),
    )
    monkeypatch.setattr(
        relay_llm.relay_runtime,
        "resolve_execution_context",
        lambda _session_id: (None, None, None),
    )
    called = False

    def provider(_request):
        nonlocal called
        called = True
        return "provider-result"

    with pytest.raises(quorum_dispatch.QuorumPolicyViolation, match="Private"):
        relay_llm.execute(
            {"model": "gpt", "messages": [{"role": "user", "content": "hello"}]},
            provider,
            session_id="session-1",
            name="openai",
            model_name="gpt",
            metadata={"base_url": "https://api.openai.com/v1"},
        )

    assert called is False
    event = quorum_dispatch.list_events()[0]
    assert event["allowed"] is False
    assert event["provider"] == "openai"


def test_balanced_requires_consent_for_off_device_dispatch(monkeypatch):
    request = {"messages": [{"role": "user", "content": "hello"}]}
    without_consent = quorum_dispatch.evaluate_dispatch(
        request,
        provider="openai",
        model="gpt",
        base_url="https://api.openai.com/v1",
        settings=quorum_dispatch.DispatchSettings(
            default_policy="balanced", cloud_consent=False
        ),
    )
    with_consent = quorum_dispatch.evaluate_dispatch(
        request,
        provider="openai",
        model="gpt",
        base_url="https://api.openai.com/v1",
        settings=quorum_dispatch.DispatchSettings(
            default_policy="balanced", cloud_consent=True
        ),
    )

    assert without_consent.allowed is False
    assert "consent" in without_consent.reason.lower()
    assert with_consent.allowed is True


def test_sensitive_payload_stays_local_even_with_cloud_consent(monkeypatch):
    monkeypatch.setattr(
        quorum_dispatch,
        "_sensitive_categories",
        lambda _request: ("credential",),
    )
    decision = quorum_dispatch.evaluate_dispatch(
        {"messages": [{"role": "tool", "content": "sensitive result"}]},
        provider="anthropic",
        model="claude",
        base_url="https://api.anthropic.com",
        settings=quorum_dispatch.DispatchSettings(
            default_policy="quality", cloud_consent=True
        ),
    )

    assert decision.allowed is False
    assert "sensitive" in decision.reason.lower()


def test_private_allows_loopback_provider(monkeypatch):
    monkeypatch.setattr(
        quorum_dispatch,
        "_sensitive_categories",
        lambda _request: (),
    )
    decision = quorum_dispatch.evaluate_dispatch(
        {"messages": [{"role": "user", "content": "hello"}]},
        provider="ollama",
        model="qwen",
        base_url="http://localhost:11434/v1",
        settings=quorum_dispatch.DispatchSettings(default_policy="private"),
    )

    assert decision.allowed is True
    assert decision.reach == "local"


def test_cost_controlled_fails_closed_without_usage_ledger(monkeypatch):
    monkeypatch.setattr(
        quorum_dispatch,
        "_sensitive_categories",
        lambda _request: (),
    )
    decision = quorum_dispatch.evaluate_dispatch(
        {"messages": []},
        provider="quorum-scaffold",
        model="scaffold",
        settings=quorum_dispatch.DispatchSettings(default_policy="cost_controlled"),
    )
    assert decision.allowed is False
    assert "usage ledger" in decision.reason.lower()


def test_tool_policy_blocks_network_capability_but_allows_local(monkeypatch):
    monkeypatch.setattr(
        quorum_dispatch,
        "_load_settings",
        lambda: quorum_dispatch.DispatchSettings(default_policy="private"),
    )

    quorum_dispatch.enforce_tool_dispatch("terminal", session_id="session-1")
    quorum_dispatch.enforce_tool_dispatch("patch", session_id="session-1")
    quorum_dispatch.enforce_tool_dispatch("process", session_id="session-1")
    quorum_dispatch.enforce_tool_dispatch("project_list", session_id="session-1")
    quorum_dispatch.enforce_tool_dispatch("session_search", session_id="session-1")
    with pytest.raises(quorum_dispatch.QuorumPolicyViolation, match="web_search"):
        quorum_dispatch.enforce_tool_dispatch("web_search", session_id="session-1")


def test_network_tool_never_receives_sensitive_arguments(monkeypatch):
    monkeypatch.setattr(
        quorum_dispatch,
        "_load_settings",
        lambda: quorum_dispatch.DispatchSettings(
            default_policy="quality", cloud_consent=True
        ),
    )
    monkeypatch.setattr(
        quorum_dispatch,
        "_sensitive_categories",
        lambda request: ("credentials",) if request.get("args") else (),
    )

    with pytest.raises(quorum_dispatch.QuorumPolicyViolation, match="Sensitive"):
        quorum_dispatch.enforce_tool_dispatch(
            "web_search",
            args={"query": "password=hunter2-example"},
            session_id="session-1",
        )


def test_cost_controlled_tool_dispatch_fails_closed(monkeypatch):
    monkeypatch.setattr(
        quorum_dispatch,
        "_load_settings",
        lambda: quorum_dispatch.DispatchSettings(default_policy="cost_controlled"),
    )
    monkeypatch.setattr(quorum_dispatch, "_sensitive_categories", lambda _request: ())

    with pytest.raises(quorum_dispatch.QuorumPolicyViolation, match="usage ledger"):
        quorum_dispatch.enforce_tool_dispatch("terminal")


def test_inspector_events_never_retain_request_content(monkeypatch):
    secret = "do-not-retain-this-prompt"
    session_id = "private-session-identifier"
    monkeypatch.setattr(
        quorum_dispatch,
        "_load_settings",
        lambda: quorum_dispatch.DispatchSettings(default_policy="private"),
    )
    monkeypatch.setattr(quorum_dispatch, "_sensitive_categories", lambda _request: ())

    quorum_dispatch.enforce_model_request(
        {"messages": [{"role": "user", "content": secret}]},
        provider="ollama",
        model="local",
        base_url="http://127.0.0.1:11434/v1",
        session_id=session_id,
    )

    event_text = repr(quorum_dispatch.list_events())
    assert secret not in event_text
    assert session_id not in event_text
    assert "sha256:" in event_text
    assert "messages" not in event_text


def test_status_is_degraded_when_policy_config_cannot_be_read(monkeypatch):
    monkeypatch.setattr(
        quorum_dispatch,
        "_load_settings",
        lambda: (_ for _ in ()).throw(
            quorum_dispatch.QuorumPolicyUnavailable("broken config")
        ),
    )

    status = quorum_dispatch.get_status()

    assert status["available"] is False
    assert status["health"] == "degraded"
    assert status["reason"] == "config_unavailable"
    assert status["fail_closed"] is True
